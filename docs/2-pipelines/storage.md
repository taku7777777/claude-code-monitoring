# 保存スキーマ（Loki）

> **層: L2** — [2-pipelines/](README.md)。正本は [../CONTRACT.md §4](../CONTRACT.md) と
> `loki-config.yml`。なぜログベースか＝cumulative counter を後から差分計算する方式は
> この用途で構造的に破綻するため（ADR 0001 / requirements.md 12章）。

## index_label は5つだけ

stream label（＝series を増やす）に昇格するのは以下の**5つのみ**:

```
service_name, workspace, work_type, event_name, query_source
```

- これで基本のフィルタ・グルーピング（workspace 絞り込み等）を賄う。
- **cardinality 爆発を防ぐ**ため、`session_id` / `prompt_id` / `tool_name` / `model` /
  `outcome` / `category` / 各 `*_tokens` / `effective_tokens` 等は **structured metadata** に留める
  （`| key="..."` でフィルタ・`unwrap key` で数値化はできるが series は増えない）。

## ラベル昇格は Loki 側で設定

- Collector の `loki.resource.labels` ヒントは Loki 3.x では**効かない**。
  `loki-config.yml` の `limits_config.otlp_config...attributes_config` で `index_label` を明示指定する。
- OTel attribute は Loki 内で `.`→`_` に変換（`event.name`→`event_name`）。

## 未設定値のフォールバック

- `workspace` / `work_type` が無い場合は `"(unset)"` を入れる（[derivations.md](derivations.md)）。
  Grafana の label_values は明示値しか返さないため、フォールバックが無いと選択肢に出ない。
- `unlabeled` 比率の増加は計測の退行シグナル（ラベル漏れ検知。CONTRACT §5.5）。

## 保持・容量

- retention 30日（720h）。`compactor.retention_enabled: true` を忘れるとディスクが際限なく膨らむ。
- Prometheus は SDK 標準メトリクスの受け皿として残置するが**ダッシュボードは未参照**
  （全て Loki ベース。ADR 0001）。
