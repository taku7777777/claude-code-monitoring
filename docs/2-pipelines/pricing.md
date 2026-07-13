# 単価表と校正ループ

> **層: L2** — [2-pipelines/](README.md)。正本の単価表は
> [../../pricing/pricing.yaml](../../pricing/pricing.yaml)（SSOT）、唯一のローダは
> `analysis/pricing.py`。詳細契約は [../CONTRACT.md §7](../CONTRACT.md)、運用は RUNBOOK §5。

## 3層のコスト観

| 種類 | 値 | 用途 |
|---|---|---|
| **表示** | `cost_recalc`（pricing.yaml × 実トークン） | ダッシュボードの全コスト（[derivations.md](derivations.md)） |
| **監視** | `cost_usd`（SDK 推定） | 再計算との乖離を「SDK推定との乖離」stat / analyzer で常時監視 |
| **正** | console.anthropic.com の請求 | ground truth。月次で突合し pricing.yaml を是正 |

## pricing.yaml の中身（L2 の設計値）

- モデル × 単価（input/output, $/MTok）× 発効日。日付サフィックス（`-YYYYMMDD`）は
  ローダが剥がして照合（[../1-references/models.md](../1-references/models.md)）。
- キャッシュ係数（read 0.1x / write 5m 1.25x / write 1h 2.0x、既定 TTL=1h）。
- 修飾子（batch 0.5x / fast 2.0x / geo_us 1.1x）、トークナイザ世代差補正（new≒+30%）。
- **best-effort 値**（2026-07-12 に SDK 含意単価で経験的校正済み。校正後乖離 +3.9%/24h）。

## 校正ループ（Operate の一部）

```
月次: console.anthropic.com の実請求と cost_recalc / cost_usd を突合
  └─ 乖離あり ─▶ pricing.yaml を是正
       └─▶ python3 scripts/gen-cost-recalc.py   # 計算式を再生成
            └─▶ docker compose restart otel-collector   # 反映（過去分は不変）
```

- **利用箇所は3つ**（DRY・pricing.py 経由のみ）:
  1. collector の `cost_recalc`（表示基準。gen-cost-recalc が生成）
  2. analyzer [g]（SDK 推定との乖離監視・反実仮想）
  3. 校正ループ（本節）
- 未定義モデルは `sdk_fallback`。混入は概況「SDK推定との乖離」stat で気づく。
