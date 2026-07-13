# 派生値（collector 事前計算）

> **層: L2** — [2-pipelines/](README.md)。SDK 生値（L1）から collector が取り込み時に計算する
> 派生 attribute。**なぜ収集層で1回だけ計算するか**＝ダッシュボードで毎回計算するとパネル間で
> 式がブレて値が乖離するため（ADR 0002）。正本は [../CONTRACT.md §3/§5](../CONTRACT.md)。

いずれも `otel-collector-config.yml` の transform（OTTL）で `api_request` に付与される。
L1 の生値との区別は [../1-references/api-request-attributes.md](../1-references/api-request-attributes.md)。

## context_tokens

```
context_tokens = cache_read_tokens + cache_creation_tokens
```

- 1リクエストが抱える履歴量。collector の `transform/promote_workspace_logs` で
  `Int()` cast して合算（Loki には文字列で届くため）。
- ダッシュボードでは単一フィールドとして参照（2つの unwrap を `+` で繋ぐと別 series/別時刻の
  最大値同士を合算して乖離する。requirements.md 12.6 の教訓）。

## effective_tokens

```
effective_tokens = input_tokens*1.0 + cache_read_tokens*0.1
                 + cache_creation_tokens*1.25 + output_tokens*5.0
```

- 価格ウェイトで正規化した「作業量の安定した規約値」（効率比の共通分母）。
- ウェイト＝価格比（output/input=5、cache_read=0.1、cache_write(5分)=1.25）。pricing.yaml と一致させる。
- **金額の正確性は担わない**（それは cost_recalc の役割）。cache write の実課金が 2.0x と判明後も
  ウェイト 1.25 は**互換性のため据え置き**（比率指標の連続性を優先。CONTRACT §5）。
- ⚠️ ウェイト変更は過去イベントと連続性が切れる → 変更時は
  `intervention-marker.sh --category other` で時刻を記録。

## cost_recalc / cost_recalc_src

```
cost_recalc = Σ(トークン種別 × pricing.yaml の単価 × キャッシュ/修飾子係数)
cost_recalc_src = "pricing"（単価表で計算） | "sdk_fallback"（未定義モデル→cost_usd をコピー）
```

- **ダッシュボードの全コスト表示の基準値**（SDK 推定 `cost_usd` は乖離監視用の参照に留める。ADR 0004/0006）。
- 計算式は **`scripts/gen-cost-recalc.py` が pricing.yaml から生成**して collector 設定に焼き込む
  （手動編集禁止のコピー）。価格は**受信時点で焼き込み**＝過去分は不変。
- **pricing.yaml 変更時の必須手順**:
  ```bash
  python3 scripts/gen-cost-recalc.py && docker compose restart otel-collector
  # 整合確認: python3 scripts/gen-cost-recalc.py --check
  ```
- 欠損時（2026-07-12 の導入以前のイベント）はダッシュボード側 coalesce で `cost_usd` に
  フォールバック（`label_format cost_v=... → unwrap cost_v`）。

## 帰属属性の昇格（workspace / work_type）

- resource attribute を data point / log attribute に**選択的に昇格**（全 ON はカーディナリティ爆発）。
- 値が無い場合は `"(unset)"` で埋める（Prometheus はラベル不在と空文字を同一視し、
  Grafana の label_values に出ないため）。詳細は [storage.md](storage.md) と CONTRACT §6。
