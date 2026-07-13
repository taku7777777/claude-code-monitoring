# Claude Code Monitoring

Claude Code（Anthropic の CLI コーディングエージェント）の利用実態を**ローカル完結**で観測し、
コストを**客観的に最適化する**ための監視基盤。Claude Code が OpenTelemetry で export する
metrics / logs をローカルに集約し、Grafana で「コスト・時間配分・コンテキスト消費・
コスト最適化サイクル」を可視化する。

- **収集**: OpenTelemetry Collector (contrib版) が OTLP (gRPC/HTTP) を受信
- **保存**: ダッシュボードで使う値は全て **Loki (ログ) ベース**。Prometheus は SDK 標準
  メトリクスの受け皿として稼働するだけで、ダッシュボードからは参照しない
  （ログベース採用の経緯は [ADR 0001](docs/adr/0001-log-based-event-architecture.md)）
- **可視化**: Grafana、3 つのダッシュボード（Usage / Context / Cost Optimization）
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
        │             Usage / Context / Cost Optimization
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
- 日々/週次/施策サイクルの見方は [`docs/RUNBOOK.md`](docs/RUNBOOK.md)（運用ランブック）。
- ユースケース別の具体的な判断例（実測値ベース）は [`docs/CASEBOOK.md`](docs/CASEBOOK.md)。
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

`scripts/claude-wrapper.zsh`（`~/.zshrc` へ追記して使う zsh ラッパ、requirements.md 5.2）は
起動時に env を export する**保険**であり、上記2経路が機能する現行バージョンでは必須ではない。
置いていないディレクトリからの起動は `(unset)` として計測が継続される。

### 4. Grafana を開く

<http://localhost:3033> （admin/admin、匿名 Viewer 有効）。

---

## ダッシュボード

| ダッシュボード | uid | 既定期間 | 用途 |
|---|---|---|---|
| **Usage** (`claude-code-usage`) | `claude-code-usage` | now-24h | 利用量の記述（2026-07-14 量に純化）: 総量サマリ（コスト・トークン・行数・セッション数・待機/放置/起動）/ 推移 / 内訳（ws・model・source・トークン種・work_type・tool）/ 行動・時間配分。`$workspace` 変数で全体↔タスク詳細を一本化 |
| **Context** (`claude-code-context`) | `claude-code-context` | now-3h | 最大コンテキスト量、compaction、cache hit 率、prompt/tool 別詳細、context 汚染検出 |
| **Cost Optimization** (`claude-code-cost`) | `claude-code-cost` | now-7d | 単位コスト（CPSO・$/1M実効トークン）、バーンレート/予算、施策の before/after 検証、寄与度分解、サブエージェント委任率 |
| **Prompt明細** (`claude-code-prompt`) | `claude-code-prompt` | now-24h | prompt_id 単位のドリルダウン: 本文・トークン内訳・モデル×ソース・使用ツール・API明細。各 prompt テーブルの行リンクから遷移 |
| **Workspace / Session（Today絞り込み）** (`claude-code-workspace`) | `claude-code-workspace` | Today同一 | Today と同一コンテンツを `workspace` / `session_id` 変数で絞り込む画面。Today の workspace別サマリ行リンクから着地。ヘッダの「Session List」ボタンで下記へ |
| **Session List** (`claude-code-session-list`) | `claude-code-session-list` | now-7d | workspace のセッション一覧（コスト/context/リクエスト数でソート・session_id 検索）。行クリックで Session Detail へ |
| **Session Detail** (`claude-code-session`) | `claude-code-session` | now-24h | Usage を `workspace`/`session_id` で絞った1セッションの詳細（コスト・トークン・行数・待機/放置・モデル/ソース内訳） |

全ダッシュボード（Prompt明細を除く）は Loki datasource（`uid: loki`）を使い、`workspace`
テンプレート変数で絞り込める（複数選択・全選択可）。実際の会話内容の振り返りは
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
[ADR 0005](docs/adr/0005-objective-verification-methods.md)、および requirements.md 第 16 章を参照。

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

---

## 設計判断（ADR）

| ADR | 決定 |
|---|---|
| [0001](docs/adr/0001-log-based-event-architecture.md) | cumulative counter を採らず、ログベース個別イベント方式を採る |
| [0002](docs/adr/0002-effective-tokens-in-collector.md) | `effective_tokens` を Collector で 1 回だけ事前計算する |
| [0003](docs/adr/0003-outcome-signal-and-intervention-marker.md) | 客観サイクルのため `task_outcome` と `intervention_marker` を新設する |
| [0004](docs/adr/0004-pricing-ssot-and-cost-recompute.md) | `pricing.yaml` を価格 SSOT にコストを自前再計算する |
| [0005](docs/adr/0005-objective-verification-methods.md) | 単一ユーザー向けに ITSA + 管理図 + CUSUM/PELT の準実験を採る |

再構築の詳細仕様と 16 件の教訓は [`requirements.md`](requirements.md)、名前・スキーマの契約は
[`docs/CONTRACT.md`](docs/CONTRACT.md) を参照。
