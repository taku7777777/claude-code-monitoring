# Claude Code Monitoring

Claude Code（Anthropic の CLI コーディングエージェント）の利用実態を**ローカル完結**で観測し、
コストを**客観的に最適化する**ための監視基盤。Claude Code が OpenTelemetry で export する
metrics / logs をローカルに集約し、Grafana で「コスト・時間配分・コンテキスト消費・
コスト最適化サイクル」を可視化する。

- **収集**: OpenTelemetry Collector (contrib版) が OTLP (gRPC/HTTP) を受信
- **保存**: ダッシュボードで使う値は全て **Loki (ログ) ベース**。Prometheus は SDK 標準
  メトリクスの受け皿として稼働するだけで、ダッシュボードからは参照しない
  （ログベース採用の経緯は [ADR 0001](docs/adr/0001-log-based-event-architecture.md)）
- **可視化**: Grafana。考える起点は **Today**（今の使用状況・常時巡回）と **Cost Optimization**
  （週次の振り返り）の2枚で、そこから Usage / Context / Session List・Detail / Prompt List・明細 へ
  ドリルダウンする（画面一覧は下表）
- **独自計測**: Claude Code Hooks で SDK が出さない「待機時間」「permission 待ち時間」
  「変更行数」に加え、コスト最適化のための **task_outcome / intervention_marker** を送信

> ⚠️ **ローカル開発専用**。詳細は末尾の[セキュリティ注意](#-セキュリティ注意ローカル専用)を参照。

---

## アーキテクチャ

```
Claude Code (SDK)
   │
   ├── metrics (OTLP gRPC :4317) ──┐
   ├── logs    (OTLP gRPC :4317) ──┤
   └── 独自 hook (OTLP HTTP :4318)─┤   ※ hook は logs のみ送信
        │                          ▼
        │                 OTEL Collector
        │                (effective_tokens / context_tokens を1回だけ事前計算)
        │                    │            │
        │        metrics ┌───┘            └───┐ logs
        │                ▼                     ▼
        │         Prometheus (:9090)      Loki (:3100)
        │         受け皿のみ・未参照      ダッシュボードの実データソース
        │                │                     │
        │                └──────────┬──────────┘
        │                           ▼
        │                    Grafana (:3033)
        │        Today / Usage / Context / Cost Optimization / …（ドリル階層）
        │
   独自イベント (hook / CLI 経由で Loki へ):
     ├─ wait_time_observed         応答完了→次プロンプトまでの待機時間
     ├─ permission_wait_observed   permission ダイアログ表示中の時間
     ├─ lines_changed              Edit/Write 等の追加/削除行数
     ├─ task_outcome               成果ラベル（CPSO の分母）★新規
     └─ intervention_marker        施策マーカー（before/after 検証の起点）★新規
```

- 全ポートは `127.0.0.1` 限定バインド。外部公開しない。
- 派生値（`context_tokens`, `effective_tokens`）は **Collector 側で 1 回だけ**計算し、
  ダッシュボードは単一フィールドを参照するだけにしてパネル間の整合を構造的に保証する
  （[ADR 0002](docs/adr/0002-effective-tokens-in-collector.md)）。
- 名前・エンドポイント・スキーマの唯一の基準は [`docs/CONTRACT.md`](docs/CONTRACT.md)。
- **なぜそのコストになるのか**（LLM 課金の仕組み・最適化レバー・監視とアクションの対応）は
  [`docs/0-fundamentals/`](docs/0-fundamentals/README.md)（本リポジトリの意義と使い方の出発点）。
- 日々/週次/施策サイクルの見方は [`docs/RUNBOOK.md`](docs/RUNBOOK.md)（運用ランブック）。
- ユースケース別の具体的な判断例（実測値ベース）は [`docs/CASEBOOK.md`](docs/CASEBOOK.md)。
- **はじめて触る人向け**のハンズオン（サンプルを流して画面がどう動くかを自分で試す）は
  [`docs/ONBOARDING.md`](docs/ONBOARDING.md)。
- パネル単位の要件（何を表示し、どんな判断を実現するか）は
  [`docs/3-requirements/`](docs/3-requirements/README.md)（ページ→セクション別ファイル）。

---

## セットアップ

### 0. 前提条件

| 依存 | 用途 | 備考 |
|---|---|---|
| **Docker** + Compose v2 プラグイン | 監視スタックの起動 | `docker compose version` で確認。**プラグイン未導入でも `scripts/stack-up.sh` が `docker run` で代替起動する** |
| **jq** | 全 hook / CLI スクリプトの JSON 処理 | **必須**。hook は設計上無音（常に exit 0）のため、jq 未導入だと `wait_time_observed` / `lines_changed` / `task_outcome` 等が**エラーも出さずに欠落**する |
| **python3** | 行数計算・ナノ秒時刻・分析スクリプト | macOS 標準の python3 で可 |
| **curl** | hook の OTLP HTTP 送信 | macOS 標準で可 |
| pyyaml / requests | `analysis/*.py` のみ | `pip3 install -r analysis/requirements.txt` |

### 1. 監視スタックを起動

```bash
docker compose up -d        # Compose v2 プラグインがある場合
# または（プラグイン未導入の環境）
./scripts/stack-up.sh       # compose があれば compose、無ければ docker run で同一構成を起動
```

OTEL Collector / Prometheus / Loki / Grafana が起動する（`restart: unless-stopped`）。
停止は `docker compose down` または `./scripts/stack-down.sh`（volume は保持される）。

また、collector は外部ネットワーク `mrw-telemetry`（`stack-up.sh` または
muti-repo-workspace の `devcontainer-up.sh` が `--internal` で作成）にも参加しており、
muti-repo-workspace のエージェントコンテナが同ネットワーク経由で OTLP を送信できる。
このネットワークに参加するのは otel-collector のみで、他のサービスは参加しない
（インターネット経路も無い）。

> **Note**: `docker compose up -d` を直接使う場合は、事前に
> `docker network create --internal mrw-telemetry` を一度実行しておくこと
> （external ネットワークが無いと compose が起動に失敗する。`stack-up.sh` 経由なら自動作成される）。

### 2. Claude Code に telemetry と hook を設定

[`settings/settings.example.json`](settings/settings.example.json) の内容を
`~/.claude/settings.json` に反映する。`/ABSOLUTE/PATH/TO` を**このリポジトリの絶対パス**
（`scripts/` を含むディレクトリ）へ置換すること。例:
`/Users/you/github/claude-code-monitoring/scripts/wait-time-on-stop.sh`。

telemetry を有効化する env（`CLAUDE_CODE_ENABLE_TELEMETRY=1` ほか）と、独自イベント用の
hook（Stop / UserPromptSubmit / Notification / PostToolUse）が含まれている。設定後は
**Claude Code を再起動**しないと反映されない。

### 3. 各プロジェクトに workspace 判定値を置く

計測対象の各プロジェクトルートの `.claude/settings.json` に判定値を仕込む:

```jsonc
{ "env": { "OTEL_RESOURCE_ATTRIBUTES": "workspace=HHW-1234,work_type=main-dev" } }
```

**これだけで workspace 帰属は機能する**（2026-07-11 実測）:

- **Claude Code 本体のテレメトリ**（api_request 等）は、この設定だけで resource attributes に
  `workspace` / `work_type` が付与される（cmux / IDE 起動でも有効）。
- **hook / CLI スクリプト**は、環境変数 `OTEL_RESOURCE_ATTRIBUTES` が hook 子プロセスへ
  伝播しないため、hook payload の `cwd`（CLI は `$PWD`）から親へ遡って
  `.claude/settings.local.json` → `.claude/settings.json` を読み自力で解決する
  （解決順序は CONTRACT §6）。

`scripts/claude-wrapper.zsh`（`~/.zshrc` へ追記して使う zsh ラッパ）は
起動時に env を export する**保険**であり、上記2経路が機能する現行バージョンでは必須ではない。
置いていないディレクトリからの起動は `(unset)` として計測が継続される。

### 4. Grafana を開く

<http://localhost:3033> （admin/admin、匿名 Viewer 有効）。

---

## ダッシュボード

| ダッシュボード | uid | 既定期間 | 用途 |
|---|---|---|---|
| **Today**（主画面）(`claude-code-today`) | `claude-code-today` | now/d（当日）| **今の使用状況を常時巡回する起点**: 本日コスト・背景比率・キャッシュ有効率・コスト構成・コンテキスト遷移・バーン遷移・workspace別サマリ。日次 $150 / 週 $600 / 月 $2000 のヘッドルーム前提。workspace別サマリの行から各タスクへドリルダウン |
| **Usage** (`claude-code-usage`) | `claude-code-usage` | now-24h | 利用量の記述（2026-07-14 量に純化）: 総量サマリ（コスト・トークン・行数・セッション数・待機/放置/起動）/ 推移 / 内訳（ws・model・source・トークン種・work_type・tool）/ 行動・時間配分。`$workspace` 変数で全体↔タスク詳細を一本化 |
| **Context** (`claude-code-context`) | `claude-code-context` | now-3h | 最大コンテキスト量、compaction、cache hit 率、prompt/tool 別詳細、context 汚染検出 |
| **Cost Optimization** (`claude-code-cost`) | `claude-code-cost` | now-7d | 単位コスト（CPSO・$/1M実効トークン）、バーンレート/予算、施策の before/after 検証、寄与度分解、サブエージェント委任率 |
| **Prompt明細** (`claude-code-prompt`) | `claude-code-prompt` | now-24h | prompt_id 単位のドリルダウン: 本文・トークン内訳・モデル×ソース・使用ツール・API明細。各 prompt テーブルの行リンクから遷移 |
| **Workspace / Session（Today絞り込み）** (`claude-code-workspace`) | `claude-code-workspace` | Today同一 | Today と同一コンテンツを `workspace` / `session_id` 変数で絞り込む画面。Today の workspace別サマリ行リンクから着地。ヘッダの「Session List」ボタンで下記へ |
| **Session List** (`claude-code-session-list`) | `claude-code-session-list` | now-7d | workspace のセッション一覧（セッション数・合計コストの集計タイル＋ session_id/開始/最終更新/継続/コスト/$1M実効/リクエスト数/最大context の表）。行クリックで Session Detail へ |
| **Session Detail** (`claude-code-session`) | `claude-code-session` | now-24h | Usage を `workspace`/`session_id` で絞った1セッションの詳細（コスト・トークン・行数・待機/放置・モデル/ソース内訳） |
| **Prompt List** (`claude-code-prompt-list`) | `claude-code-prompt-list` | now-7d | session/workspace のプロンプト一覧。api_request 集計の**実コスト**（＝セッション総コストと一致）・実効tok・取り込み比・実行時間。本文は user_prompt 由来。行の prompt_id から Prompt明細へ |

全ダッシュボード（Prompt明細を除く）は Loki datasource（`uid: loki`）を使い、`workspace`
テンプレート変数で絞り込める（**単一選択**。`All` は allValue `.*` で全 workspace にマッチ）。実際の会話内容の振り返りは
`scripts/show-session.sh <session_id>` でローカル履歴からサルベージできる。
タスク用ディレクトリの作成時は `scripts/init-task-workspace.sh <dir> --type <種別>` で
workspace ラベルを生成する（タスク=workspace 運用。CONTRACT §5.5）。

Cost Optimization ダッシュボードの主なパネル: コスト / プロンプト完遂数 /
成果あたりコスト(CPSO) / コスト/1M実効トークン / キャッシュ有効率 / 単位コスト分布 p50/p90/p95 /
バーンレートと月次着地予測 / 日次コスト推移（施策マーカーを縦線で重畳）/ 管理図近似 /
prompt・work_type・model・query_source 別コスト寄与 / 実効トークン vs 生トークン。

---

## コスト最適化の使い方

「観測 → 診断 → 施策 → 検証」の客観サイクルを回す。詳細な設計判断は
[ADR 0003](docs/adr/0003-outcome-signal-and-intervention-marker.md) /
[ADR 0005](docs/adr/0005-objective-verification-methods.md)、および [docs/3-requirements/cost-optimization/](docs/3-requirements/cost-optimization/README.md) を参照。

### 1. 成果ラベルを付ける（CPSO の分母）

Stop hook は `task_outcome`（`outcome=completed`, `outcome_proxy=stop_reached`）を自動送信する
（Stop 到達の弱いプロキシ）。人間の判断で上書きしたい場合は CLI を使う:

```bash
scripts/task-outcome.sh <success|failure|abandoned> [note]
```

CPSO (Cost Per Successful Outcome) = 期間コスト ÷ 成果数（成功系 outcome 件数 − 失敗系件数の減算方式。手動 failure/abandoned が並存する自動 completed を相殺する — CONTRACT §3.2）。Stop hook の `completed` と手動 `success` は同一タスクで重複し得る点に注意（厳密な成果数が要る場合は手動ラベル運用に統一する）。分母がノイズだと分子最適化が
自己目的化するため、成果シグナルを明示的に記録する。

### 2. 施策を記録して before/after を比較する

コスト削減施策（キャッシュ導入・モデル切替・context 削減・compaction チューニング等）を
実施する瞬間に、施策マーカーを打つ:

```bash
scripts/intervention-marker.sh --category <caching|model_switch|context_reduction|\
  compaction_tuning|prompt_edit|subagent_policy|other> --desc "..." [--scope "..."]
```

`intervention_id=iv-<epoch>` を採番し stdout に返す。Cost Optimization ダッシュボードでは
この時刻に縦線（annotation）が重畳され、施策の前後でコスト・単位コストがどう動いたかを
準実験（ITSA / 管理図）として照合できる。単一ユーザーで A/B ができないためこの方式を採る。

### 3. スクリプトで寄与度分解・CPSO・変化点を掘る

```bash
python3 analysis/cost-optimization-analyzer.py --hours 168 --top 10
python3 analysis/cost-optimization-analyzer.py --json          # 自動化用
```

Loki から集計し、(a) CPSO、(b) パレート寄与度分解（prompt_id / work_type 別・累積80%まで
ハイライト・今期 vs 前期の増分）、(c) ベースライン percentile p50/p90/p95（prompt_id ごとの期間合計コストの分布）、(d) 変化点検出
（CUSUM 内蔵、`ruptures` があれば PELT 併用）、(e) RICE スコア表の雛形を出力する。
`analysis/requirements.txt` の依存を入れておくこと（`requests` は必須、`ruptures` は任意）。

---

## 価格について（重要）

コストは 3 層で扱う。

- **表示**: ダッシュボードのコストは `cost_recalc`（`pricing/pricing.yaml` の単価×実トークンの
  再計算値。collector が取り込み時に計算 /
  [ADR 0006](docs/adr/0006-cost-recalc-in-collector.md)）。計算式は
  `scripts/gen-cost-recalc.py` が pricing.yaml から生成するため、**pricing.yaml 変更時は
  再生成＋collector 再起動**（`python3 scripts/gen-cost-recalc.py && docker compose restart otel-collector`）。
- **監視**: SDK 推定の `cost_usd` はイベントに併存し、概況の「SDK推定との乖離」stat と
  analyzer [g] で再計算との乖離を常時監視する
  （[ADR 0004](docs/adr/0004-pricing-ssot-and-cost-recompute.md)）。
- **正**: ground truth は console.anthropic.com の請求。月次で突合し、乖離があれば
  pricing.yaml を是正する（校正ループ / RUNBOOK §5）。

> ⚠️ `pricing/pricing.yaml` の価格・係数は **best-effort（2026-07-12 に SDK 含意単価で
> 経験的校正済み。校正後の乖離 +3.9%/24h）**。実請求との突合で是正し続けること。
> キャッシュ書込は 1h TTL（2.0x）の単一仮定で近似する。世代跨ぎの反実仮想は
> トークナイザ世代差（新世代で同一テキスト約 +30% トークン化）を補正する。

---

## ⚠️ セキュリティ注意（ローカル専用）

このスタックは**個人ローカル利用専用**であり、共有環境・外部公開では絶対に使用しないこと。

- Grafana が **admin/admin 固定** + **匿名 Viewer 有効**（認証なしで誰でも閲覧できる）
- 全サービスが **`127.0.0.1` バインド**前提（外部到達を想定していない）
- telemetry はローカル完結で外部送出しないが、上記の緩い認証設定のまま公開すると
  ダッシュボード・生ログが第三者に露出する
- **保存データに PII を含む**: `OTEL_LOG_USER_PROMPTS=1` によりユーザープロンプト**全文**と
  `user_email` / `user_id` / `organization_id` 等の識別子が Loki のローカルボリューム
  （`loki-data`）に保持される。`loki-data` の共有・バックアップや `:3100` 公開時は取り扱いに注意

---

## 設計判断（ADR）

| ADR | 決定 |
|---|---|
| [0001](docs/adr/0001-log-based-event-architecture.md) | cumulative counter を採らず、ログベース個別イベント方式を採る |
| [0002](docs/adr/0002-effective-tokens-in-collector.md) | `effective_tokens` を Collector で 1 回だけ事前計算する |
| [0003](docs/adr/0003-outcome-signal-and-intervention-marker.md) | 客観サイクルのため `task_outcome` と `intervention_marker` を新設する |
| [0004](docs/adr/0004-pricing-ssot-and-cost-recompute.md) | `pricing.yaml` を価格 SSOT にコストを自前再計算する |
| [0005](docs/adr/0005-objective-verification-methods.md) | 単一ユーザー向けに ITSA + 管理図 + CUSUM/PELT の準実験を採る |

再構築の背景・16件の教訓・実装順序は [`requirements.md`](requirements.md)（§0-2 / §12 / §15。
詳細仕様は docs/ が正典）、名前・スキーマの契約は [`docs/CONTRACT.md`](docs/CONTRACT.md) を参照。
