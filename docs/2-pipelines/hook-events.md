# 自前イベントの生成（hook / CLI）

> **層: L2** — [2-pipelines/](README.md)。SDK が出さない指標を、Claude Code Hooks / CLI で
> **個別イベント**として Loki に直接送る（メトリクスでは送らない。ADR 0001）。
> 正本は [../CONTRACT.md §3/§6](../CONTRACT.md) と requirements.md §11、実体は `scripts/*.sh`。
> イベントの一覧は [../1-references/events.md](../1-references/events.md)（自前由来の節）。

## 生成される自前イベント

| event_name | 生成契機 | 主属性 | スクリプト |
|---|---|---|---|
| `wait_time_observed` | Stop で完了時刻を保存 → 次 UserPromptSubmit で差分送信 | `duration_seconds` | wait-time-on-stop / -on-prompt |
| `permission_wait_observed` | Notification 表示 → 解決（PostToolUse/Stop）で差分送信 | `duration_seconds` | permission-wait-on-* |
| `lines_changed` | PostToolUse（Edit/Write 等）で追加/削除行を計算 | `lines_added`/`lines_removed`/`tool_name` | lines-changed-on-tool-use |
| `task_outcome` | Stop で自動 `completed` / CLI で手動 success 等 | `outcome`/`outcome_proxy` | task-outcome-on-stop / task-outcome |
| `intervention_marker` | CLI 手動（施策を打つ瞬間） | `intervention_id`/`category`/`description` | intervention-marker |

## 送信仕様（全 hook 共通）

- 送信先: 既定 `http://localhost:4318/v1/logs`（OTLP HTTP）。`curl -sS -m 5` で POST、失敗しても無視。
- **workspace/work_type の解決順序**: 環境変数 `OTEL_RESOURCE_ATTRIBUTES` → hook payload の `cwd`
  （CLI は `$PWD`）から親へ遡り `.claude/settings*.json` の `env.OTEL_RESOURCE_ATTRIBUTES` →
  CLI のみ `CLAUDE_WORKSPACE`/`CLAUDE_WORK_TYPE` → 無ければ `(unset)`（CONTRACT §6）。
  ※ settings の env は hook 子プロセスに伝播しないため、経路2（cwd から自力解決）が主。

## hook 設計原則（絶対厳守・requirements.md 11.1）

1. **常に `exit 0` / stderr 無出力**（特に Stop hook で stderr+exit 2 は「続行指示」と誤解される）。
2. 重い処理は `( … ) & disown` でバックグラウンド化し、応答をブロックしない。
3. state file は **session_id 別**に分離（並列セッション干渉防止）。
4. 古い state は **24h で自動削除**（「未送信のまま終了」は計上しない＝思考時間と離席の区別不能のため）。

## 品質・限界

- `task_outcome` の自動 `completed` は Stop 到達の弱いプロキシ。手動 success と重複し得る
  （厳密な成果数は手動ラベル運用に統一。CONTRACT §3.2）。集計は**減算方式**＝成功系
  （`outcome!~"failure|abandoned"`）の件数 − 失敗系（`outcome=~"failure|abandoned"`）の件数。
  手動 failure/abandoned は追記のみで取り消せないため、並存する自動 completed を減算で相殺する
  （ダッシュボード CPSO/完遂数パネル・analyzer と同一定義）。
- 自動起動セッション（cmux 等）の completed 混入に注意（[../3-requirements/cost-optimization/04-verification.md](../3-requirements/cost-optimization/04-verification.md) §3）。
