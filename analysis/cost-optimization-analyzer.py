#!/usr/bin/env python3
# =============================================================================
# cost-optimization-analyzer.py
#   Loki (http://localhost:3100) からコストイベントを集計し、コスト最適化の
#   「観測 → 診断 → 施策 → 効果検証」サイクル (Inform/Optimize/Operate) を支援する CLI。
# =============================================================================
# 価格情報は best-effort (2026-07)。金額は SDK 推定 cost_usd の集計であり公式請求ではない。
#
# 機能:
#   (a) CPSO   : Cost Per Successful Outcome = 期間コスト / 成功系 task_outcome 数
#                (成功系−失敗系の減算方式。completed/手動 success の重複限界は CONTRACT §3.2)
#   (b) パレート寄与度分解 : prompt_id / work_type 別コストを降順、累積80%到達までを ▶ で
#                            ハイライト（80%を跨ぐ項目を含む）。今期 vs 前期の増分 $ も併記。
#   (c) ベースライン percentile : prompt_id ごとの「期間合計コスト」の分布から p50/p90/p95。
#                (ダッシュボードの「api_request単位コスト分布」= リクエスト1件あたり分位点とは
#                 集計単位が異なる点に注意)
#   (d) 変化点検出 : 日次コスト系列 (1日ビン・ゼロ埋め) に対し、参照区間ベースの
#                    tabular CUSUM（変化開始点を報告）。ruptures があれば PELT も併用。
#   (e) RICE スコア表の雛形 : 寄与度上位を Reach/Impact/Confidence/Effort テンプレで出力。
#   (f) 施策効果検証 : intervention_marker ごとに日次単位コスト (cost/1M 実効トークン) の
#                      before/after を I-MR 管理図 (X̄±2.66·MR̄) で評価し水準シフトを判定。
#   (g) モデル経済性 : モデル別に cost_usd と pricing.yaml 再計算の乖離 (推定誤差)、
#                      反実仮想 (--cf-target への切替でいくら変わるか、トークナイザ世代補正込み)、
#                      キャッシュ純節約額 (read節約 − write割増) を算出。
#   (h) タスク別サマリ : タスク=workspace 前提 (CONTRACT §5.5) の workspace 別
#                        コスト/CPSO/委任率/探索コスト比/rework比率。--archive で CSV 永続化。
#
# オプション: --hours N / --top N / --json / --loki URL / --cf-target MODEL
#             / --archive / --archive-dir DIR
#
# 設計: Loki が無くても import 時にエラーにならない。接続は実行時にのみ行う。
#       Loki クエリ流儀:
#       sum by (prompt_id)(sum_over_time({event_name="api_request"} | unwrap cost_usd [Nh]))
# =============================================================================
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_LOKI = "http://localhost:3100"
DEFAULT_CF_TARGET = "claude-sonnet-5"

DAY = 86400


# -----------------------------------------------------------------------------
# Loki クライアント (requests は実行時に遅延 import)
# -----------------------------------------------------------------------------
class LokiError(RuntimeError):
    pass


class LokiClient:
    def __init__(self, base_url: str = DEFAULT_LOKI, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._requests = None

    def _req(self):
        if self._requests is None:
            try:
                import requests  # 遅延 import: 実行時のみ必要
            except ImportError as e:  # pragma: no cover
                raise LokiError(
                    "requests が必要です: pip install requests (analysis/requirements.txt)"
                ) from e
            self._requests = requests
        return self._requests

    def query_instant(self, logql: str, at: Optional[float] = None) -> List[Dict[str, Any]]:
        """瞬時ベクトルクエリ。result のリスト (各要素 {metric, value:[ts,val]}) を返す。"""
        params = {"query": logql}
        if at is not None:
            params["time"] = f"{at:.0f}"
        return self._get("/loki/api/v1/query", params)

    def query_range(
        self, logql: str, start: float, end: float, step: str = "1h",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """範囲クエリ。metric なら {metric, values}, log selector なら {stream, values}。"""
        params: Dict[str, Any] = {
            "query": logql,
            "start": f"{start:.0f}",
            "end": f"{end:.0f}",
            "step": step,
        }
        if limit is not None:
            params["limit"] = limit
        return self._get("/loki/api/v1/query_range", params)

    def _get(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        requests = self._req()
        url = self.base_url + path
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
        except Exception as e:
            raise LokiError(f"Loki 接続失敗 ({url}): {e}") from e
        if resp.status_code != 200:
            raise LokiError(f"Loki HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if payload.get("status") != "success":
            raise LokiError(f"Loki クエリ失敗: {payload}")
        return payload.get("data", {}).get("result", [])


# -----------------------------------------------------------------------------
# Loki レスポンス整形ヘルパ
# -----------------------------------------------------------------------------
def instant_scalar(result: List[Dict[str, Any]]) -> float:
    """瞬時ベクトルの合算値 (通常 1 series) を float で返す。空なら 0.0。"""
    total = 0.0
    for series in result:
        val = series.get("value")
        if val and len(val) == 2:
            try:
                total += float(val[1])
            except (TypeError, ValueError):
                pass
    return total


def instant_by_label(result: List[Dict[str, Any]], label: str) -> Dict[str, float]:
    """ラベル値 -> 数値 の dict。ラベル欠損は '(none)'。"""
    out: Dict[str, float] = {}
    for series in result:
        key = series.get("metric", {}).get(label, "(none)")
        val = series.get("value")
        if val and len(val) == 2:
            try:
                out[key] = out.get(key, 0.0) + float(val[1])
            except (TypeError, ValueError):
                pass
    return out


def range_series(result: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """metric 範囲クエリを [(ts, val), ...] にして返す。複数 series は時刻で合算。"""
    acc: Dict[float, float] = {}
    for series in result:
        for pair in series.get("values", []):
            if len(pair) != 2:
                continue
            try:
                ts = float(pair[0])
                v = float(pair[1])
            except (TypeError, ValueError):
                continue
            acc[ts] = acc.get(ts, 0.0) + v
    return sorted(acc.items())


def fill_daily_grid(
    series: List[Tuple[float, float]], n_days: int, now: float
) -> List[Tuple[float, float]]:
    """
    1日刻みの評価グリッド (末尾 = now) にゼロ埋め整列する。
    Loki のメトリック範囲クエリはサンプルが無い日を返さないため、
    欠落日を 0.0 で補完しないと CUSUM/PELT が日付ギャップを圧縮した系列で評価してしまう。
    """
    grid = [now - (n_days - 1 - i) * DAY for i in range(n_days)]
    lookup = dict(series)
    out: List[Tuple[float, float]] = []
    for ts in grid:
        # クエリの評価点はグリッドと一致するはずだが、丸め差に備え ±1s で照合
        v = lookup.get(ts)
        if v is None:
            for k, kv in lookup.items():
                if abs(k - ts) <= 1.0:
                    v = kv
                    break
        out.append((ts, v if v is not None else 0.0))
    return out


# -----------------------------------------------------------------------------
# 統計ユーティリティ (numpy 非依存の自前実装。あれば numpy を使う)
# -----------------------------------------------------------------------------
def percentile(values: Sequence[float], q: float) -> float:
    """q パーセンタイル (0-100)。線形補間。空なら 0.0。"""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    try:
        import numpy as _np  # 任意
        return float(_np.percentile(xs, q))
    except ImportError:
        pass
    rank = (q / 100.0) * (len(xs) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return xs[int(rank)]
    frac = rank - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def mean(values: Sequence[float]) -> float:
    xs = [float(v) for v in values]
    return sum(xs) / len(xs) if xs else 0.0


def mean_std(values: Sequence[float]) -> Tuple[float, float]:
    xs = [float(v) for v in values]
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / n
    return mu, math.sqrt(var)


def imr_limits(values: Sequence[float]) -> Optional[Dict[str, float]]:
    """
    個別値管理図 (I-MR): 中心線 X̄、管理限界 X̄ ± 2.66·MR̄ (MR̄ = 隣接差絶対値の平均)。
    2.66 = 3/d2 (d2=1.128, n=2)。データ2点未満は None。
    """
    xs = [float(v) for v in values]
    if len(xs) < 2:
        return None
    mrs = [abs(xs[i] - xs[i - 1]) for i in range(1, len(xs))]
    mrbar = mean(mrs)
    xbar = mean(xs)
    return {
        "center": xbar,
        "ucl": xbar + 2.66 * mrbar,
        "lcl": max(0.0, xbar - 2.66 * mrbar),
        "mrbar": mrbar,
    }


def cusum_changepoints(
    series: Sequence[float],
    threshold_sigma: float = 4.0,
    drift_sigma: float = 0.5,
) -> List[int]:
    """
    参照区間ベースの双方向 tabular CUSUM。**変化開始点 (onset)** の index を返す。

    - μ0/σ0 は「現在セグメント先頭の参照窓 (max(3, m//3) 点)」から推定する。
      全系列から推定するとシフト自体が σ を膨らませて h が上がり、水準シフトが
      原理的に検出不能になる (レビュー指摘 C13)。
    - 報告 index はアラーム発火点ではなく、発火した側の累積量が最後に 0 から
      正に転じた点 = 変化開始の推定点 (レビュー指摘 C14)。
    - アラーム後は変化点からセグメントを切り直して再帰的に次を探す。
    - 参照窓の内部は検出対象外 (トレードオフとして文書化)。5点未満は判定不能で []。
    """
    xs = [float(v) for v in series]
    n = len(xs)
    if n < 5:
        return []
    cps: List[int] = []
    seg_start = 0
    while len(cps) < 20:
        seg = xs[seg_start:]
        m = len(seg)
        if m < 5:
            break
        ref_len = max(3, m // 3)
        mu, sigma = mean_std(seg[:ref_len])
        if sigma == 0.0:
            # 参照区間が完全フラット: 水準の5% (ゼロなら微小値) を σ とみなす
            sigma = abs(mu) * 0.05 or 1e-9
        k = drift_sigma * sigma
        h = threshold_sigma * sigma
        s_hi = s_lo = 0.0
        onset_hi: Optional[int] = None
        onset_lo: Optional[int] = None
        alarm_onset: Optional[int] = None
        for i in range(ref_len, m):
            x = seg[i]
            prev_hi, prev_lo = s_hi, s_lo
            s_hi = max(0.0, s_hi + (x - mu - k))
            s_lo = max(0.0, s_lo + (mu - k - x))
            if s_hi > 0.0 and prev_hi == 0.0:
                onset_hi = i
            if s_lo > 0.0 and prev_lo == 0.0:
                onset_lo = i
            if s_hi > h or s_lo > h:
                alarm_onset = onset_hi if s_hi > h else onset_lo
                if alarm_onset is None:
                    alarm_onset = i
                break
        if alarm_onset is None:
            break
        cp = seg_start + alarm_onset
        cps.append(cp)
        seg_start = cp
    return cps


def pelt_changepoints(series: Sequence[float]) -> Optional[List[int]]:
    """ruptures があれば PELT で変化点を返す。無ければ None。"""
    try:
        import ruptures as rpt  # 任意
        import numpy as _np
    except ImportError:
        return None
    xs = _np.asarray([float(v) for v in series], dtype=float).reshape(-1, 1)
    if len(xs) < 3:
        return []
    try:
        algo = rpt.Pelt(model="rbf").fit(xs)
        bkps = algo.predict(pen=3.0)
        # ruptures は末尾に n を含めるので除く
        return [b for b in bkps if b < len(xs)]
    except Exception:
        return None


# -----------------------------------------------------------------------------
# LogQL ビルダ
# -----------------------------------------------------------------------------
def q_total_cost(hours: int) -> str:
    return f'sum(sum_over_time({{event_name="api_request"}} | unwrap cost_usd [{hours}h]))'


def q_cost_by(label: str, hours: int) -> str:
    return (
        f'sum by ({label}) '
        f'(sum_over_time({{event_name="api_request"}} | unwrap cost_usd [{hours}h]))'
    )


def q_outcome_count(hours: int) -> str:
    # 減算方式 (CONTRACT §3.2 集計規約): 成功系 − 失敗系。手動 failure/abandoned が
    # 並存する自動 completed を相殺する。ダッシュボード CPSO パネルと同一定義。
    return (
        f'(sum(count_over_time({{event_name="task_outcome"}}'
        f' | outcome!~"failure|abandoned" [{hours}h]))'
        f' - (sum(count_over_time({{event_name="task_outcome"}}'
        f' | outcome=~"failure|abandoned" [{hours}h])) or vector(0)))'
    )


def q_daily(field: str) -> str:
    # 1日ビン: step=1d + グリッド整列 (fill_daily_grid) で使う
    return f'sum(sum_over_time({{event_name="api_request"}} | unwrap {field} [1d]))'


def q_tokens_by_model(field: str, hours: int) -> str:
    return (
        f'sum by (model) '
        f'(sum_over_time({{event_name="api_request"}} | unwrap {field} [{hours}h]))'
    )


# -----------------------------------------------------------------------------
# 日次系列 (1日ビン・ゼロ埋め・単位コスト)
# -----------------------------------------------------------------------------
def daily_series(loki: LokiClient, field: str, hours: int, now: float
                 ) -> Tuple[int, List[Tuple[float, float]]]:
    """
    [1d] 窓 × step=1d のタンブリング日次系列。
    評価点は末尾が now、先頭が now-(n_days-1)d となるよう整列 (レビュー指摘 C15)。
    n_days = floor(hours/24)。hours<24 は 1 点。
    """
    n_days = max(1, int(hours * 3600) // DAY)
    if n_days == 1:
        # start == end の query_range は Loki が評価点を返さないため instant で評価する
        val = instant_scalar(loki.query_instant(q_daily(field), at=now))
        return 1, [(now, val)]
    start = now - (n_days - 1) * DAY
    result = loki.query_range(q_daily(field), start=start, end=now, step="86400")
    series = fill_daily_grid(range_series(result), n_days, now)
    return n_days, series


def daily_unit_cost(loki: LokiClient, hours: int, now: float
                    ) -> List[Tuple[float, Optional[float]]]:
    """日次の コスト/1M実効トークン。ET=0 の日は None (稼働なし日)。"""
    _, cost = daily_series(loki, "cost_usd", hours, now)
    _, et = daily_series(loki, "effective_tokens", hours, now)
    et_map = dict(et)
    out: List[Tuple[float, Optional[float]]] = []
    for ts, c in cost:
        e = et_map.get(ts, 0.0)
        out.append((ts, (c / (e / 1_000_000.0)) if e > 0 else None))
    return out


# -----------------------------------------------------------------------------
# 分析ロジック
# -----------------------------------------------------------------------------
def analyze_cpso(loki: LokiClient, hours: int, now: float) -> Dict[str, Any]:
    cost = instant_scalar(loki.query_instant(q_total_cost(hours), at=now))
    outcomes = instant_scalar(loki.query_instant(q_outcome_count(hours), at=now))
    cpso = (cost / outcomes) if outcomes > 0 else None
    return {
        "period_hours": hours,
        "total_cost_usd": round(cost, 6),
        "task_outcomes": int(outcomes),
        "outcome_filter": '成功系(outcome!~"failure|abandoned") − 失敗系(減算相殺)',
        "cpso_usd": round(cpso, 6) if cpso is not None else None,
        "note": "completed(hook) と手動 success は同一タスクで重複し得る (CONTRACT §3.2)",
    }


def analyze_pareto(
    loki: LokiClient, label: str, hours: int, now: float, top: int
) -> Dict[str, Any]:
    """label 別コストを降順、累積80% 到達までを vital few に。今期 vs 前期の増分も算出。"""
    cur = instant_by_label(loki.query_instant(q_cost_by(label, hours), at=now), label)
    prev_at = now - hours * 3600
    prev = instant_by_label(loki.query_instant(q_cost_by(label, hours), at=prev_at), label)

    total = sum(cur.values())
    rows: List[Dict[str, Any]] = []
    cumulative = 0.0
    for key, cost in sorted(cur.items(), key=lambda kv: kv[1], reverse=True):
        prev_cum_share = (cumulative / total) if total > 0 else 0.0
        cumulative += cost
        share = (cost / total) if total > 0 else 0.0
        cum_share = (cumulative / total) if total > 0 else 0.0
        delta = cost - prev.get(key, 0.0)
        rows.append({
            "key": key,
            "cost_usd": round(cost, 6),
            "share": round(share, 4),
            "cum_share": round(cum_share, 4),
            # 80% ラインを跨ぐ項目まで含める (直前累積が 80% 未満なら vital few)
            "in_vital_few": prev_cum_share < 0.80,
            "delta_vs_prev_usd": round(delta, 6),
        })
    return {
        "label": label,
        "total_cost_usd": round(total, 6),
        "rows": rows[:top] if top else rows,
        "all_rows": rows,
    }


def analyze_baseline(loki: LokiClient, hours: int, now: float) -> Dict[str, Any]:
    """prompt_id ごとの期間合計コストの分布 (リクエスト単位の分位点とは別物)。"""
    by_prompt = instant_by_label(
        loki.query_instant(q_cost_by("prompt_id", hours), at=now), "prompt_id"
    )
    costs = list(by_prompt.values())
    return {
        "unit": "prompt_id ごとの期間合計コスト (per-request 分位点とは集計単位が異なる)",
        "n_prompts": len(costs),
        "p50_usd": round(percentile(costs, 50), 6),
        "p90_usd": round(percentile(costs, 90), 6),
        "p95_usd": round(percentile(costs, 95), 6),
        "max_usd": round(max(costs), 6) if costs else 0.0,
    }


def analyze_changepoints(loki: LokiClient, hours: int, now: float) -> Dict[str, Any]:
    n_days, series = daily_series(loki, "cost_usd", hours, now)
    values = [v for _, v in series]
    cusum_idx = cusum_changepoints(values)
    pelt_idx = pelt_changepoints(values)

    def to_dates(idxs: Optional[List[int]]) -> Optional[List[str]]:
        if idxs is None:
            return None
        out = []
        for i in idxs:
            if 0 <= i < len(series):
                ts = series[i][0]
                out.append(time.strftime("%Y-%m-%d", time.localtime(ts)))
        return out

    return {
        "n_days": n_days,
        "daily_cost_usd": [round(v, 6) for v in values],
        "insufficient_points": n_days < 5,
        "cusum_changepoints": to_dates(cusum_idx),
        "pelt_changepoints": to_dates(pelt_idx),  # None = ruptures 未導入
        "note": "変化点は onset (変化開始推定点)。5点未満は判定不能",
    }


# ---- (f) 施策効果検証 --------------------------------------------------------
def fetch_interventions(loki: LokiClient, hours: int, now: float) -> List[Dict[str, Any]]:
    start = now - hours * 3600
    result = loki.query_range(
        '{event_name="intervention_marker"}', start=start, end=now,
        step="1h", limit=1000,
    )
    markers: List[Dict[str, Any]] = []
    for stream in result:
        labels = stream.get("stream", {})
        for ts_ns, _line in stream.get("values", []):
            try:
                ts = int(ts_ns) / 1e9
            except (TypeError, ValueError):
                continue
            markers.append({
                "ts": ts,
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)),
                "intervention_id": labels.get("intervention_id", "?"),
                "category": labels.get("category", "?"),
                "description": labels.get("description", ""),
                "scope": labels.get("scope", ""),
            })
    markers.sort(key=lambda m: m["ts"])
    return markers


def before_after(
    series: List[Tuple[float, Optional[float]]],
    marker_ts: float,
    max_before: int = 7,
    min_points: int = 2,
) -> Dict[str, Any]:
    """(ts, val) 日次系列を marker_ts で分割し、I-MR 管理図で水準シフトを評価する。"""
    pts = [(t, v) for t, v in series if v is not None]
    before = [v for t, v in pts if t < marker_ts][-max_before:]
    after = [v for t, v in pts if t >= marker_ts]
    if len(before) < min_points or len(after) < 1:
        return {"evaluable": False, "n_before": len(before), "n_after": len(after),
                "note": f"before {min_points} 点以上 / after 1 点以上が必要"}
    mb, ma = mean(before), mean(after)
    shift = ma - mb
    pct = (shift / mb * 100.0) if mb else None
    limits = imr_limits(before)
    violations: List[float] = []
    if limits:
        violations = [round(v, 6) for v in after
                      if v > limits["ucl"] or v < limits["lcl"]]
    return {
        "evaluable": True,
        "n_before": len(before), "n_after": len(after),
        "mean_before": round(mb, 6), "mean_after": round(ma, 6),
        "shift": round(shift, 6),
        "shift_pct": round(pct, 2) if pct is not None else None,
        "imr": {k: round(v, 6) for k, v in limits.items()} if limits else None,
        "after_outside_limits": violations,
        # 有意 = 管理限界外の after 点が存在し、かつシフト率 >10%
        "significant": bool(violations) and abs(pct or 0.0) > 10.0,
    }


def analyze_interventions(loki: LokiClient, hours: int, now: float) -> Dict[str, Any]:
    markers = fetch_interventions(loki, hours, now)
    unit = daily_unit_cost(loki, hours, now)
    results = []
    for m in markers:
        ev = before_after(unit, m["ts"])
        results.append({**m, "unit_cost_effect": ev})
    return {
        "metric": "日次 コスト/1M実効トークン (1日ビン)",
        "method": "before/after 平均 + I-MR 管理図 (X̄±2.66·MR̄, before区間から算出)",
        "n_markers": len(markers),
        "results": results,
        "note": ("有意判定 = after に管理限界外の点があり、かつ水準シフト >10%。"
                 "novelty効果を避けるため施策直後数日での確定判断は避けること"),
    }


# ---- (g) モデル経済性 (推定誤差 / 反実仮想 / キャッシュ純節約) -----------------
def analyze_model_economics(
    loki: LokiClient, hours: int, now: float, cf_target: str
) -> Dict[str, Any]:
    try:
        import pricing as P
    except ImportError as e:
        return {"available": False,
                "note": f"pricing モジュールを import できません: {e}"}
    try:
        pr = P.load_pricing()
    except Exception as e:  # pyyaml 欠如や YAML 破損
        return {"available": False, "note": f"pricing.yaml をロードできません: {e}"}

    fields = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"]
    by_model: Dict[str, Dict[str, float]] = {}
    for f in fields:
        for model, v in instant_by_label(
            loki.query_instant(q_tokens_by_model(f, hours), at=now), "model"
        ).items():
            by_model.setdefault(model, {})[f] = v
    cost_by_model = instant_by_label(
        loki.query_instant(q_cost_by("model", hours), at=now), "model"
    )

    at_date = time.strftime("%Y-%m-%d", time.localtime(now))
    cache = pr.get("cache", {})
    read_mult = float(cache.get("read_multiplier", 0.1))
    write_mult = float(cache.get("write_5m_multiplier", 1.25))

    rows = []
    totals = {"observed": 0.0, "recomputed": 0.0, "cf": 0.0, "cache_net": 0.0}
    for model, toks in sorted(by_model.items()):
        observed = cost_by_model.get(model, 0.0)
        row: Dict[str, Any] = {"model": model, "observed_usd": round(observed, 6),
                               **{k: int(v) for k, v in toks.items()}}
        try:
            kw = dict(
                input_tokens=toks.get("input_tokens", 0.0),
                output_tokens=toks.get("output_tokens", 0.0),
                cache_read_tokens=toks.get("cache_read_tokens", 0.0),
                cache_creation_tokens=toks.get("cache_creation_tokens", 0.0),
                at_date=at_date, pricing=pr,
            )
            recomputed = P.recompute_cost(model, **kw)
            row["recomputed_usd"] = round(recomputed, 6)
            row["drift_usd"] = round(observed - recomputed, 6)
            # 反実仮想: cf_target で処理した場合 (トークナイザ世代補正込み)
            if model != cf_target:
                cf = P.counterfactual_cost(model, cf_target, **kw)
                row["cf_target"] = cf_target
                row["cf_usd"] = round(cf, 6)
                row["cf_saving_usd"] = round(recomputed - cf, 6)
                totals["cf"] += cf
            else:
                totals["cf"] += recomputed
            # キャッシュ純節約額 = read節約 − write割増 (input 単価基準)
            in_price = P.price_for(model, at_date, pr)["input"]
            read_savings = toks.get("cache_read_tokens", 0.0) * in_price * (1 - read_mult) / 1e6
            write_overhead = toks.get("cache_creation_tokens", 0.0) * in_price * (write_mult - 1) / 1e6
            row["cache_read_savings_usd"] = round(read_savings, 6)
            row["cache_write_overhead_usd"] = round(write_overhead, 6)
            row["cache_net_savings_usd"] = round(read_savings - write_overhead, 6)
            totals["observed"] += observed
            totals["recomputed"] += recomputed
            totals["cache_net"] += read_savings - write_overhead
        except KeyError:
            row["note"] = "pricing.yaml に未定義のモデル (skip)"
        rows.append(row)

    return {
        "available": True,
        "cf_target": cf_target,
        "at_date": at_date,
        "rows": rows,
        "totals": {k: round(v, 6) for k, v in totals.items()},
        "note": ("cf_saving は『全リクエストを cf_target で処理した場合』の理論値。"
                 "品質・完了率の変化 (CPSO) は含まない。世代跨ぎはトークナイザ補正 (±30%) 済み。"
                 "価格は best-effort、公式 pricing と要突合"),
    }


def _instant_by_two_labels(
    result: List[Dict[str, Any]], l1: str, l2: str
) -> Dict[Tuple[str, str], float]:
    """(label1値, label2値) -> 数値。ラベル欠損は '(none)'。"""
    out: Dict[Tuple[str, str], float] = {}
    for series in result:
        m = series.get("metric", {})
        key = (m.get(l1, "(none)"), m.get(l2, "(none)"))
        val = series.get("value")
        if val and len(val) == 2:
            try:
                out[key] = out.get(key, 0.0) + float(val[1])
            except (TypeError, ValueError):
                pass
    return out


def analyze_tasks(loki: LokiClient, hours: int, now: float) -> Dict[str, Any]:
    """[h] タスク(=workspace)別レポート (docs/USECASE.md Phase 2)。

    タスク=workspace 運用 (CONTRACT §5.5) を前提に、workspace ごとの
    コスト / 実効トークン / CPSO / 委任率 / 探索コスト比 / 再編集(手戻り proxy) を出す。
    - 探索コスト比: 編集ツール (Edit/Write 等) を一度も使わなかった prompt のコスト比率。
      読み込み・調査系に費やした割合の近似。
    - rework(再編集)比率: lines_changed が 2 回以上発生した file の割合。
      「一度書いて直した」の粗い proxy (prompt 跨ぎ情報は lines_changed に無いため回数基準)。
    """
    h = f"[{hours}h]"
    base = f'{{event_name="api_request"}}'
    cost_ws_wt = _instant_by_two_labels(
        loki.query_instant(
            f'sum by (workspace, work_type) (sum_over_time({base} | unwrap cost_usd {h}))', at=now),
        "workspace", "work_type")
    et_ws = instant_by_label(
        loki.query_instant(
            f'sum by (workspace) (sum_over_time({base} | unwrap effective_tokens {h}))', at=now),
        "workspace")
    out_ws = instant_by_label(
        loki.query_instant(
            f'(sum by (workspace) (count_over_time({{event_name="task_outcome"}} '
            f'| outcome!~"failure|abandoned" {h})) - (sum by (workspace) '
            f'(count_over_time({{event_name="task_outcome"}} | outcome=~"failure|abandoned" {h})) '
            f'or (sum by (workspace) (count_over_time({{event_name="task_outcome"}} '
            f'| outcome!~"failure|abandoned" {h})) * 0)))', at=now),
        "workspace")
    prompts_ws = instant_by_label(
        loki.query_instant(
            f'sum by (workspace) (count_over_time({{event_name="user_prompt"}} {h}))', at=now),
        "workspace")
    agent_ws = instant_by_label(
        loki.query_instant(
            f'sum by (workspace) (sum_over_time({{event_name="api_request", '
            f'query_source=~"agent:.*"}} | unwrap cost_usd {h}))', at=now),
        "workspace")
    # 探索コスト比: prompt別コスト と 編集ありprompt集合 の突合
    cost_ws_pid = _instant_by_two_labels(
        loki.query_instant(
            f'sum by (workspace, prompt_id) (sum_over_time({base} | unwrap cost_usd {h}))', at=now),
        "workspace", "prompt_id")
    edit_pids = set(_instant_by_two_labels(
        loki.query_instant(
            f'sum by (workspace, prompt_id) (count_over_time({{event_name="tool_decision"}} '
            f'| tool_name=~"Edit|Write|MultiEdit|NotebookEdit" {h}))', at=now),
        "workspace", "prompt_id").keys())
    explore_cost: Dict[str, float] = {}
    for (ws, pid), c in cost_ws_pid.items():
        if (ws, pid) not in edit_pids:
            explore_cost[ws] = explore_cost.get(ws, 0.0) + c
    # rework proxy: file別 lines_changed 回数
    edits_ws_file = _instant_by_two_labels(
        loki.query_instant(
            f'sum by (workspace, file_path) (count_over_time({{event_name="lines_changed"}} {h}))',
            at=now),
        "workspace", "file_path")
    files_total: Dict[str, int] = {}
    files_reworked: Dict[str, int] = {}
    for (ws, _fp), cnt in edits_ws_file.items():
        files_total[ws] = files_total.get(ws, 0) + 1
        if cnt >= 2:
            files_reworked[ws] = files_reworked.get(ws, 0) + 1

    rows = []
    for (ws, wt), cost in sorted(cost_ws_wt.items(), key=lambda kv: -kv[1]):
        outcomes = out_ws.get(ws, 0.0)
        agent_c = agent_ws.get(ws, 0.0)
        ftotal = files_total.get(ws, 0)
        rows.append({
            "workspace": ws,
            "work_type": wt,
            "cost_usd": cost,
            "effective_tokens": et_ws.get(ws, 0.0),
            "outcomes": int(outcomes),
            "cpso_usd": (cost / outcomes) if outcomes else None,
            "prompts": int(prompts_ws.get(ws, 0.0)),
            "delegation_rate": (agent_c / cost) if cost else None,
            "explore_cost_share": (explore_cost.get(ws, 0.0) / cost) if cost else None,
            "rework_ratio": (files_reworked.get(ws, 0) / ftotal) if ftotal else None,
            "files_edited": ftotal,
        })
    return {
        "period_hours": hours,
        "rows": rows,
        "note": ("タスク=workspace 前提。outcomes は Stop 到達 completed を含む弱い proxy。"
                 "探索コスト比=編集なし prompt のコスト比率 / rework=同一 file への複数回編集の比率 (proxy)。"),
    }


def write_task_archive(tasks: Dict[str, Any], out_dir: str, now: float) -> str:
    """[h] のタスク別サマリを CSV に永続化する (Loki retention 90d を超える長期比較用)。"""
    import csv
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d", time.localtime(now))
    path = os.path.join(out_dir, f"tasks-{stamp}-{tasks['period_hours']}h.csv")
    cols = ["workspace", "work_type", "cost_usd", "effective_tokens", "outcomes",
            "cpso_usd", "prompts", "delegation_rate", "explore_cost_share",
            "rework_ratio", "files_edited"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["generated_at", "period_hours"] + cols)
        w.writeheader()
        gen = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
        for r in tasks["rows"]:
            row = {"generated_at": gen, "period_hours": tasks["period_hours"]}
            row.update({c: r.get(c) for c in cols})
            w.writerow(row)
    return path


def build_rice_template(pareto: Dict[str, Any], top: int) -> List[Dict[str, Any]]:
    """
    寄与度上位から RICE スコア表の雛形を作る。
    Reach/Confidence/Effort/assumed_reduction は手入力想定の仮値。
    Impact = 現状コスト × 想定削減率。RICE = Reach * Impact * Confidence / Effort。
    """
    rows = pareto.get("all_rows", [])[: (top or 5)]
    template: List[Dict[str, Any]] = []
    for r in rows:
        assumed_reduction = 0.30  # 手入力想定
        confidence = 0.5          # 手入力想定
        effort = 1.0              # 手入力想定 (人日)
        reach = 1                 # 手入力想定 (件数/影響範囲)
        impact = round(r["cost_usd"] * assumed_reduction, 6)
        rice = round(reach * impact * confidence / effort, 6) if effort else None
        template.append({
            "target": r["key"],
            "current_cost_usd": r["cost_usd"],
            "reach_TODO": reach,
            "assumed_reduction_TODO": assumed_reduction,
            "impact_usd": impact,
            "confidence_TODO": confidence,
            "effort_days_TODO": effort,
            "rice_score": rice,
        })
    return template


# -----------------------------------------------------------------------------
# テキスト出力
# -----------------------------------------------------------------------------
def _fmt_usd(v: Optional[float]) -> str:
    return "-" if v is None else f"${v:,.4f}"


def print_report(report: Dict[str, Any]) -> None:
    cpso = report["cpso"]
    print("=" * 70)
    print(f"Cost Optimization Analyzer  (period = {cpso['period_hours']}h)")
    print("  金額は SDK 推定 cost_usd の集計。公式請求ではない (best-effort 2026-07)。")
    print("=" * 70)

    print("\n[a] CPSO — Cost Per Successful Outcome")
    print(f"    期間コスト      : {_fmt_usd(cpso['total_cost_usd'])}")
    print(f"    成功系 outcome  : {cpso['task_outcomes']}  ({cpso['outcome_filter']})")
    print(f"    CPSO            : {_fmt_usd(cpso['cpso_usd'])} / outcome")
    print(f"    注記            : {cpso['note']}")

    for pareto in (report["pareto_prompt"], report["pareto_work_type"]):
        print(f"\n[b] パレート寄与度分解 — {pareto['label']} 別 "
              f"(total {_fmt_usd(pareto['total_cost_usd'])})")
        print(f"    {'▶':2} {'key':<28} {'cost':>11} {'share':>7} {'cum%':>7} {'Δ vs prev':>11}")
        for r in pareto["rows"]:
            mark = "▶" if r["in_vital_few"] else " "
            print(f"    {mark:2} {str(r['key'])[:28]:<28} "
                  f"{_fmt_usd(r['cost_usd']):>11} "
                  f"{r['share']*100:>6.1f}% {r['cum_share']*100:>6.1f}% "
                  f"{_fmt_usd(r['delta_vs_prev_usd']):>11}")

    base = report["baseline"]
    print("\n[c] ベースライン percentile (prompt_id ごとの期間合計コストの分布)")
    print(f"    n={base['n_prompts']}  "
          f"p50={_fmt_usd(base['p50_usd'])}  "
          f"p90={_fmt_usd(base['p90_usd'])}  "
          f"p95={_fmt_usd(base['p95_usd'])}  "
          f"max={_fmt_usd(base['max_usd'])}")

    cp = report["changepoints"]
    print("\n[d] 変化点検出 (日次コスト系列・1日ビン・ゼロ埋め)")
    print(f"    days={cp['n_days']}  daily={[round(v,2) for v in cp['daily_cost_usd']]}")
    if cp["insufficient_points"]:
        print("    ※ 5点未満のため CUSUM は判定不能 (--hours を増やすこと)")
    print(f"    CUSUM (onset) : {cp['cusum_changepoints'] or '検出なし'}")
    pelt = cp["pelt_changepoints"]
    print(f"    PELT          : {'ruptures 未導入 (任意)' if pelt is None else (pelt or '検出なし')}")

    print("\n[e] RICE スコア雛形 (TODO は手入力想定)")
    print(f"    {'target':<28} {'cur$':>9} {'impact$':>9} {'RICE':>8}")
    for r in report["rice"]:
        print(f"    {str(r['target'])[:28]:<28} "
              f"{_fmt_usd(r['current_cost_usd']):>9} "
              f"{_fmt_usd(r['impact_usd']):>9} "
              f"{(r['rice_score'] if r['rice_score'] is not None else '-'):>8}")
    print("    (Reach/Confidence/Effort/assumed_reduction は仮値。実測で更新すること)")

    iv = report["interventions"]
    print(f"\n[f] 施策効果検証 — {iv['metric']}")
    print(f"    手法: {iv['method']}")
    if iv["n_markers"] == 0:
        print("    施策マーカーなし (scripts/intervention-marker.sh で記録すると前後比較できる)")
    for r in iv["results"]:
        ev = r["unit_cost_effect"]
        head = f"    [{r['date']}] {r['category']}: {r['description'][:40]}"
        if not ev["evaluable"]:
            print(f"{head}\n        評価不能 (before={ev['n_before']}, after={ev['n_after']}) — {ev.get('note','')}")
            continue
        sig = "✔ 有意" if ev["significant"] else "− 有意でない"
        print(f"{head}")
        print(f"        before平均={ev['mean_before']:.4f} after平均={ev['mean_after']:.4f} "
              f"シフト={ev['shift_pct']:+.1f}%  {sig}")
        if ev["imr"]:
            print(f"        I-MR: center={ev['imr']['center']:.4f} "
                  f"UCL={ev['imr']['ucl']:.4f} LCL={ev['imr']['lcl']:.4f} "
                  f"限界外after点={ev['after_outside_limits'] or 'なし'}")
    print(f"    注記: {iv['note']}")

    me = report["model_economics"]
    print("\n[g] モデル経済性 (推定誤差 / 反実仮想 / キャッシュ純節約)")
    if not me.get("available"):
        print(f"    利用不可: {me.get('note')}")
    else:
        print(f"    反実仮想ターゲット: {me['cf_target']}  (単価適用日: {me['at_date']})")
        print(f"    {'model':<22} {'観測$':>9} {'再計算$':>9} {'乖離$':>8} "
              f"{'cf$':>9} {'cf節約$':>9} {'cache純節約$':>11}")
        for r in me["rows"]:
            if "note" in r:
                print(f"    {r['model']:<22} {_fmt_usd(r['observed_usd']):>9}  ({r['note']})")
                continue
            print(f"    {r['model']:<22} "
                  f"{_fmt_usd(r['observed_usd']):>9} "
                  f"{_fmt_usd(r['recomputed_usd']):>9} "
                  f"{_fmt_usd(r['drift_usd']):>8} "
                  f"{_fmt_usd(r.get('cf_usd')):>9} "
                  f"{_fmt_usd(r.get('cf_saving_usd')):>9} "
                  f"{_fmt_usd(r['cache_net_savings_usd']):>11}")
        t = me["totals"]
        print(f"    {'合計':<22} {_fmt_usd(t['observed']):>9} {_fmt_usd(t['recomputed']):>9} "
              f"{'':>8} {_fmt_usd(t['cf']):>9} "
              f"{_fmt_usd(t['recomputed'] - t['cf']):>9} {_fmt_usd(t['cache_net']):>11}")
        print(f"    注記: {me['note']}")

    tasks = report["tasks"]
    print("\n[h] タスク (workspace) 別サマリ")
    print(f"    {'workspace':<28} {'type':<9} {'cost$':>9} {'CPSO$':>8} "
          f"{'prompt':>6} {'委任%':>6} {'探索%':>6} {'手戻り%':>7}")
    for r in tasks["rows"]:
        pct = lambda v: "-" if v is None else f"{v*100:.0f}%"
        cpso = "-" if r["cpso_usd"] is None else f"{r['cpso_usd']:.2f}"
        print(f"    {r['workspace'][:28]:<28} {r['work_type'][:9]:<9} "
              f"{r['cost_usd']:>9.2f} {cpso:>8} {r['prompts']:>6} "
              f"{pct(r['delegation_rate']):>6} {pct(r['explore_cost_share']):>6} "
              f"{pct(r['rework_ratio']):>7}")
    print(f"    注記: {tasks['note']}")
    print()


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
def build_report(loki: LokiClient, hours: int, top: int, cf_target: str) -> Dict[str, Any]:
    now = time.time()
    pareto_prompt = analyze_pareto(loki, "prompt_id", hours, now, top)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "cpso": analyze_cpso(loki, hours, now),
        "pareto_prompt": pareto_prompt,
        "pareto_work_type": analyze_pareto(loki, "work_type", hours, now, top),
        "baseline": analyze_baseline(loki, hours, now),
        "changepoints": analyze_changepoints(loki, hours, now),
        "rice": build_rice_template(pareto_prompt, top),
        "interventions": analyze_interventions(loki, hours, now),
        "model_economics": analyze_model_economics(loki, hours, now, cf_target),
        "tasks": analyze_tasks(loki, hours, now),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Loki コスト最適化アナライザ (best-effort 価格, 2026-07)"
    )
    ap.add_argument("--hours", type=int, default=168,
                    help="集計期間 (時間)。既定 168h = 7d")
    ap.add_argument("--top", type=int, default=10,
                    help="パレート/RICE の表示件数。既定 10")
    ap.add_argument("--json", action="store_true", help="JSON で出力")
    ap.add_argument("--loki", default=DEFAULT_LOKI,
                    help=f"Loki ベース URL。既定 {DEFAULT_LOKI}")
    ap.add_argument("--cf-target", default=DEFAULT_CF_TARGET,
                    help=f"反実仮想の換算先モデル。既定 {DEFAULT_CF_TARGET}")
    ap.add_argument("--archive", action="store_true",
                    help="[h] タスク別サマリを CSV に永続化 (retention 超えの長期比較用)")
    ap.add_argument("--archive-dir",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive"),
                    help="アーカイブ出力先。既定 analysis/archive/")
    args = ap.parse_args(argv)

    loki = LokiClient(args.loki)
    try:
        report = build_report(loki, args.hours, args.top, args.cf_target)
    except LokiError as e:
        sys.stderr.write(f"[error] {e}\n")
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    if args.archive:
        path = write_task_archive(report["tasks"], args.archive_dir, time.time())
        sys.stderr.write(f"[archive] タスク別サマリを書き出しました: {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
