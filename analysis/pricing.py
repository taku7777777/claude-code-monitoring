#!/usr/bin/env python3
# =============================================================================
# pricing.py — pricing/pricing.yaml の唯一のローダ / コスト再計算ライブラリ
# =============================================================================
# 価格は best-effort (2026-07 調査値)。利用前に必ず公式 pricing ページと突合すること。
# pricing.yaml が価格 SSOT。本モジュールが唯一のローダ (他コードは直接 YAML をパースしない)。
#
# 提供関数:
#   load_pricing(path)            -> dict           : YAML 読込 (+簡易キャッシュ)
#   price_for(model, at_date)     -> {input,output} : price_changes を発効日で解決
#   recompute_cost(...)           -> USD            : cache 係数・modifier 係数を積算
#   counterfactual_cost(...)      -> USD            : 別モデル換算 (世代跨ぎは token 補正)
#
# CLI: `python3 pricing.py` で自己テストを出力する。
# =============================================================================
from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Any, Dict, Optional, Union

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "[pricing] pyyaml が必要です: pip install pyyaml (analysis/requirements.txt 参照)\n"
    )
    raise

# デフォルトの価格表パス (このファイルからの相対 → pricing/pricing.yaml)
_DEFAULT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pricing", "pricing.yaml")
)

# ロード結果の簡易キャッシュ (path -> parsed dict)
_CACHE: Dict[str, Dict[str, Any]] = {}

DateLike = Union[str, _dt.date, _dt.datetime, None]


def _to_date(value: DateLike) -> Optional[_dt.date]:
    """'YYYY-MM-DD' 文字列 / date / datetime を date に正規化。None は None。"""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        # ISO 形式のみ想定。時刻付きでも先頭 10 文字で日付を取る。
        return _dt.date.fromisoformat(value[:10])
    raise TypeError(f"unsupported date value: {value!r}")


def load_pricing(path: Optional[str] = None, *, reload: bool = False) -> Dict[str, Any]:
    """pricing.yaml を読み込んで dict を返す。path 省略時は既定パス。"""
    p = os.path.abspath(path or _DEFAULT_PATH)
    if not reload and p in _CACHE:
        return _CACHE[p]
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if "models" not in data:
        raise ValueError(f"pricing.yaml に 'models' がありません: {p}")
    _CACHE[p] = data
    return data


def normalize_model_id(model: str) -> str:
    """日付サフィックス付きモデル ID (claude-haiku-4-5-20251001 等) を基底 ID に正規化。"""
    import re
    return re.sub(r"-\d{8}$", "", model)


def get_model(model: str, pricing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """モデル定義 dict を返す。日付サフィックスは剥がして照合。未知モデルは KeyError。"""
    pricing = pricing or load_pricing()
    models = pricing.get("models", {})
    if model in models:
        return models[model]
    base = normalize_model_id(model)
    if base in models:
        return models[base]
    raise KeyError(
        f"未知のモデル '{model}'. pricing.yaml の models に定義がありません。"
        f" 既知: {sorted(models)}"
    )


def price_for(
    model: str,
    at_date: DateLike = None,
    pricing: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """
    指定日時点で有効な {input, output} 価格 (USD / 1M tokens) を返す。
    price_changes があれば at_date 以下で最新の発効日のものを採用。
    at_date 省略時は最新 (=全 price_changes を適用) とみなす。
    """
    pricing = pricing or load_pricing()
    m = get_model(model, pricing)
    inp = float(m["input"])
    out = float(m["output"])

    target = _to_date(at_date)
    changes = m.get("price_changes") or []
    if changes:
        # 発効日昇順に並べ、target 以下 (target=None は全適用) の最後の変更を採用。
        applicable = []
        for ch in changes:
            frm = _to_date(ch.get("from"))
            if target is None or (frm is not None and frm <= target):
                applicable.append((frm, ch))
        applicable.sort(key=lambda t: (t[0] or _dt.date.min))
        if applicable:
            last = applicable[-1][1]
            if "input" in last:
                inp = float(last["input"])
            if "output" in last:
                out = float(last["output"])
    return {"input": inp, "output": out}


def tokenizer_generation(model: str, pricing: Optional[Dict[str, Any]] = None) -> str:
    """モデルの tokenizer 世代 ('new'/'old') を返す。既定 'old'。"""
    m = get_model(model, pricing)
    return str(m.get("tokenizer_generation", "old"))


def recompute_cost(
    model: str,
    input_tokens: float = 0,
    output_tokens: float = 0,
    cache_read_tokens: float = 0,
    cache_creation_tokens: float = 0,
    at_date: DateLike = None,
    *,
    batch: bool = False,
    fast: bool = False,
    geo_us: bool = False,
    cache_write_ttl: Optional[str] = None,
    pricing: Optional[Dict[str, Any]] = None,
) -> float:
    """
    トークン内訳から USD コストを再計算する。

    input_tokens          : キャッシュ未使用の新規入力トークン (input 価格 x1.0)
    cache_read_tokens     : cache read (input 価格 x read_multiplier)
    cache_creation_tokens : cache write (input 価格 x write_{ttl}_multiplier)
    output_tokens         : 出力トークン (output 価格)
    cache_write_ttl       : '5m' | '1h' | None (None は cache.write_default_ttl、既定 '1h')
    batch/fast/geo_us     : modifier 係数を乗算 (積算)。

    モデル定義に long_context がある場合、入力コンテキスト合計
    (input + cache_read + cache_creation) が threshold を超えるリクエストは
    input 系に input_multiplier、output に output_multiplier を乗算する。

    戻り値: USD (= tokens/1e6 * price_per_1M を係数積算した合計)。
    """
    pricing = pricing or load_pricing()
    price = price_for(model, at_date, pricing)
    cache = pricing.get("cache", {})
    mods = pricing.get("modifiers", {})

    if cache_write_ttl is None:
        cache_write_ttl = str(cache.get("write_default_ttl", "1h"))
    read_mult = float(cache.get("read_multiplier", 0.1))
    if cache_write_ttl == "1h":
        write_mult = float(cache.get("write_1h_multiplier", 2.0))
    elif cache_write_ttl == "5m":
        write_mult = float(cache.get("write_5m_multiplier", 1.25))
    else:
        raise ValueError(f"cache_write_ttl は '5m' か '1h': {cache_write_ttl!r}")

    in_price = price["input"]
    out_price = price["output"]

    # 長コンテキスト割増 (>threshold の 1M モデル)。定義がある場合のみ適用。
    lc = get_model(model, pricing).get("long_context")
    if lc:
        ctx = input_tokens + cache_read_tokens + cache_creation_tokens
        if ctx > float(lc.get("threshold", 200_000)):
            in_price *= float(lc.get("input_multiplier", 2.0))
            out_price *= float(lc.get("output_multiplier", 1.5))

    # per 1M tokens 価格なので 1e6 で割る。
    cost = (
        input_tokens * in_price
        + cache_read_tokens * in_price * read_mult
        + cache_creation_tokens * in_price * write_mult
        + output_tokens * out_price
    ) / 1_000_000.0

    # modifier 係数の積算。
    factor = 1.0
    if batch:
        factor *= float(mods.get("batch", 0.5))
    if fast:
        factor *= float(mods.get("fast", 2.0))
    if geo_us:
        factor *= float(mods.get("geo_us", 1.1))

    return cost * factor


def counterfactual_cost(
    from_model: str,
    to_model: str,
    input_tokens: float = 0,
    output_tokens: float = 0,
    cache_read_tokens: float = 0,
    cache_creation_tokens: float = 0,
    at_date: DateLike = None,
    *,
    batch: bool = False,
    fast: bool = False,
    geo_us: bool = False,
    cache_write_ttl: Optional[str] = None,
    pricing: Optional[Dict[str, Any]] = None,
) -> float:
    """
    from_model で観測されたトークン数を to_model で処理した場合の反実仮想コスト (USD)。

    トークナイザ世代が異なる場合、同一テキストのトークン数は世代で変わる (新世代 ≈ +30%)。
    new_vs_old_ratio (=1.3) で補正する:
        old -> new : トークン増 (x ratio)
        new -> old : トークン減 (x 1/ratio)
        同一世代    : 補正なし
    """
    pricing = pricing or load_pricing()
    ratio = float(pricing.get("tokenizer", {}).get("new_vs_old_ratio", 1.3))

    gen_from = tokenizer_generation(from_model, pricing)
    gen_to = tokenizer_generation(to_model, pricing)

    scale = 1.0
    if gen_from != gen_to:
        if gen_from == "old" and gen_to == "new":
            scale = ratio         # old→new: トークン増
        elif gen_from == "new" and gen_to == "old":
            scale = 1.0 / ratio   # new→old: トークン減

    return recompute_cost(
        to_model,
        input_tokens=input_tokens * scale,
        output_tokens=output_tokens * scale,
        cache_read_tokens=cache_read_tokens * scale,
        cache_creation_tokens=cache_creation_tokens * scale,
        at_date=at_date,
        batch=batch,
        fast=fast,
        geo_us=geo_us,
        cache_write_ttl=cache_write_ttl,
        pricing=pricing,
    )


# -----------------------------------------------------------------------------
# CLI 自己テスト
# -----------------------------------------------------------------------------
def _selftest() -> int:
    pricing = load_pricing()
    print(f"pricing.yaml: {_DEFAULT_PATH}")
    print(f"models: {len(pricing.get('models', {}))} 件\n")

    # 既知トークンでの opus-4-8 コスト計算
    model = "claude-opus-4-8"
    inp, out, cr, cc = 10_000, 2_000, 50_000, 5_000
    cost = recompute_cost(
        model, input_tokens=inp, output_tokens=out,
        cache_read_tokens=cr, cache_creation_tokens=cc,
    )
    # 手計算検証 (既定 TTL=1h -> write 2.0x):
    #   (10000*5 + 50000*5*0.1 + 5000*5*2.0 + 2000*25)/1e6
    # = (50000 + 25000 + 50000 + 50000)/1e6 = 175000/1e6 = 0.175
    print(f"[recompute] {model} in={inp} out={out} cr={cr} cc={cc}")
    print(f"           cost = ${cost:.6f}  (期待 ~$0.175000)")
    assert abs(cost - 0.175) < 1e-9, cost

    # TTL 5m 明示指定なら write 1.25x
    cost_5m = recompute_cost(model, input_tokens=inp, output_tokens=out,
                             cache_read_tokens=cr, cache_creation_tokens=cc,
                             cache_write_ttl="5m")
    print(f"[recompute] cache_write_ttl='5m' -> ${cost_5m:.6f}  (期待 ~$0.156250)")
    assert abs(cost_5m - 0.15625) < 1e-9, cost_5m

    # modifier: batch は半額
    cost_batch = recompute_cost(model, input_tokens=inp, output_tokens=out,
                                cache_read_tokens=cr, cache_creation_tokens=cc, batch=True)
    print(f"[modifier] batch=True -> ${cost_batch:.6f}  (期待 半額 ~$0.087500)")
    assert abs(cost_batch - cost * 0.5) < 1e-9

    # 日付サフィックス付きモデル ID の正規化解決
    dated = price_for("claude-haiku-4-5-20251001")
    print(f"[normalize] claude-haiku-4-5-20251001 -> {dated}")
    assert dated == {"input": 1.5, "output": 7.5}, dated

    # sonnet-5 現行価格 (2026-07-12 SDK 含意単価で校正)
    s5 = price_for("claude-sonnet-5")
    print(f"[price_for] sonnet-5 = {s5}")
    assert s5 == {"input": 3.0, "output": 15.0}, s5

    # counterfactual: opus-4-8(new) の負荷を opus-4-6(old) で処理 → token 減 (x 1/1.3)
    cf_new_to_old = counterfactual_cost(
        "claude-opus-4-8", "claude-opus-4-6",
        input_tokens=inp, output_tokens=out,
        cache_read_tokens=cr, cache_creation_tokens=cc,
    )
    print(f"[counterfactual] opus-4-8(new)->opus-4-6(old) = ${cf_new_to_old:.6f}")
    # 同価格 (5/25) だが new->old で token が 1/1.3 に減るのでコストも 1/1.3
    assert abs(cf_new_to_old - cost / 1.3) < 1e-9, cf_new_to_old

    # counterfactual: old->new は token 増 (x1.3)
    cf_old_to_new = counterfactual_cost(
        "claude-opus-4-6", "claude-opus-4-8",
        input_tokens=inp, output_tokens=out,
        cache_read_tokens=cr, cache_creation_tokens=cc,
    )
    print(f"[counterfactual] opus-4-6(old)->opus-4-8(new) = ${cf_old_to_new:.6f}")
    assert abs(cf_old_to_new - cost * 1.3) < 1e-9, cf_old_to_new

    print("\nOK: 全自己テスト通過")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
