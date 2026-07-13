#!/usr/bin/env python3
"""prompt_summary_tailer — prompt 単位のサマリイベントを last-wins 追記する常駐プロセス。

目的 (CONTRACT §3.4):
  「実行期間が時間窓に重なる prompt を、真の全体期間・総コストで一覧する」ため、
  api_request を定期的に集計して prompt_summary イベントを Loki へ追記する。
  Loki は追記専用のため「上書き」はできない — 代わりに更新版を追記し、
  読み取り側が max 集計で最新値を採用する（end/コスト/tok は単調増加なので
  max = 最新と等価。単調でない比率系は生の分子分母を送る）。

設計:
  - イベントの timeUnixNano は **prompt の開始時刻 (start)** に固定する。
    これにより「窓の終端までのレンジ + start_ms/end_ms の数値メタデータ比較」で
    重なり判定ができる（開始が窓終端以前 ∧ 終了が窓開始以降）。
  - 発火イベント（Stop hook 等）に依存しない: api_request の実在だけを根拠に
    集計するため、中断・放置された prompt も漏れない。
  - tick ごとに「直近に活動のあった prompt」だけを再集計して追記する。
    活動が止まれば自然に最終版が残る。

環境変数:
  LOKI_URL   (default http://loki:3100)
  OTLP_URL   (default http://otel-collector:4318/v1/logs)
  INTERVAL   tick 間隔秒 (default 30)
  LIFE_HOURS prompt の最大寿命 = 全量集計の遡り幅 (default 48)
  COLD_HOURS 状態ファイル欠損時（初回/再起動後）の取りこぼし回収幅 (default 12)
  STATE_FILE (default /tmp/prompt_summary_tailer.state)
  ONCE=1     1 tick だけ実行して終了（手動検証用）
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

LOKI = os.environ.get("LOKI_URL", "http://loki:3100")
OTLP = os.environ.get("OTLP_URL", "http://otel-collector:4318/v1/logs")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
LIFE_S = int(float(os.environ.get("LIFE_HOURS", "48")) * 3600)
COLD_S = int(float(os.environ.get("COLD_HOURS", "12")) * 3600)
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/prompt_summary_tailer.state")
ONCE = os.environ.get("ONCE") == "1"

COST_PIPE = ('| label_format cost_v="{{ if .cost_recalc }}{{ .cost_recalc }}'
             '{{ else }}{{ .cost_usd }}{{ end }}" | unwrap cost_v')


def http_json(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def loki_instant(query, at_s):
    q = urllib.parse.urlencode({"query": query, "time": f"{at_s}000000000"})
    return http_json(f"{LOKI}/loki/api/v1/query?{q}")


def loki_range(query, start_s, end_s, limit, direction):
    q = urllib.parse.urlencode({
        "query": query, "start": f"{start_s}000000000", "end": f"{end_s}000000000",
        "limit": limit, "direction": direction,
    })
    return http_json(f"{LOKI}/loki/api/v1/query_range?{q}")


def scalar(resp, default=0.0):
    res = resp.get("data", {}).get("result", [])
    if not res:
        return default
    return float(res[0]["value"][1])


def first_entry(resp):
    """query_range 応答から (ts_ns:int, stream_labels:dict, line) を1件返す。"""
    for stream in resp.get("data", {}).get("result", []):
        for ts, line in stream.get("values", []):
            return int(ts), stream.get("stream", {}), line
    return None, {}, None


def active_prompt_ids(now_s, since_s):
    rng = max(1, now_s - since_s)
    resp = loki_instant(
        f'sum by (prompt_id) (count_over_time({{event_name="api_request"}} [{rng}s]))', now_s)
    ids = []
    for r in resp.get("data", {}).get("result", []):
        pid = r.get("metric", {}).get("prompt_id")
        if pid:
            ids.append(pid)
    return ids


def summarize(pid, now_s):
    life0 = now_s - LIFE_S
    esc = pid.replace('"', '')
    base = f'{{event_name="api_request"}} | prompt_id="{esc}"'
    cost = scalar(loki_instant(f'sum(sum_over_time({base} {COST_PIPE} [{LIFE_S}s]))', now_s))
    eff = scalar(loki_instant(
        f'sum(sum_over_time({base} | unwrap effective_tokens [{LIFE_S}s]))', now_s))
    reqs = scalar(loki_instant(f'sum(count_over_time({base} [{LIFE_S}s]))', now_s))
    main = f'{{event_name="api_request", query_source="repl_main_thread"}} | prompt_id="{esc}"'
    creation = scalar(loki_instant(
        f'sum(sum_over_time({main} | unwrap cache_creation_tokens [{LIFE_S}s]))', now_s))
    output = scalar(loki_instant(
        f'sum(sum_over_time({main} | unwrap output_tokens [{LIFE_S}s]))', now_s))

    first_ns, labels, _ = first_entry(loki_range(base, life0, now_s, 1, "forward"))
    last_ns, last_labels, _ = first_entry(loki_range(base, life0, now_s, 1, "backward"))
    if first_ns is None:
        return None
    labels = {**labels, **last_labels}

    up_ns, up_labels, _ = first_entry(loki_range(
        f'{{event_name="user_prompt"}} | prompt_id="{esc}"', life0, now_s, 1, "forward"))
    start_ns = min(first_ns, up_ns) if up_ns else first_ns
    text = (up_labels.get("prompt") or "")[:300]

    return {
        "prompt_id": pid,
        "start_ms": start_ns // 1_000_000,
        "end_ms": last_ns // 1_000_000,
        "cost_total": round(cost, 6),
        "eff_total": round(eff, 1),
        "requests": int(reqs),
        "creation_main": int(creation),
        "output_main": int(output),
        "workspace": labels.get("workspace", "(unset)"),
        "work_type": labels.get("work_type", "(unset)"),
        "session_id": labels.get("session_id", ""),
        "prompt": text,
        "_start_ns": start_ns,
    }


def emit(s):
    attrs = [{"key": "event.name", "value": {"stringValue": "prompt_summary"}}]
    for k in ("prompt_id", "start_ms", "end_ms", "cost_total", "eff_total",
              "requests", "creation_main", "output_main", "session_id", "prompt"):
        attrs.append({"key": k, "value": {"stringValue": str(s[k])}})
    payload = {"resourceLogs": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "claude-code-prompt-summary"}},
            {"key": "workspace", "value": {"stringValue": s["workspace"]}},
            {"key": "work_type", "value": {"stringValue": s["work_type"]}},
        ]},
        "scopeLogs": [{
            "scope": {"name": "claude_code_prompt_summary", "version": "0.1.0"},
            "logRecords": [{
                "timeUnixNano": str(s["_start_ns"]),
                "observedTimeUnixNano": str(time.time_ns()),
                "severityNumber": 9, "severityText": "INFO",
                "body": {"stringValue": "prompt_summary"},
                "attributes": attrs,
            }],
        }],
    }]}
    http_json(OTLP, json.dumps(payload).encode())


def tick():
    now_s = int(time.time())
    since = now_s - COLD_S
    try:
        with open(STATE_FILE) as f:
            last = int(f.read().strip())
            since = max(now_s - COLD_S, last - 2 * INTERVAL)
    except (OSError, ValueError):
        pass
    n = 0
    for pid in active_prompt_ids(now_s, since):
        try:
            s = summarize(pid, now_s)
            if s:
                emit(s)
                n += 1
        except Exception as e:  # 1 prompt の失敗で tick 全体を落とさない
            print(f"warn: {pid}: {e}", file=sys.stderr, flush=True)
    with open(STATE_FILE, "w") as f:
        f.write(str(now_s))
    print(f"tick: {n} summaries", flush=True)


def main():
    while True:
        try:
            tick()
        except Exception as e:
            print(f"error: tick failed: {e}", file=sys.stderr, flush=True)
        if ONCE:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
