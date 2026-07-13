# 実装契約 (CONTRACT) — Single Source of Truth

このファイルは、全コンポーネント（Collector / Loki / Grafana / hook / 分析スクリプト）が
共有する**名前・エンドポイント・スキーマ**の唯一の基準である。全ファイルはここに従うこと。
値を変える場合は必ずこのファイルを先に更新する。

## 1. ネットワーク / ポート / データソース

| 用途 | 値 |
|---|---|
| OTLP gRPC (SDK metrics + logs) | `http://localhost:4317` |
| OTLP HTTP (hook からの logs 送信) | `http://localhost:4318/v1/logs` |
| Loki | `http://localhost:3100` (docker内: `http://loki:3100`) |
| Prometheus | `http://localhost:9090` (docker内: `http://prometheus:9090`) |
| Grafana | `http://localhost:3033` (admin/admin, anonymous Viewer 有効) |
| Grafana datasource UID (Loki) | `loki` |
| Grafana datasource UID (Prometheus) | `prometheus` |

全 port binding は `127.0.0.1` 限定。**外部公開厳禁**。

## 2. イベント (event_name) 一覧

`event.name` 属性は Loki で index_label `event_name` に昇格される。
下表は本リポジトリが**使う**イベントの契約。SDK が吐く全イベント（未使用含む）の観測カタログは
[1-references/events.md](1-references/events.md)、`api_request` の全属性は
[1-references/api-request-attributes.md](1-references/api-request-attributes.md)。

| event_name | 発生源 | 状態 |
|---|---|---|
| `api_request` | Claude Code SDK | 既存 |
| `tool_decision` | SDK | 既存 |
| `tool_result` | SDK | 既存 |
| `user_prompt` | SDK | 既存 |
| `lines_changed` | hook (PostToolUse) | 既存 |
| `wait_time_observed` | hook (Stop→UserPromptSubmit) | 既存 |
| `permission_wait_observed` | hook (Notification→PostToolUse/Stop) | 既存 |
| `compaction` | SDK | 既存 |
| `task_outcome` | hook (Stop) + CLI | **新規** |
| `intervention_marker` | CLI | **新規** |

## 3. イベント属性スキーマ

### 3.1 `api_request`（SDK 標準 + Collector 派生）
- `cost_usd` (double) — SDK 推定コスト。**公式請求ではない（推定値）**。乖離監視用の参照値
  （ダッシュボードの表示は `cost_recalc` を使う）。
- `cost_recalc` (double, Collector 派生 / ADR 0006) — pricing.yaml の単価×実トークンの
  再計算コスト。**ダッシュボードの全コスト表示の基準値**。計算式は
  `scripts/gen-cost-recalc.py` が pricing.yaml から生成（pricing.yaml 変更時は再生成＋
  collector 再起動が必須）。価格は受信時点で焼き込み（過去分は不変）。
  ダッシュボードのクエリは欠損時（2026-07-12 の導入以前のイベント）に cost_usd へ
  フォールバックする coalesce（`label_format cost_v=...` → `unwrap cost_v`）を使う。
- `cost_recalc_src` (string, Collector 派生) — `pricing`（単価表で計算）/
  `sdk_fallback`（未定義モデルのため cost_usd をコピー）。乖離 stat は pricing 分のみを
  比較するため fallback の混入は stat では検知できない。pricing.yaml 未定義モデルの
  発生は analyzer [g] の未定義警告で気づく。
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` (int, 文字列で届く)
  - `input_tokens` は**キャッシュ未使用の新規入力**（cache 分は別カウント）と仮定する。
- `model` (例 `claude-opus-4-8`), `query_source` (例 `repl_main_thread` / `subagent` / `away_summary`。
  種別の一覧・意味・確度は [query_source リファレンス](1-references/query-source.md)）
- `prompt_id`, `session_id` (structured metadata)
- `duration_ms` (double)
- `effort` (`low`/`medium`/`high`/`xhigh`/`max`), `speed` (`fast` 等), `request_id` — structured metadata。
  SDK が送ってくればそのまま Loki に保持される（追加設定不要、index_label にはしない）。
- `context_tokens` (Collector 派生) = `cache_read_tokens + cache_creation_tokens`
- `effective_tokens` (Collector 派生・**新規**) = §5 参照

### 3.2 `task_outcome`（新規）
- `event.name` = `task_outcome`
- `outcome` (string): `completed` | `success` | `failure` | `abandoned`
  - hook 自動送信は `completed`（Stop 到達の弱いプロキシ）。
  - CLI 手動送信は `success`/`failure`/`abandoned` を**追加記録**する（ログベースのため
    上書きではない。同一タスクに completed と手動ラベルが並存し得る）。
- `outcome_proxy` (string): 判定根拠（例 `stop_reached`, `manual`）
- `note` (string, 任意): CLI 手動送信時の第2引数。空の場合は属性自体を送らない
  （structured metadata、index_label にはしない）。
- `session_id`, `prompt_id` (任意), `workspace`, `work_type`
- **集計規約（減算方式）**: 成果数 = 成功系（`outcome!~"failure|abandoned"`）の件数 −
  失敗系（`outcome=~"failure|abandoned"`）の件数。手動 failure/abandoned はイベントを
  取り消せない（ログは追記のみ）ため、並存する自動 completed を**減算で相殺**する。
  限界2点をダッシュボード説明文に明記すること:
  1. completed と手動 success の重複は分離できず2件計上される
     （厳密な成果数が要る期間は手動ラベル運用に統一する）
  2. Stop 未到達（completed が無い）タスクへの failure/abandoned 記録は相殺相手が無く、
     1件分の過剰減算（保守側の誤差）になる
- **限界**: プロキシは真の品質劣化を取りこぼす。ダッシュボード説明文に明記すること。

### 3.3 `intervention_marker`（新規）
- `event.name` = `intervention_marker`
- `intervention_id` (string): 一意ID（`iv-<epoch>` 形式）
- `category` (string): `caching` | `model_switch` | `context_reduction` | `compaction_tuning` | `prompt_edit` | `subagent_policy` | `other`
- `description` (string), `scope` (string, 任意: 対象 prompt_id/work_type 等)
- `workspace`, `work_type`
- 用途: Grafana annotation として全パネルに縦線を重畳し、施策の before/after を照合。

### 3.4 `prompt_summary`（新規・派生）

prompt 単位の全量サマリ（時間窓ドリルダウン用）。正確な仕様は
[1-references/events.md](1-references/events.md) の `prompt_summary` を参照。

- **last-wins 追記**: テイラー（`scripts/prompt_summary_tailer.py`・30秒周期）が
  活動のあった prompt の更新版を追記し、読み取りは **max 集計**で最新版を採用する
  （end_ms / cost_total / eff_total / requests / creation_main / output_main は
  単調増加のため max = 最新。**単調でない値をこのイベントに追加してはならない** —
  追加する場合は分子分母に分解して送ること）
- **timeUnixNano = start_ms**（窓との重なり判定を可能にするための規約。
  変更してはならない — プロンプト一覧の検索がこの前提に依存する）
- 集計元は api_request のみ（Stop 等の発火イベントに依存しない = 中断・放置も漏れない）

## 4. Loki ラベル方針（cardinality 制御）

index_label に昇格するのは以下の**5つのみ**:
`service_name`, `workspace`, `work_type`, `event_name`, `query_source`

`workspace` / `work_type` は **resource 属性と log 属性の両経路**から昇格させる
（loki-config.yml otlp_config）。resource 属性を持たないイベントは collector が
log 属性側に `(unset)` を付与するため、log 属性経由の昇格が無いと `(unset)` が
ストリームセレクタ（`{workspace=~...}`）で選択できない（2026-07-12 実測バグの修正）。

`session_id` / `prompt_id` / `tool_name` / `model` / `outcome` / `category` / `effort` / `speed` /
`request_id` / 各種 `*_tokens` / `effective_tokens` は **structured metadata**（`| key="..."` で
フィルタ・`unwrap key` で数値化はできるが series を増やさない）。

## 5. `effective_tokens`（実効トークン）定義

価格ウェイトで正規化した「効率比の共通分母」。**Collector 側で1回だけ**計算する（DRY, 派生値は
収集層で単一フィールド化 — requirements.md 12.6 の教訓）。

```
effective_tokens = input_tokens*1.0
                 + cache_read_tokens*0.1
                 + cache_creation_tokens*1.25
                 + output_tokens*5.0
```

ウェイトの根拠（2026-07 時点、要定期確認）: cache read = 通常入力の 0.1x / cache write(5分) = 1.25x /
output = input の 5倍（`pricing/pricing.yaml` の全モデルで output/input 価格比 = 5.0。SSOT と一致させる）。
価格改定時はこのウェイトと `pricing/pricing.yaml` を併せて見直す。
**役割分担（ADR 0006）**: effective_tokens は「作業量の安定した規約値」であり、
金額の正確性は `cost_recalc` が担う。cache write の実課金が 2.0x（1h TTL）と判明した後も
effective_tokens のウェイト 1.25 は互換性のため据え置く（比率指標の連続性を優先）。
**運用注意**: ウェイトを変更すると過去イベントの effective_tokens と連続性が切れる。変更時は
`intervention-marker.sh --category other --desc "effective_tokens weight change"` で時刻を記録すること。

## 5.5 workspace / work_type の運用（タスク = workspace）

- 運用前提: **タスクごとにディレクトリを作成し、タスク = workspace として扱う**
  （docs/USECASE.md）。ラベルは `scripts/init-task-workspace.sh <dir> --type <種別>` で生成する。
- `work_type` はタスク種別の taxonomy: **`feature` / `incident` / `design` / `refactor` /
  `chore`**（常設リポジトリは `main-dev`、cmux 等の自動起動セッションは `auto`、
  フォールバックは `unlabeled`）。enum 強制はしないが、
  種別グルーピング分析はこの値を前提とする。
- `~/github/.claude/settings.json` は `workspace=unlabeled` のフォールバック。個別ラベルの
  無いディレクトリからの起動を「unlabeled」として可視化する（ラベル漏れ検知が目的。
  unlabeled 比率の増加は計測の退行シグナル）。
- workspace 名は再利用しない（例: `task-YYYYMMDD-<内容>`）。

## 6. hook 送信仕様

- 送信先: 環境変数 → 既定 `http://localhost:4318/v1/logs`
  - `CLAUDE_WAIT_OTLP_LOGS_ENDPOINT`（wait/permission）
  - `CLAUDE_LINES_OTLP_LOGS_ENDPOINT`（lines）
  - `CLAUDE_OUTCOME_OTLP_LOGS_ENDPOINT`（task_outcome）
  - `CLAUDE_INTERVENTION_OTLP_LOGS_ENDPOINT`（intervention_marker）
- 送信は `curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d "$JSON" <endpoint>`。
  失敗しても無視（`exit 0`、stderr 無出力）。
- `workspace` / `work_type` の解決順序（全 hook / CLI 共通、resource attributes に付与）:
  1. 環境変数 `OTEL_RESOURCE_ATTRIBUTES`（zsh ラッパ経由の起動時のみ立つ）
  2. hook payload の `cwd`（CLI は `$PWD`）から親へ遡り、各階層の
     `.claude/settings.local.json` → `.claude/settings.json` の `env.OTEL_RESOURCE_ATTRIBUTES`
     （実測: IDE/cmux 等の起動では env が hook 子プロセスへ伝播しないため、この経路が主となる）
  3. CLI のみ `CLAUDE_WORKSPACE` / `CLAUDE_WORK_TYPE`
  4. いずれも無ければ `(unset)`
- OTLP logs JSON の形は requirements.md 11.2 と同型（`resourceLogs[].scopeLogs[].logRecords[]`）。
- hook 設計原則（requirements.md 11.1）を厳守: 常に `exit 0` / 重い処理は `( … ) & disown` /
  state file は session_id 別 / 古い state は 24h で削除。

## 7. 価格 SSOT

`pricing/pricing.yaml` が唯一の価格表（モデルID × 発効日）。`analysis/pricing.py` が唯一のローダ。
モデル ID の日付サフィックス（`-YYYYMMDD`）はローダが正規化して照合する。

利用箇所は3つ（ADR 0004 / 0006）:
1. **collector の `cost_recalc`**（ダッシュボード表示の基準値）— `scripts/gen-cost-recalc.py` が
   pricing.py 経由で OTTL 計算式を生成。**pricing.yaml 変更時は再生成＋collector 再起動が必須**
   （`python3 scripts/gen-cost-recalc.py && docker compose restart otel-collector`。
   整合確認は `--check`）。
2. **analyzer [g]** — SDK 推定（cost_usd）との乖離監視・反実仮想。
3. **校正ループ** — 月次で console.anthropic.com の請求と突合し、乖離があれば
   pricing.yaml を是正 →（1）を再生成（RUNBOOK §5）。

**価格は best-effort（2026-07-12 に SDK 含意単価で経験的校正済み）。実請求との突合で是正し
続けること。** cache 書込は `cache.write_default_ttl`（既定 1h = 2.0x）の単一仮定で近似する。
世代跨ぎの反実仮想はトークナイザ世代差（新世代で同一テキスト約 +30% トークン化）を補正する。

## 8. ダッシュボード

（uid 追加: `claude-code-prompt` = Prompt明細ドリルダウン。prompt テーブルの
`prompt_id` 列 data link から `var-prompt_id` 付きで遷移する。
`claude-code-workspace` = Today のクローンを `workspace` / `session_id` 変数で絞り込む画面
（「Today絞り込み」）。Today の workspace別サマリ行 data link から `var-workspace` +
`from=now/d&to=now` 付きで着地。全パネルは Today と同一クエリに `workspace=~"$workspace"` と
`| session_id=~"$session_id"` を機械付与したもの（生成器
`scripts/gen-workspace-filtered-today.py`）。累積コストパネルのみ $150 予算線と同曜日平均を除去。）

| ファイル | uid | 既定期間 | 内容 |
|---|---|---|---|
| `grafana/provisioning/dashboards/claude-code.json` | `claude-code-usage` | now-24h | 利用量の記述（2026-07-14 量に純化。Session Detail の生成元） |
| `grafana/provisioning/dashboards/claude-code-context.json` | `claude-code-context` | now-3h | コンテキスト分析（10.2） |
| `grafana/provisioning/dashboards/claude-code-cost.json` | `claude-code-cost` | now-7d | **新規** コスト最適化（設計仕様v1） |
| `grafana/provisioning/dashboards/claude-code-today.json` | `claude-code-today` | now/d〜now/M 固定 | 進行中の資源配分・当日検知（3-requirements/today） |
| `grafana/provisioning/dashboards/claude-code-prompt.json` | `claude-code-prompt` | now-24h | prompt_id 単位ドリルダウン（3-requirements/prompt-detail） |
| `grafana/provisioning/dashboards/claude-code-workspace.json` | `claude-code-workspace` | Today同一 | Today を workspace/session で絞り込む画面（3-requirements/workspace） |
| `grafana/provisioning/dashboards/claude-code-session-list.json` | `claude-code-session-list` | now-7d | workspace のセッション一覧（ソート/検索）。生成器 `scripts/gen-session-list.py` |
| `grafana/provisioning/dashboards/claude-code-session.json` | `claude-code-session` | now-24h | Session Detail = Usage を workspace/session で絞り込み。生成器 `scripts/gen-session-detail.py` |

全ダッシュボードは Loki datasource `uid: loki` を使用。templating 変数 `workspace` は
`label_values(workspace)`（複数選択・includeAll・refresh on time range change）。
