# event_name カタログ（観測されるイベント種別）

> **層: L1（観測可能なデータ＝事実）** — [1-references/](README.md) 参照。

Loki の index_label `event_name` は「どのイベントか」を表す。値は2系統ある:

- **SDK 由来**: Claude Code 本体が OTLP logs で吐くイベント（公式監視ドキュメントが正本。
  [Claude Code 監視ドキュメント](https://code.claude.com/docs/en/monitoring-usage)）。
- **自前 由来**: 本リポジトリの hook / CLI が OTLP HTTP で送るイベント（正本は
  [../CONTRACT.md §2/§3](../CONTRACT.md)）。

下表は**この環境で実際に観測された値**（過去30d）。SDK 由来の細かいイベントは
バージョンで増減しうる。ダッシュボード利用のあるものは「利用」列に印。

## SDK 由来（Claude Code 本体）

| event_name | 意味 | 利用 | 確度 |
|---|---|---|---|
| `api_request` | Anthropic API を1回呼んだ記録。コスト・トークン・model・query_source 等を持つ**中核イベント**（属性は [api-request-attributes.md](api-request-attributes.md)） | ◎ 全ダッシュボードの主データ源 | 高 |
| `api_refusal` | API 側が拒否（refusal）を返した記録 | − | 中 |
| `assistant_response` | アシスタント応答の完了記録 | − | 中 |
| `tool_decision` | ツール実行の許可/拒否の決定（`tool_name` を持つ。`OTEL_LOG_TOOL_DETAILS=1` で詳細付与） | ○ tool別呼び出し回数 | 高 |
| `tool_result` | ツール実行の結果（`tool_result_size_bytes` 等） | ○ | 高 |
| `user_prompt` | ユーザーがプロンプトを送信した記録（`OTEL_LOG_USER_PROMPTS=1` で `prompt_length`。本文は送らない） | ○ prompt数 | 高 |
| `compaction` | コンテキスト圧縮の記録（`trigger`=auto/manual, `pre_tokens`/`post_tokens`/`duration_ms`） | ○ Context ダッシュボード | 高 |
| `subagent_completed` | サブエージェント（Task 委任）の完了記録 | △ 委任分析の補助 | 中〜高 |
| `permission_mode_changed` | 権限モード（例: acceptEdits 等）の変更記録 | − | 中 |
| `mcp_server_connection` | MCP サーバへの接続イベント | − | 中 |
| `hook_registered` | 起動時に hook が登録された記録 | − | 中 |
| `hook_execution_start` | SDK が hook の実行を開始した記録（＝本体が hook を呼んだ側。自前 hook が"送る"イベントとは別） | − | 中 |
| `hook_execution_complete` | 同・hook 実行の完了記録 | − | 中 |
| `skill_activated` | Skill / スラッシュコマンドが起動した記録 | − | 中 |
| `at_mention` | `@` によるファイル / エージェント等の参照（メンション）記録 | − | 中 |
| `api_error` | API 呼び出しがエラー応答を返した記録（`api_refusal` とは別系統） | − | 中 |
| `api_retries_exhausted` | API リトライ上限に達し再試行を諦めた記録 | − | 中 |
| `feedback_survey` | フィードバック調査が提示された記録 | − | 低〜中 |

> **収集時の除外（2026-07-17〜）**: `hook_execution_start` / `hook_execution_complete` /
> `hook_registered` は消費者がなく全ログの約2/3を占めるため、Collector の filter processor で
> Loki 送信前に drop している。hook のデバッグが必要な場合は filter を一時的に外す。

## 自前 由来（本リポジトリの hook / CLI）

正本は [../CONTRACT.md §2/§3](../CONTRACT.md)。SDK が出さない指標を個別イベントで Loki へ送る。

| event_name | 発生源 | 意味 | 利用 | 確度 |
|---|---|---|---|---|
| `lines_changed` | hook (PostToolUse) | Edit/Write 等の追加/削除行数（`lines_added`/`lines_removed`, `tool_name`） | ○ | 高 |
| `wait_time_observed` | hook (Stop→UserPromptSubmit) | 応答完了→次プロンプトまでの待機時間（`duration_seconds`） | ○ 放置率 | 高 |
| `permission_wait_observed` | hook (Notification→解決) | permission ダイアログ表示中の時間（`duration_seconds`） | ○ 放置率 | 高 |
| `task_outcome` | hook (Stop) + CLI | 成果ラベル（`outcome`, `outcome_proxy`）。CPSO の分母 | ○ CPSO | 高 |
| `intervention_marker` | CLI | 施策マーカー（`intervention_id`, `category`, `description`）。annotation 縦線の源 | ○ 効果検証 | 高 |

## 注

- 「利用」が − のイベントは現状ダッシュボード未使用（将来の分析余地）。
- SDK 由来イベントの網羅・正確な発火条件は公式監視ドキュメントが正本。本表は観測事実の記録。
- 未知の event_name を観測したら本表に追記（[再取得コマンドは query-source.md 末尾と同様に
  `label/event_name/values`]）。

## `prompt_summary`（派生・自前）

prompt 単位のサマリ。`scripts/prompt_summary_tailer.py`（docker compose の
prompt-summary-tailer）が api_request を30秒ごとに集計し、**last-wins 追記**する
（Loki は追記専用のため上書きせず、読み取り側が max 集計で最新版を採用。
end/コスト/tok は単調増加なので max = 最新。定義は CONTRACT §3.4）。

- **timeUnixNano = prompt の開始時刻**（時間窓との重なり判定を
  `end_ms >= 窓開始 | start_ms <= 窓終端` のメタデータ数値比較で行うため。
  out-of-order 追記になるため loki-config の ingester.max_chunk_age を 48h に拡大済み）
- 属性: `prompt_id` / `start_ms`（= user_prompt 時刻、無ければ最初の api_request）/
  `end_ms`（最後の api_request）/ `cost_total`（cost_recalc 系 coalesce 合計）/
  `eff_total` / `requests` / `creation_main` / `output_main`（main の取り込み比の分子分母 —
  比率は単調でないため生値を送る）/ `session_id` / `prompt`（先頭300字）
- Stop hook に依存しないため**中断・放置された prompt も漏れない**
- 消費者: プロンプト一覧ダッシュボード（時間窓ドリルダウン）
