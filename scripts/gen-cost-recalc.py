#!/usr/bin/env python3
# =============================================================================
# gen-cost-recalc.py — pricing.yaml から collector の cost_recalc 計算式を生成
# =============================================================================
# pricing/pricing.yaml (SSOT) を analysis/pricing.py 経由で読み、api_request ログに
# cost_recalc (単価表×実トークンの再計算コスト USD) を付与する OTTL statements を
# otel-collector-config.yml の BEGIN/END マーカー間に書き込む。
#
#   使い方: python3 scripts/gen-cost-recalc.py            # 生成して書き込み
#           python3 scripts/gen-cost-recalc.py --check    # 差分があれば exit 1 (生成漏れ検知)
#
# pricing.yaml を変更したら本スクリプトを再実行し、collector を再起動すること:
#   python3 scripts/gen-cost-recalc.py && docker compose restart otel-collector
#
# 設計 (ADR 0006):
# - 価格はログ受信時点で焼き込まれる (query 時再計算ではない)。過去分は不変。
# - 未知モデルは cost_usd (SDK推定) にフォールバックし cost_recalc_src="sdk_fallback"
#   を付ける (合計が欠けて過小表示になる事故を防ぐ)。定義済みは "pricing"。
# - 生成時点の有効価格 (price_changes 解決済み) を使う。発効日を跨ぐ改定は
#   発効日に再生成が必要 (RUNBOOK §5 の月次確認に含める)。
# =============================================================================
import datetime
import os
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "analysis"))
import pricing as pr  # noqa: E402

CONFIG = os.path.join(REPO, "otel-collector-config.yml")
BEGIN = "      # BEGIN GENERATED cost_recalc"
END = "      # END GENERATED cost_recalc"

TOKEN_GUARD = (
    'log.attributes["input_tokens"] != nil and log.attributes["output_tokens"] != nil '
    'and log.attributes["cache_read_tokens"] != nil and log.attributes["cache_creation_tokens"] != nil'
)


def fmt(x: float) -> str:
    """OTTL 用の数値リテラル (指数表記を避ける)。"""
    return f"{x:.10f}".rstrip("0").rstrip(".")


def cost_expr(in_p: float, out_p: float, read_m: float, write_m: float) -> str:
    return (
        f'(Double(log.attributes["input_tokens"]) * {fmt(in_p)}'
        f' + Double(log.attributes["cache_read_tokens"]) * {fmt(in_p * read_m)}'
        f' + Double(log.attributes["cache_creation_tokens"]) * {fmt(in_p * write_m)}'
        f' + Double(log.attributes["output_tokens"]) * {fmt(out_p)}) / 1000000.0'
    )


def build_statements(today: datetime.date) -> list:
    p = pr.load_pricing()
    cache = p.get("cache", {})
    read_m = float(cache.get("read_multiplier", 0.1))
    ttl = str(cache.get("write_default_ttl", "1h"))
    write_m = float(
        cache.get("write_1h_multiplier", 2.0) if ttl == "1h" else cache.get("write_5m_multiplier", 1.25)
    )

    sts = []
    for model in sorted(p.get("models", {})):
        mdef = p["models"][model]
        if mdef.get("deprecated"):
            continue
        price = pr.price_for(model, today, p)
        match = f'IsMatch(log.attributes["model"], "^{model}(-[0-9]{{8}})?$")'
        guard = f'log.attributes["cost_recalc"] == nil and {match} and {TOKEN_GUARD}'
        lc = mdef.get("long_context")
        if lc:
            thr = int(lc.get("threshold", 200_000))
            in_m = float(lc.get("input_multiplier", 2.0))
            out_m = float(lc.get("output_multiplier", 1.5))
            ctx = (
                'Double(log.attributes["input_tokens"]) + Double(log.attributes["cache_read_tokens"])'
                ' + Double(log.attributes["cache_creation_tokens"])'
            )
            sts.append(
                f'- set(log.attributes["cost_recalc"], '
                f'{cost_expr(price["input"] * in_m, price["output"] * out_m, read_m, write_m)}) '
                f'where {guard} and ({ctx}) > {thr}.0'
            )
        sts.append(
            f'- set(log.attributes["cost_recalc"], '
            f'{cost_expr(price["input"], price["output"], read_m, write_m)}) where {guard}'
        )

    sts.append(
        '- set(log.attributes["cost_recalc_src"], "pricing") '
        'where log.attributes["cost_recalc"] != nil'
    )
    sts.append(
        '- set(log.attributes["cost_recalc"], Double(log.attributes["cost_usd"])) '
        'where log.attributes["cost_recalc"] == nil and log.attributes["cost_usd"] != nil'
    )
    sts.append(
        '- set(log.attributes["cost_recalc_src"], "sdk_fallback") '
        'where log.attributes["cost_recalc_src"] == nil and log.attributes["cost_recalc"] != nil'
    )
    return sts


def render(today: datetime.date) -> str:
    lines = [
        BEGIN,
        "      # 生成: scripts/gen-cost-recalc.py — 手動編集禁止 (日付は焼き込まない:",
        "      # --check が生成日でなく価格の実差分だけを検知できるようにするため)",
        "      # pricing.yaml 変更時は再生成して collector を再起動すること (ADR 0006)",
    ]
    lines += [f"          {s}" for s in build_statements(today)]
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    today = datetime.date.today()
    with open(CONFIG, encoding="utf-8") as fh:
        text = fh.read()
    if BEGIN not in text or END not in text:
        sys.stderr.write(f"マーカーが見つかりません: {CONFIG}\n({BEGIN!r} / {END!r})\n")
        return 2
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    new = head + render(today) + tail
    if check:
        if new != text:
            sys.stderr.write("cost_recalc ブロックが pricing.yaml と不整合です。再生成してください:\n"
                             "  python3 scripts/gen-cost-recalc.py && docker compose restart otel-collector\n")
            return 1
        print("OK: cost_recalc ブロックは pricing.yaml と整合")
        return 0
    with open(CONFIG, "w", encoding="utf-8") as fh:
        fh.write(new)
    print(f"生成完了: {CONFIG} (モデル別 statements + fallback)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
