# イベント別 属性カタログ（api_request 以外）

> **層: L1（観測可能な事実）** — [1-references/](README.md) 参照。
> `api_request` は [api-request-attributes.md](api-request-attributes.md) に独立。
> ここでは他の主要イベントの**イベント固有属性**を記す。
> 共通属性（`prompt_id` / `session_id` / `event_sequence` / `event_timestamp` /
> `observed_timestamp` / `detected_level` / 各 resource・env 属性）は api_request と同じなので
> そちらを参照（重複記載しない）。

いずれも structured metadata（index_label は `event_name` 等の5つのみ。CONTRACT §4）。

## tool_decision（ツール実行の許可/拒否）

| 属性 | 型 | 意味 | 確度 |
|---|---|---|---|
| `tool_name` | string | 対象ツール（例 `Bash` / `Edit` / `Read`） | 高 |
| `decision` | string | 判定（例 `accept` / `reject`） | 高 |
| `source` | string | 判定の由来（例 `config`＝許可リスト由来） | 中〜高 |
| `tool_use_id` | string | ツール実行の一意ID（`toolu_...`） | 高 |
| `tool_parameters` | string(JSON) | **ツール入力の全文**（例 Bash の実行コマンド全体） | 高 |

> ⚠️ **プライバシー注意（実測差異）**: `tool_parameters` には **ツール入力の本文
> （Bash なら実行コマンド全体）がそのまま入る**。requirements.md 5.1 は
> 「`OTEL_LOG_TOOL_DETAILS=1` でも tool_input 本文は送らない」と記していたが、
> 現行 SDK（`service_version` 2.1.x）では tool_decision に全文が含まれることを観測。
> ローカル完結だが Loki に生コマンドが保存される点は認識しておく（無効化したい場合は
> `OTEL_LOG_TOOL_DETAILS` を外す＝tool_name 等も落ちるトレードオフ）。

`tool_result`（ツール結果）は本文でなくサイズ等（`tool_result_size_bytes` 等）を持つ想定
（詳細観測は必要時に追記）。

## compaction（コンテキスト圧縮）

| 属性 | 型 | 意味 | 確度 |
|---|---|---|---|
| `trigger` | string | 契機（`auto`＝上限到達の自動 / `manual`＝`/compact`） | 高 |
| `pre_tokens` | int | 圧縮前のコンテキストトークン数 | 高 |
| `post_tokens` | int | 圧縮後のトークン数 | 高 |
| `duration_ms` | double | 圧縮処理の所要時間（ms） | 高 |
| `success` | bool | 成否（`true`/`false`） | 高 |
| `precompute_reuse` | string | 事前計算の再利用状況（例 `miss_not_ready`）。詳細は公式未確認 | 低 |

Context ダッシュボードの compaction 回数・pre_tokens top10・duration p95 等はこれらを使う。

## 注

- 他イベント（`user_prompt` の `prompt_length`、`lines_changed` の `lines_added`/`lines_removed`、
  `task_outcome`/`intervention_marker` の属性等）は [../CONTRACT.md §3](../CONTRACT.md)（自前イベントの正本）
  と [events.md](events.md) を参照。
- 未知の属性を観測したら本表に追記。
