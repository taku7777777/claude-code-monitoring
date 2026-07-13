# Claude Code OTEL Monitoring — 再構築仕様書 (requirements.md)

このドキュメントは、本リポジトリと**同等の機能をゼロから再現性高く再構築する**ために必要な要件・設計・実装知見を1ファイルに集約したものである。

対象読者は「このリポジトリの過去のファイルを一切参照できない別のAI/エンジニア」。したがって:
- 単なる「何を作るか」ではなく「どう作るべきか」「なぜそう作るのか」「何を避けるべきか」まで含める
- 過去に踏んだ失敗（16件の ADR = Architecture Decision Record）を要約し、**同じ轍を踏まないための情報**を優先的に残す
- 実装方式（Prometheus / Loki / Grafana / OTEL Collector）は本リポジトリで採用した具体解だが、別の技術スタックで再実装する場合も本質的な要件（第1〜2章）は変わらない

---

## 0. エグゼクティブサマリー

**何を作るか**: Claude Code (Anthropic の CLI コーディングエージェント) が OpenTelemetry で export する metrics / logs をローカルに集約し、Grafana ダッシュボードで「コスト・時間配分・コンテキスト消費・セッション活動」を可視化するローカル観測基盤。

**最終的な技術構成（本リポジトリの到達点）**:
- **収集**: OpenTelemetry Collector (contrib版) が Claude Code から OTLP (gRPC/HTTP) を受信
- **保存**: **ダッシュボードで使う値は全て Loki (ログ) ベース**。Prometheus は SDK 標準メトリクスの受け皿として稼働だけしているが、ダッシュボードからは参照していない（歴史的経緯があり後述）
- **可視化**: Grafana、2つのダッシュボード（利用量 / コンテキスト分析）
- **独自計測**: Claude Code Hooks (Stop / UserPromptSubmit / Notification / PostToolUse) を使い、SDK が出さない「待機時間」「permission待ち時間」「変更行数」を独自イベントとして Loki に送信

**最重要の設計原則（先に知っておくべきこと）**:
> **「累積値 (cumulative counter) を保存して後からクエリ時に差分計算する」アーキテクチャは、この用途では構造的に破綻する。**
> 理由は第12章で詳述するが、結論として **「個別イベントを記録し、`sum_over_time` で単純合算する」方式（ログベース）を最初から採用すべき**。本プロジェクトは cumulative counter 方式 (Prometheus) から個別イベント方式 (Loki) へ段階移行する過程で 16 件の ADR を積み上げた。ゼロから作るなら、最初からログベースのイベント記録方式を選び、この移行コストを払わずに済ませられる。

---

## 1. 背景・目的

Claude Code の利用実態（コスト・時間・行数・コンテキスト消費・セッション活動）を可視化することで、以下を実現する。

- **コスト最適化**: チケット（ワークスペース）単位・作業種別単位での費用把握
- **時間配分の把握**: ユーザー操作時間・Claude 実行時間・放置時間の比率把握
- **コンテキスト効率の把握**: cache hit 率・compaction 発生状況・token 消費量
- **異常検知**: 不審な高コスト・長時間化・compaction 連発などの即時把握

想定利用者: 個人ローカル環境で Claude Code を使う開発者本人（マルチユーザー集計は非対応）。

## 2. 用語定義

| 用語 | 定義 |
|---|---|
| **ワークスペース (workspace)** | 1つのチケット作業に対応する作業ディレクトリ単位。例: `HHW-1234` |
| **作業種別 (work_type)** | ワークスペースの作業カテゴリ。例: `main-dev` / `server-dev` / `web-dev` / `incident` |
| **セッション (session)** | Claude Code の1回の起動から終了までの単位。内部で `session_id` (UUID) が振られる |
| **アクティブセッション** | 表示期間内に少なくとも1回 Claude へリクエストを送信したセッション |
| **Claude起動処理時間 (active_time)** | Claude が推論・ツール実行で活動している時間 |
| **Claude待機時間 (wait_time)** | Claude が応答完了後、ユーザーの次プロンプトを待っている時間 |
| **Permission待ち時間 (permission_wait)** | 権限確認ダイアログ表示中の時間 |
| **放置率** | (待機時間 + permission待ち時間) ÷ (起動処理時間 + 待機時間 + permission待ち時間) |
| **コンテキスト量 (context_tokens)** | api_request 1回あたりの履歴トークン数 (`cache_read_tokens + cache_creation_tokens`) |
| **compaction** | コンテキスト上限到達時に履歴を要約して圧縮する処理。`auto` / `manual` の2種類 |
| **query_source** | どの処理系統がAPIを呼んだかの識別子。例: `repl_main_thread`（メイン対話）, `away_summary`（バックグラウンド要約） |

---

## 3. 機能要件（実装非依存）

再実装するプラットフォームが何であれ、以下を満たすこと。

### 3.1 共通要件

| ID | 要件 |
|---|---|
| C-01 | 表示期間（time range）を任意に変更できる |
| C-02 | **任意の時間レンジ（5分〜数日）で値が頭打ち・固定化しない**。短期から長期まで時間とともに自然に増加・推移すること |
| C-03 | **ワークスペース絞り込み**ができる（複数選択・全選択可能） |
| C-04 | 同一指標を複数パネル（例: stat と pie chart）で表示する場合、**両者の値が整合**していること |
| C-05 | **異常値ガード**: 物理的に取り得ない値（比率 > 100%、負の累積差分）は表示前にクランプされ、誤情報を出さないこと |
| C-06 | **計測欠損の透明性**: 「ログ送信開始前に起動していたセッションが集計から漏れる」等の制約は、該当パネルの説明に明記する |
| C-07 | パネル説明から設計判断の経緯（ADR）への導線がある |

### 3.2 利用量ダッシュボード — 全体ビュー

| ID | 要件 |
|---|---|
| U-01 | 表示期間内の**総コスト (USD)** |
| U-02 | 表示期間内の**追加行数・削除行数の合計** |
| U-03 | 表示期間内の**アクティブセッション数** |
| U-04 | **放置率**（ユーザー応答待ち時間の割合） |
| U-05 | **Claude起動処理時間**の累計（permission待ち時間を除外した純粋な処理時間） |
| U-06 | **Claude待機時間**の累計 |
| U-07 | **コスト推移**を時系列で確認（時間方向に細粒度の増分） |
| U-08 | **ワークスペース別コスト内訳**（円グラフ） |
| U-09 | **作業種別 (work_type) 別コスト内訳**（円グラフ） |
| U-10 | コストに**異常検知の閾値色分け**がある（黄/橙/赤）。「計測バグの可能性」に気づける |

### 3.3 利用量ダッシュボード — ワークスペース別ビュー

選択中のワークスペースに絞った同等の指標に加え:

| ID | 要件 |
|---|---|
| W-01 | コスト・行数・セッション数・放置率・起動処理時間・待機時間（全体ビューと同粒度） |
| W-02 | **トークン使用量**（input / output / cache 種別ごと） |
| W-03 | **Permission待ち時間** |
| W-04 | **モデル別コスト内訳**（Opus / Sonnet / Haiku 等） |
| W-05 | **ソース別コスト内訳**（メイン対話 / サブエージェント / バックグラウンド要約等） |
| W-06 | **トークン推移**を時系列で |
| W-07 | **トークン種別内訳**（円グラフ） |
| W-08 | **コスト推移**を時系列で |

### 3.4 コンテキスト分析ダッシュボード

| ID | 要件 |
|---|---|
| X-01 | **現在の最大コンテキスト量**（api_request 1回あたりの履歴トークン数） |
| X-02 | 表示期間内の **auto-compaction 発生回数** |
| X-03 | 表示期間内の **manual /compact 発生回数** |
| X-04 | 表示期間内の**累計コスト** |
| X-05 | 表示期間内の**アクティブセッション数** |
| X-06 | **セッション別コンテキスト量推移**、compaction発生タイミングが視覚的に識別できる |
| X-07 | **セッション別最大コンテキスト量 top10**（重い対応のセッション特定） |
| X-08 | **compaction直前のコンテキスト量 top10**（どこまで貯めたか） |
| X-09 | **cache hit比率の推移**（コンテキスト効率） |
| X-10 | **compaction処理時間 (p95/avg)** |
| X-11 | **prompt別コスト top10**（テーブル、prompt_idで詳細追跡） |
| X-12 | **tool_name別呼び出し回数 top15** |
| X-13 | **ソース別トークン消費内訳**（メイン対話 vs サブエージェント等） |
| X-14 | **モデル別コスト内訳** |
| X-15 | **直近のcompactionイベント**を生ログで（pre_tokens/post_tokens/duration/trigger） |
| X-16 | コンテキスト量表示に **Opus系上限 (200,000 tokens) への危険水域**の視覚的目印 |
| X-17 (拡張) | **context汚染検出**: prompt別の「取り込み/生成比」top10、prompt別turn数top10、取り込み/生成比の推移（後述12.11） |

### 3.5 データ取得・記録要件

| ID | 要件 |
|---|---|
| D-01 | Claude Code起動時に**ワークスペース**が自動判定され、各イベント・メトリクスにラベル付けされる |
| D-02 | Claude Code起動時に**作業種別 (work_type)** が自動判定され、ラベル付けされる |
| D-03 | 同一のClaude Code sessionが複数回exportされても、集計上は1つのセッションとして扱える |
| D-04 | telemetry出力先は**ローカル環境内**で完結し、外部に送出されない |
| D-05 | 計測対象データは**少なくとも7日間**保持され、長期トレンドを参照できる |
| D-06 | サービス再起動・予期せぬ電源断でも**過去の計測データを保持** |
| D-07 | データ収集が一時的に停止していた期間があっても、再開後は自動的に集計が継続される |

### 3.6 運用要件

| ID | 要件 |
|---|---|
| O-01 | `docker compose up -d` 1コマンドで全サービスが起動する |
| O-02 | サービスはDocker daemon起動中であれば**自動再起動**する |
| O-03 | ダッシュボードは**Webブラウザ**からアクセスできる |
| O-04 | 認証なしの**Viewer権限**（読み取り専用）で誰でも閲覧できる |
| O-05 | ダッシュボード定義は**コードとしてバージョン管理**（provisioning） |
| O-06 | 過去の方式選定・バグ対応の経緯は**ADRとして残す** |
| O-07 | 不可逆な変更（データリセット、方式変更、トレードオフを含む選択）時は**新規ADRを追加** |

### 3.7 非機能要件

| ID | 要件 |
|---|---|
| N-01 | 通常利用負荷下でダッシュボード読み込みが**数秒以内** |
| N-02 | 1日中使い続けてもストレージが**数GB以下**に収まる |
| N-03 | telemetry収集によるClaude Codeの**応答速度低下が体感できない** |
| N-04 | telemetry関連プロセスがクラッシュしても**Claude Code本体の動作に影響しない** |

### 3.8 明示的スコープ外

- Claude Code以外のツール・プロセスの監視
- 複数ユーザー間での集計（個人利用前提）
- アラート通知（Slack/Email等）
- 月次レポート・PDFエクスポート
- 過去データの編集・補正
- 認証・権限管理（個人ローカル利用前提）
- traces（`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`）は未使用。将来検討事項

### 3.9 実装非依存で残る既知の制約

- telemetry設定が反映されていない期間に起動したセッションは、設定反映後も**再起動するまで集計に出てこない**
- ワークスペース判定はシェル起動時の環境変数に依存するため、シェルラッパー設定が前提
- セッション単位の詳細分析はメトリクス基盤側では持たない方針（後述の理由により、ログ基盤に一本化）

---

## 4. システム全体構成（実装ベースライン）

### 4.1 コンポーネントとバージョン

| コンポーネント | イメージ/バージョン | 役割 |
|---|---|---|
| Claude Code (SDK) | n/a | telemetry出力元（OTLP gRPC/HTTP） |
| OpenTelemetry Collector | `otel/opentelemetry-collector-contrib:0.150.1` | 受信・属性変換・分配（**contrib版必須**、transform processorを使うため） |
| Prometheus | `prom/prometheus:v3.11.3` | SDK標準メトリクスの受け皿（ダッシュボードでは不使用。将来的に廃止可能） |
| Loki | `grafana/loki:3.5.1` | ログ保存（ダッシュボードの実データソース） |
| Grafana | `grafana/grafana:13.0.1` | 可視化 |

### 4.2 データフロー

```
Claude Code (SDK)
   │
   ├── metrics (OTLP gRPC :4317) ──┐
   ├── logs    (OTLP gRPC :4317) ──┤
   └── 独自 hook (OTLP HTTP :4318)─┤  ※ hookはlogsのみ送信
                                    ▼
                           OTEL Collector
                              │       │
                  metrics ┌───┘       └───┐ logs
                          ▼               ▼
                   Prometheus          Loki
                   (:9090, 未参照)     (:3100, ダッシュボード実体)
                          │               │
                          └───────┬───────┘
                                  ▼
                              Grafana (:3033)
```

### 4.3 永続化

| Volume | 用途 | Retention |
|---|---|---|
| `prometheus-data` | メトリクスTSDB | 30日 / 2GB（先に当たった方） |
| `loki-data` | ログchunks + index | 30日 (720h) |
| `grafana-data` | Grafana設定・履歴 | 無期限 |

### 4.4 docker-compose.yml（そのまま利用可能な完全定義）

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.150.1
    restart: unless-stopped
    volumes:
      - ./otel-collector-config.yml:/etc/otelcol-contrib/config.yaml
    ports:
      - "127.0.0.1:4317:4317"   # OTLP gRPC
      - "127.0.0.1:4318:4318"   # OTLP HTTP
    depends_on:
      - prometheus
      - loki

  prometheus:
    image: prom/prometheus:v3.11.3
    restart: unless-stopped
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=30d
      - --storage.tsdb.retention.size=2GB
      # 突然の電源断でWALごと飛ばないよう、ブロック確定間隔を短縮（既定2h→30m）
      - --storage.tsdb.min-block-duration=30m
      - --storage.tsdb.max-block-duration=30m
      - --storage.tsdb.wal-compression
      - --web.console.libraries=/usr/share/prometheus/console_libraries
      - --web.console.templates=/usr/share/prometheus/consoles
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    ports:
      - "127.0.0.1:9090:9090"

  loki:
    image: grafana/loki:3.5.1
    restart: unless-stopped
    command: -config.file=/etc/loki/local-config.yaml
    volumes:
      - ./loki-config.yml:/etc/loki/local-config.yaml
      - loki-data:/loki
    ports:
      - "127.0.0.1:3100:3100"

  grafana:
    image: grafana/grafana:13.0.1
    restart: unless-stopped
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    ports:
      - "127.0.0.1:3033:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
    depends_on:
      - prometheus
      - loki

volumes:
  prometheus-data:
  grafana-data:
  loki-data:
```

**重要**: 全サービスの port binding は `127.0.0.1` 限定。**外部公開厳禁**（Grafanaがadmin/admin固定 + anonymous viewer有効のため）。

---

## 5. Claude Code側の設定

### 5.1 telemetry有効化 (`~/.claude/settings.json`)

```jsonc
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
    // Prometheus は cumulative 前提（Counter リセットや Collector 再起動に強い）
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
    // ダッシュボードは Loki ログベースの集計を使うため必須
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_LOG_USER_PROMPTS": "1",
    "OTEL_LOG_TOOL_DETAILS": "1"
  }
}
```

設定後は **Claude Codeを再起動**しないと反映されない。

| 変数 | 役割 | プライバシー影響 |
|---|---|---|
| `OTEL_LOGS_EXPORTER=otlp` | logsパイプライン有効化（必須） | なし |
| `OTEL_LOG_USER_PROMPTS=1` | `user_prompt` eventに`prompt_length`を含める | プロンプト本文は送らない・長さのみ |
| `OTEL_LOG_TOOL_DETAILS=1` | `tool_decision` に `tool_name`/`decision`/`tool_use_id`/**`tool_parameters`**、`tool_result` に `tool_result_size_bytes` 等を含める | ⚠️ **tool 入力本文が送られる**（下記） |

> ⚠️ **プライバシー実測訂正（2026-07-13）**: 当初「tool_input/output 本文は送らない」と記していたが、
> 現行 SDK（`service_version` 2.1.x）では **`tool_decision.tool_parameters` に tool 入力の本文
> （Bash なら実行コマンド全体）がそのまま含まれる**ことを実測で確認した。ローカル完結だが
> 生コマンドが Loki に保存される点は認識すること（無効化は `OTEL_LOG_TOOL_DETAILS` を外す＝
> tool_name 等の計測も失うトレードオフ）。tool の**出力**本文はサイズ（`tool_result_size_bytes`）
> のみで本文は送られない。詳細は [docs/1-references/event-attributes.md](docs/1-references/event-attributes.md)。

**重要**: `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` を `delta` にする誘惑があるが、**やってはいけない**（第12.9章）。

### 5.2 ワークスペース判定（shell wrapper は保険に降格 — 5.2.1 の実測アップデート参照）

`OTEL_RESOURCE_ATTRIBUTES` は OpenTelemetry SDK の仕様上、**プロセス起動時に環境変数として立っている必要があり、`settings.json` の `env` 経由では効かない**（SDK初期化がenv注入より早い）——と当初想定していたが、**現行バージョンでは本体テレメトリに限りプロジェクト `.claude/settings.json` の `env` だけで効くことを実測確認した（5.2.1）**。

そこでシェル関数で `claude` をラップし、CWDから親ディレクトリを順に遡って各階層で `.claude/settings.local.json`（個人優先）→ `.claude/settings.json` の順に探索し、最初に見つかった値をexportしてから本体を起動する。

```bash
# ~/.zshrc に追加
claude() {
  emulate -L zsh
  setopt local_options no_xtrace no_verbose no_print_exit_value
  if (( $+commands[jq] )); then
    local dir=$PWD attrs= candidate=
    while [[ -n $dir && $dir != / ]]; do
      for candidate in $dir/.claude/settings.local.json $dir/.claude/settings.json; do
        if [[ -f $candidate ]]; then
          attrs=$(jq -r '.env.OTEL_RESOURCE_ATTRIBUTES // empty' $candidate 2>/dev/null)
          [[ -n $attrs ]] && break 2
        fi
      done
      dir=${dir:h}
    done
    if [[ -n $attrs ]]; then
      OTEL_RESOURCE_ATTRIBUTES=$attrs command claude "$@"
      return
    fi
  fi
  command claude "$@"
}
```

ワークスペース側の `.claude/settings.json` には以下を仕込む:

```jsonc
{
  "env": {
    "OTEL_RESOURCE_ATTRIBUTES": "workspace=HHW-1234,work_type=main-dev"
  }
}
```

**制約（既知）**: shell wrapperはzsh専用。bash/fish環境では動作しない。ワークスペース判定はCWD依存。

#### 5.2.1 実測アップデート（2026-07-11）: wrapper なしでも workspace は付く

cmux 経由（zsh ラッパを通らない起動、`OTEL_RESOURCE_ATTRIBUTES` 環境変数なし）の実セッションで確認した実測挙動:

1. **本体テレメトリには効く**: プロジェクトの `.claude/settings.json` の `env.OTEL_RESOURCE_ATTRIBUTES`
   だけで、`api_request` / `user_prompt` 等の本体イベントに `workspace` / `work_type` が付与された。
   冒頭の「settings.json の env では効かない」は現行バージョンの本体には当てはまらない。
2. **子プロセスには伝播しない**: 同じセッションで hook（および Bash ツール）の環境には
   `OTEL_METRICS_EXPORTER` 等の settings 由来 env が現れない。settings の env は本体内部にのみ
   適用され、子プロセスへは export されない。このため環境変数頼みの hook は `(unset)` になる。
3. **対策（実装済み）**: 全 hook / CLI スクリプトは環境変数が無い場合、hook payload の `cwd`
   （CLI は `$PWD`）から親へ遡って `.claude/settings.local.json` → `.claude/settings.json` の
   `env.OTEL_RESOURCE_ATTRIBUTES` を自力解決する（zsh ラッパと同一の探索順。CONTRACT §6）。

結論: **プロジェクトに `.claude/settings.json` を置くことが主経路**であり、zsh ラッパ
（`scripts/claude-wrapper.zsh`）は旧バージョン・端末起動向けの保険。この実測は
Claude Code のバージョン更新で変わりうるため、workspace が `(unset)` に退行した場合は
まず本節の 1↔2 のどちらが破れたかを再確認すること。

### 5.3 リアルタイム性

| 区間 | 遅延 |
|---|---|
| Claude CodeのOTLP export | 60秒ごと（SDK側固定・変更不可） |
| （Prometheus使用時のみ）scrape | 30秒ごと |
| ダッシュボード表示までの最大遅延 | 約90秒 |

---

## 6. OTEL Collector 設定

`otel-collector-config.yml` の完全定義:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"
    # 古い session.id の系列を 3h で破棄してカーディナリティを抑える
    metric_expiration: 180m

  # Loki 3.x 公式 OTLP endpoint。deprecated な lokiexporter ではなく
  # otlp_http で /otlp/v1/logs に直接送るのが現行推奨パス。
  # （Collector 0.150+ で alias `otlphttp` は deprecated、`otlp_http` を使う）
  otlp_http/loki:
    endpoint: http://loki:3100/otlp
    tls:
      insecure: true

  debug:
    verbosity: basic

processors:
  batch:
    timeout: 5s

  # workspace / work_type の resource attribute を data point attribute に昇格させ、
  # Prometheus 側で各メトリクスのラベルとして利用可能にする。
  # resource_to_telemetry_conversion を全ONにするとカーディナリティが膨らむため、
  # 意図した属性だけを選択的に昇格させる。
  # 値がない場合は "(unset)" を入れ、Grafana の label_values で選択肢として表示できるようにする
  # (Prometheus はラベル不在と空文字を同一視し、label_values は明示値しか返さないため)。
  #
  # ★ session_id は metrics pipeline 側でのみ delete している (logs pipeline には残す)。
  #   理由: Claude Code SDK が稀に同 (workspace, session_id, query_source) ラベルで
  #   2系統の cumulative state を交互に publish することがあり、
  #   Prometheus 側で series が振動して rate()/increase() のみならず
  #   <metric> - <metric> offset X 方式でもスパイクが発生していた。
  #   session_id を落として SUM 統合することでこの病的振動を解消する。
  #   セッション別の詳細分析は Loki ベースの context dashboard で代替する。
  transform/promote_workspace:
    error_mode: ignore
    metric_statements:
      - context: datapoint
        statements:
          - set(attributes["workspace"], resource.attributes["workspace"]) where resource.attributes["workspace"] != nil
          - set(attributes["workspace"], "(unset)") where attributes["workspace"] == nil
          - set(attributes["work_type"], resource.attributes["work_type"]) where resource.attributes["work_type"] != nil
          - set(attributes["work_type"], "(unset)") where attributes["work_type"] == nil
          - delete_key(attributes, "session.id")
          - delete_key(attributes, "session_id")

  # logs 側でも workspace/work_type を resource → log attribute に複製。
  # メトリクスと同じ workspace フィルタが logs パネルにも効くようにする。
  #
  # context_tokens は cache_read_tokens + cache_creation_tokens を Collector 側で事前合算した派生 attribute。
  # ダッシュボード側で 2 つの unwrap を `+` で繋ぐと、別 series / 別時刻の最大値同士を合算してしまい
  # 値が乖離する (例: stat panel が 1M を示すが timeseries は最大 558K しか出ない)。
  # 単一フィールドにしておくと panel 間で必ず整合する。
  transform/promote_workspace_logs:
    error_mode: ignore
    log_statements:
      - context: log
        statements:
          - set(log.attributes["workspace"], resource.attributes["workspace"]) where resource.attributes["workspace"] != nil
          - set(log.attributes["workspace"], "(unset)") where log.attributes["workspace"] == nil
          - set(log.attributes["work_type"], resource.attributes["work_type"]) where resource.attributes["work_type"] != nil
          - set(log.attributes["work_type"], "(unset)") where log.attributes["work_type"] == nil
          # context_tokens = cache_read_tokens + cache_creation_tokens (Loki に届く時点で string 型なので Int() で cast)
          - set(log.attributes["context_tokens"], Int(log.attributes["cache_read_tokens"]) + Int(log.attributes["cache_creation_tokens"])) where log.attributes["cache_read_tokens"] != nil and log.attributes["cache_creation_tokens"] != nil

service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [transform/promote_workspace, batch]
      exporters: [prometheus, debug]
    logs:
      receivers: [otlp]
      processors: [transform/promote_workspace_logs, batch]
      exporters: [otlp_http/loki, debug]
```

**再実装時の要点**:
1. `resource_to_telemetry_conversion: true` で全属性を昇格させてはいけない（`service.instance.id` 等の高カーディナリティ属性まで昇格し、series数が爆発する）。**意図した属性だけ選択的に昇格**させること
2. 未設定値は必ず `"(unset)"` などのフォールバック値で埋める（Prometheusはラベル不在と空文字を同一視するため、フォールバックがないとドロップダウンの選択肢に出てこない）
3. 派生計算が必要な値（`context_tokens` など）は**Collector側で1回だけ事前計算**し、ダッシュボード側では単一フィールドとして扱う。ダッシュボード側で毎回同じ計算をさせるとpanel間で計算方法がブレて値が乖離する

---

## 7. Prometheus 設定（SDK標準メトリクスの受け皿として残置）

`prometheus.yml`:

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:
  - job_name: "otel-collector"
    static_configs:
      - targets: ["otel-collector:8889"]
```

起動オプション（docker-compose参照）で `min-block-duration=30m` / `wal-compression` を必ず設定すること。デフォルトの2hブロック確定間隔だと、ローカルPCのスリープ・電源断で最大2時間分のデータがWALごと失われるリスクがある。30分に短縮すると最悪ケースが30分に減る。

**注記**: 本リポジトリの最終形態では、**ダッシュボードはPrometheusのどのメトリクスも参照しない**（第12章の経緯により全てLokiに移行済み）。ゼロから作るならPrometheusコンポーネント自体を省略し、Loki一本で構築することを推奨する。SDKがOTLPで送ってくる標準メトリクス（`claude_code_cost_usage_USD_total`等）は、ログイベント（`api_request`等）が同等以上の情報を持っているため、cumulative counterとして別途保存する価値は薄い。

---

## 8. Loki 設定

`loki-config.yml`:

```yaml
# Loki single-binary (monolithic) configuration
# 個人ローカル可視化用途。HA / replication なし、TSDB + filesystem。

auth_enabled: false                    # マルチテナント無効（X-Scope-OrgID 不要）

server:
  http_listen_port: 3100
  grpc_listen_port: 9095               # コンテナ内のみ（外部公開しない）

common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2025-01-01
      store: tsdb                      # Loki 3.x 推奨
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

storage_config:
  tsdb_shipper:
    active_index_directory: /loki/tsdb-index
    cache_location: /loki/tsdb-cache

# Prometheus と揃えて 30 日 retention。allow_structured_metadata で OTel attributes
# を index 圧迫させずに保持する（label にしない高カーディナリティ属性の受け皿）。
limits_config:
  retention_period: 720h
  ingestion_rate_mb: 8
  ingestion_burst_size_mb: 16
  max_query_series: 5000
  max_entries_limit_per_query: 5000
  allow_structured_metadata: true
  # OTLP attribute → Loki ラベル/structured metadata のマッピング制御。
  # Loki 3.x では Collector 側の loki.resource.labels ヒントは効かず、
  # ここで明示的に index_label に昇格させる必要がある。
  # ドット (.) はラベル名でアンダースコアに自動変換される（service.name → service_name）。
  otlp_config:
    resource_attributes:
      attributes_config:
        - action: index_label
          attributes:
            - workspace
            - work_type
    log_attributes:
      # log_attributes はデフォルトで structured_metadata 行きなので、
      # ラベルにしたいものだけ index_label で明示昇格する。
      # event.name は Claude Code の全イベントが持つ。query_source は main/subagent を識別。
      - action: index_label
        attributes:
          - event.name
          - query_source

# retention_enabled を忘れると retention_period が空振りしてディスクが膨らむ
compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
  compaction_interval: 10m

ruler:
  storage:
    type: local
    local:
      directory: /loki/rules
  rule_path: /loki/rules-temp
  ring:
    kvstore:
      store: inmemory
  enable_api: false

analytics:
  reporting_enabled: false
```

**再実装時の要点（最重要）**:
1. **ラベル昇格はLoki側の設定で行う**。Collector側の `loki.resource.labels` ヒントは Loki 3.x では効かない。`limits_config.otlp_config.resource_attributes.attributes_config` で `index_label` を明示指定する必要がある（[公式ドキュメント](https://grafana.com/docs/loki/latest/send-data/otel/)）
2. **stream labelに昇格させるのは最小限に**（本構成では `service_name`, `workspace`, `work_type`, `event_name`, `query_source` の5つのみ）。`session_id` / `prompt_id` / `tool_name` / `model` は structured metadata に留める。これによりクエリでのフィルタ・グルーピングは可能（`| session_id="..."`）だが、stream系列数は増えない（cardinality爆発防止）
3. `compactor.retention_enabled: true` を忘れるとディスクが際限なく膨らむ
4. OTel attributeは Loki内で `.` → `_` に変換される（`event.name` → `event_name`）。数値attributeも文字列として保持されるが `unwrap` で自動的に数値化される。`| json` parserは不要（OTLP経由で構造化済みのため）

---

## 9. Grafana Provisioning

### 9.1 データソース (`grafana/provisioning/datasources/datasources.yml`)

```yaml
apiVersion: 1

deleteDatasources:
  - name: Prometheus
    orgId: 1
  - name: Loki
    orgId: 1

datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: false
    editable: true

  - name: Loki
    type: loki
    uid: loki
    access: proxy
    url: http://loki:3100
    isDefault: true
    editable: true
    jsonData:
      maxLines: 1000
      timeout: 60
```

`uid` を固定することで、ダッシュボードJSON側のdatasource参照が壊れない。
default datasource は Loki（全ダッシュボードが Loki のみを参照するため。Prometheus は受け皿として残置）。

### 9.2 ダッシュボードprovisioning (`grafana/provisioning/dashboards/dashboards.yml`)

```yaml
apiVersion: 1

providers:
  - name: "Claude Code"
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

### 9.3 認証

```yaml
GF_AUTH_ANONYMOUS_ENABLED: true
GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
```

匿名Viewer権限で読み取り可。Editor/Adminはadmin/admin固定（個人ローカル前提）。

---

## 10. ダッシュボード仕様

両ダッシュボードとも `workspace` というtemplating変数を持つ（Loki `label_values(workspace)` クエリ、複数選択可、`includeAll: true`, `refresh: 2`(time range変更時に再取得)）。

### 10.1 Claude Code Usage (`claude-code.json`)

- **デフォルト表示期間**: `now-24h` / **auto refresh**: なし

#### 全体ビュー

| Panel | Type | Query (LogQL) | Unit / 閾値 |
|---|---|---|---|
| コスト (USD) — 表示期間 | stat | `sum(sum_over_time({event_name="api_request"} \| unwrap cost_usd [$__range]))` | currencyUSD, 閾値なし（期間依存のため） |
| コスト ($/h) — 表示期間平均 | stat | `sum(sum_over_time({event_name="api_request"} \| unwrap cost_usd [$__range])) / ($__range_s / 3600)` | currencyUSD, max=100, 黄$5/橙$20/赤$50 |
| 変更行数 | stat (2値) | `sum(sum_over_time({event_name="lines_changed"} \| unwrap lines_added [$__range]))` / 同 `lines_removed` | short |
| アクティブセッション数 | stat | `count(count by (session_id) (count_over_time({event_name="api_request"} [$__range])))` | short |
| Claude放置率 | stat | 下記「放置率の式」参照 | percentunit, max=1, 黄30%/赤60% |
| Claude起動処理 | stat | `sum(sum_over_time({event_name="api_request"} \| unwrap duration_ms [$__range])) / 1000` | s |
| Claude待機時間 | stat | `sum(sum_over_time({event_name="wait_time_observed"} \| unwrap duration_seconds [$__range])) + sum(sum_over_time({event_name="permission_wait_observed"} \| unwrap duration_seconds [$__range]))` | s |
| ワークスペース別コスト推移 (積み上げ/5分間の増分) | timeseries | `sum by (workspace) (sum_over_time({event_name="api_request"} \| unwrap cost_usd [5m]))` | currencyUSD |
| ワークスペース別context量推移 (5分窓最大値) | timeseries | `max by (workspace) (max_over_time({event_name="api_request"} \| unwrap context_tokens [5m]))` | 閾値: 黄200K/#EAB839:400K/橙600K/濃橙800K/赤1M |
| ワークスペース別コスト (期間内) | piechart | `sum by (workspace) (sum_over_time({event_name="api_request"} \| unwrap cost_usd [$__range]))` | currencyUSD |
| work_type別コスト | piechart | `sum by (work_type) (sum_over_time({event_name="api_request"} \| unwrap cost_usd [$__range]))` | currencyUSD |

#### ワークスペース別ビュー（row区切り「── ワークスペース別 ──」の下、全クエリに `workspace=~"$workspace"` フィルタを追加）

| Panel | Type | Query (LogQL) | Unit / 閾値 |
|---|---|---|---|
| コスト推移 (5分間の増分) | timeseries | `sum(sum_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap cost_usd [5m]))` | currencyUSD |
| コスト (USD) — 表示期間 | stat | 同上 workspace絞り込み版 | currencyUSD |
| コスト ($/h) — 表示期間平均 | stat | 同上 | max=50, 黄$3/橙$10/赤$30 |
| 変更行数 | stat | 同上 | short |
| アクティブセッション数 | stat | 同上 | short |
| Claude放置率 | stat | 同上 | percentunit, max=1, 黄30%/赤60% |
| Claude起動処理 | stat | 同上 | s |
| Claude待機時間 | stat | 同上 | s |
| トークン使用量 | stat | input+output+cache_read+cache_creationの合算 | short, 黄1M/赤10M |
| Permission待ち時間 | stat | `sum(sum_over_time({event_name="permission_wait_observed", workspace=~"$workspace"} \| unwrap duration_seconds [$__range]))` | s |
| モデル別コスト | piechart | `sum by (model) (sum_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap cost_usd [$__range]))` | |
| ソース別コスト | piechart | `sum by (query_source) (...\| unwrap cost_usd [$__range])` | |
| トークン種別内訳 | piechart | input/output/cache_read/cache_creationの4値 | short |
| query_source別起動処理時間 | piechart | `sum by (query_source) (...\| unwrap duration_ms [$__range]) / 1000` | s |
| tool別追加行数 | piechart | `sum by (tool_name) (sum_over_time({event_name="lines_changed", workspace=~"$workspace"} \| unwrap lines_added [$__range]))` | short |
| トークン推移 (5分間) | timeseries | input/output/cache_read/cache_creation を`[5m]`窓で4本 | short |
| 起動処理時間推移 (5分窓 stack) | timeseries | `sum by (query_source) (... \| unwrap duration_ms [5m]) / 1000` | s |

**放置率の式**（LogQLは`clamp_max`非対応なため、分子＝分母の真部分集合となる形で構造的に100%を超えないよう構成）:

```logql
(
  sum(sum_over_time({event_name="wait_time_observed"} | unwrap duration_seconds [$__range]))
  + sum(sum_over_time({event_name="permission_wait_observed"} | unwrap duration_seconds [$__range]))
)
/
(
  sum(sum_over_time({event_name="api_request"} | unwrap duration_ms [$__range])) / 1000
  + sum(sum_over_time({event_name="wait_time_observed"} | unwrap duration_seconds [$__range]))
  + sum(sum_over_time({event_name="permission_wait_observed"} | unwrap duration_seconds [$__range]))
)
```

分母 = 分子 + 起動処理時間、という構造にすることで、比率が数学的に必ず [0, 1] に収まる（クランプ関数に頼らない）。

### 10.2 Claude Code Context Usage (`claude-code-context.json`)

- **デフォルト表示期間**: `now-3h` / **auto refresh**: 30秒
- 「Claude Code Usage」へのリンクパネルあり

| Panel | Type | Query (LogQL) | Unit / 閾値 |
|---|---|---|---|
| 現在の最大context量 | stat | `max(max_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap context_tokens [$__range]))` | short, 黄100K/橙150K/赤180K |
| auto-compact発生回数 | stat | `sum(count_over_time({event_name="compaction", workspace=~"$workspace"} \| trigger="auto" [$__range]))` | short, 黄1/赤5 |
| manual /compact発生回数 | stat | `sum(count_over_time({event_name="compaction", workspace=~"$workspace"} \| trigger="manual" [$__range]))` | short |
| 累計コスト (USD) | stat | `sum(sum_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap cost_usd [$__range]))` | currencyUSD, 黄$1/赤$5 |
| logs送信セッション数 | stat | `count(count by (session_id) (count_over_time({event_name="api_request", workspace=~"$workspace"} [$__range])))` | short |
| セッション別context量推移 | timeseries | `max by (session_id) (max_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap context_tokens [$__interval]))` | 黄100K/橙150K/赤180K同色zone |
| セッション別最大context量 top10 | bargauge | `topk(10, max by (session_id) (max_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap context_tokens [$__range])))` | max=200000, 黄100K/橙150K/赤180K |
| compaction直前のコンテキスト量 top10 | bargauge | `topk(10, max by (session_id) (max_over_time({event_name="compaction", workspace=~"$workspace"} \| unwrap pre_tokens [$__range])))` | 黄100K/赤180K |
| cacheRead比率 | timeseries | `sum(...cache_read_tokens[$__interval]) / (sum(...cache_read_tokens) + sum(...input_tokens) + sum(...cache_creation_tokens))` | percentunit, max=1 |
| compaction duration p95/avg | timeseries | `quantile_over_time(0.95, {event_name="compaction",...} \| unwrap duration_ms [$__interval])` + `avg_over_time(...)` | ms |
| prompt別cost top10 | table | `topk(10, sum by (prompt_id) (sum_over_time({event_name="api_request", workspace=~"$workspace"} \| unwrap cost_usd [$__range])))` | currencyUSD |
| tool_name別呼び出し回数 top15 | table | `topk(15, sum by (tool_name) (count_over_time({event_name="tool_decision", workspace=~"$workspace"} [$__range])))` | short |
| query_source別cacheRead量 | piechart | `sum by (query_source) (sum_over_time(...\| unwrap cache_read_tokens [$__range]))` | |
| model別cost | piechart | `sum by (model) (sum_over_time(...\| unwrap cost_usd [$__range]))` | currencyUSD |
| 直近のcompactionイベント (生ログ) | logs | `{event_name="compaction", workspace=~"$workspace"}` | |
| 🚩 prompt別取り込み/生成比 top10 | table | `topk(10, sum by (prompt_id)(sum_over_time(...\|unwrap cache_creation_tokens[$__range])) / sum by (prompt_id)(sum_over_time(...\|unwrap output_tokens[$__range])))` | 黄5/赤15 |
| 🚩 prompt別turn数 top10 | table | `topk(10, sum by (prompt_id) (count_over_time({event_name="api_request", workspace=~"$workspace"} [$__range])))` | 黄30/赤50 |
| 📈 取り込み/生成比 推移 | timeseries | `sum(sum_over_time(...\|unwrap cache_creation_tokens[1h])) / sum(sum_over_time(...\|unwrap output_tokens[1h]))` | 黄5/赤15 |

**閾値の意味**: 180,000 tokens は Opus系モデルの200,000上限に対する危険水域として設定。モデル変更時は要見直し。

---

## 11. 独自イベント（Claude Code Hooks経由）

Claude Code SDK標準では計測できない指標を、Hooksで**個別イベント**としてLokiに直接送信する（メトリクスとしては送らない — 理由は第12章）。

### 11.1 設計原則（全hook共通、絶対厳守）

1. **常に `exit 0`、stderrに何も出力しない**。特に `Stop` hookで stderr + exit 2 を返すと、Claude Codeに「続行指示」と解釈される仕様があるため絶対に避ける
2. **重い処理は `( ... ) & disown` でバックグラウンド化**し、Claude Codeのレスポンスをブロックしない
3. **session_id別にstate fileを分離** (`${TMPDIR:-/tmp}/claude-wait-tracker/${SESSION_ID}.xxx`)。並列セッション/プロジェクトでの干渉を防止
4. **古いstate fileは24hで自動削除** (`find -mtime +1 -delete`)。「未送信のまま終了」は計上しない設計（思考時間と離席時間を区別できないため）
5. 既存hook（プロジェクト固有のもの含む）と共存できるよう、複数hookが同一イベントに登録されても問題ない設計にする

### 11.2 `wait_time_observed`（待機時間計測）

**課題**: SDK標準の `active_time{type=cli|user}` には「Claudeが応答完了→ユーザーが次プロンプト送信」までの待機時間（思考・閲覧・離席）が含まれない。

**仕組み**:
```
[Stop hook]                [UserPromptSubmit hook]
  │ 完了時刻をstate fileに保存    │ 直前Stopとの差分を計算しLokiへ送信
  ▼                              ▼
wait-time-on-stop.sh       wait-time-on-prompt.sh
                                  │
                                  ▼
                    OTel Collector :4318/v1/logs → Loki
                    event_name="wait_time_observed"
                    attributes: duration_seconds, session_id, workspace, work_type
```

**計上ロジック（重要）**: 「次のプロンプトが送信（確定）されるまで」計上されない。

| シナリオ | 計上 |
|---|---|
| Claude返答 → 5分後にプロンプト送信 | ✅ 5分計上 |
| Claude返答 → そのままセッション放置 → Claude Code終了 | ❌ 計上されない |
| Claude返答 → 何か入力したがEnter押さず離席 | ❌ 計上されない |
| Claude返答 → ESCで中断 → 新プロンプト送信 | ✅ Stop→送信差分が計上 |

**Stop hookスクリプト** (`wait-time-on-stop.sh`):

```bash
#!/bin/bash
# Claude が応答を完了した時刻を記録するだけ。重い処理はしない。
{
  STATE_DIR="${TMPDIR:-/tmp}/claude-wait-tracker"
  mkdir -p "$STATE_DIR" 2>/dev/null
  PAYLOAD=$(cat 2>/dev/null || true)
  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  if [ -n "$SESSION_ID" ]; then
    NOW_NS=$(date +%s%N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1e9))')
    printf '%s' "$NOW_NS" > "$STATE_DIR/${SESSION_ID}.last_stop" 2>/dev/null
  fi
} >/dev/null 2>&1
exit 0
```

**UserPromptSubmit hookスクリプト** (`wait-time-on-prompt.sh`): stdinをforegroundで読み切り、`OTEL_RESOURCE_ATTRIBUTES` から `workspace`/`work_type` を抽出した上で、バックグラウンドサブシェルで差分計算・OTLP HTTP送信・古いstate file削除を行う。送信するOTLP logs JSONペイロードの形:

```json
{
  "resourceLogs": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "claude-code-wait-tracker"}},
        {"key": "workspace", "value": {"stringValue": "<workspace>"}},
        {"key": "work_type", "value": {"stringValue": "<work_type>"}}
      ]
    },
    "scopeLogs": [{
      "scope": {"name": "claude_code_wait_tracker", "version": "0.1.0"},
      "logRecords": [{
        "timeUnixNano": "<now_ns>",
        "observedTimeUnixNano": "<now_ns>",
        "severityNumber": 9,
        "severityText": "INFO",
        "body": {"stringValue": "wait_time_observed"},
        "attributes": [
          {"key": "event.name", "value": {"stringValue": "wait_time_observed"}},
          {"key": "duration_seconds", "value": {"doubleValue": <diff_sec>}},
          {"key": "session_id", "value": {"stringValue": "<session_id>"}},
          {"key": "workspace", "value": {"stringValue": "<workspace>"}},
          {"key": "work_type", "value": {"stringValue": "<work_type>"}}
        ]
      }]
    }]
  }]
}
```

送信先は `curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d "$LOG_JSON" http://localhost:4318/v1/logs`（タイムアウト5秒、失敗しても無視）。

### 11.3 `permission_wait_observed`（permission待ち時間計測）

**課題**: SDK標準の `active_time{type=cli}` には permission確認ダイアログの表示時間も含まれており、「純粋な処理時間」と「ユーザー操作待ち時間」の区別がつかない。

**仕組み**:
```
[Notification: matcher=permission_prompt]    [PostToolUse / Stop]
  │ 表示開始時刻を保存                          │ 差分を計算しLokiへ送信
  ▼                                            ▼
permission-wait-on-notification.sh    permission-wait-on-resolve.sh
                                              │
                                              ▼
                                OTel Collector :4318/v1/logs → Loki
                                event_name="permission_wait_observed"
```

**設計ポイント**:
- Notification matcherで `permission_prompt` だけを対象にする（`idle_prompt` や `auth_success` は無視）
- **PostToolUseとStopの両方**でresolve hookを発火させる（通常はPostToolUseで完結するが、permission拒否やセッション終了時の保険としてStopでも残stateをflush）
- state fileが無ければ即noop（通常のPostToolUseはほぼコストゼロで通過）

**計算式（表示上の補正）**:
```
Claude起動処理 (表示) = Σ(生cli時間) − Σ(permission_wait)
Claude待機時間 (表示) = Σ(生wait時間) + Σ(permission_wait)
```
3つの合計は元の `cli + wait` と同じ（再分配しているだけ）。

**Notification hookスクリプト** (`permission-wait-on-notification.sh`): 時刻記録のみ。`notification_type` が `permission_prompt` の場合のみ `${SESSION_ID}.permission_start` に時刻を保存。

**PostToolUse/Stop hookスクリプト** (`permission-wait-on-resolve.sh`): `permission_start` ファイルが存在すれば差分計算してLokiへ送信、rmで削除。ペイロード構造は11.2と同型（`event.name=permission_wait_observed`）。

### 11.4 `lines_changed`（変更行数計測）

**課題**: SDKの `lines_of_code_count_total` は cumulative counterであり、後述の振動・頭打ち問題を抱える。個別イベント化することで構造的に回避する。

**仕組み**: PostToolUse hook（matcherなし、全toolで発火）で `tool_name` が `Edit`/`Write`/`MultiEdit`/`NotebookEdit` の場合のみ処理。

**行数差分の計算ロジック** (`lines-changed-on-tool-use.sh` 内、python3で実装):
- **Edit**: `old_string`/`new_string` を改行split。`new_lines > old_lines` なら差分を `added`、逆なら `removed`（相殺しない、単純な行数差）
- **MultiEdit**: `edits[]` 配列の各要素に同じロジックを適用して積算
- **Write**: 新規作成扱いとし、`content` の全行数を `added` とする（上書き時も旧内容とのdiffは取らない。SDKの計測ロジックとの乖離は許容）
- **NotebookEdit**: `new_source` の行数を `added` とする（簡易実装）
- **added=0 かつ removed=0** の場合は送信しない（ノイズ削減）
- Bash経由のファイル編集は対象外（既知の制約）

送信イベント: `event_name="lines_changed"`, attributes: `lines_added`, `lines_removed`, `tool_name`, `file_path`, `session_id`, `workspace`, `work_type`

**注意**: SDKの `claude_code_lines_of_code_count_total` とは計算ロジックが異なる（改行splitによる単純line diffのため、SDKのdiffロジックと乖離する可能性がある）。過去データとの厳密比較はできない。

### 11.5 環境変数とインストール

- `CLAUDE_WAIT_OTLP_LOGS_ENDPOINT` (任意): wait/permission hookのOTLP送信先。既定 `http://localhost:4318/v1/logs`
- `CLAUDE_LINES_OTLP_LOGS_ENDPOINT` (任意): lines hook用。既定同上
- `OTEL_RESOURCE_ATTRIBUTES`: `workspace=...,work_type=...` があれば自動でログイベント属性にも付与

**グローバルhooks登録例** (`~/.claude/settings.json`):

```jsonc
{
  "hooks": {
    "Stop": [
      { "hooks": [
        { "type": "command", "command": "/path/to/scripts/wait-time-on-stop.sh" },
        { "type": "command", "command": "/path/to/scripts/permission-wait-on-resolve.sh" }
      ]}
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "/path/to/scripts/wait-time-on-prompt.sh" }] }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [{ "type": "command", "command": "/path/to/scripts/permission-wait-on-notification.sh" }]
      }
    ],
    "PostToolUse": [
      { "hooks": [
        { "type": "command", "command": "/path/to/scripts/permission-wait-on-resolve.sh" },
        { "type": "command", "command": "/path/to/scripts/lines-changed-on-tool-use.sh" }
      ]}
    ]
  }
}
```

プロジェクト側の `.claude/settings.json` に同イベントのhookを書いても、Claude Codeはマージして両方実行する（衝突しない）。設定変更は**Claude Codeを再起動**したセッションから反映。

---

## 12. 重要な設計上の教訓（過去16件のADRの要約）

**このセクションが本ドキュメントの核心である。** ゼロから再構築する際、以下の落とし穴を踏まずに済むよう、教訓を先に共有する。

### 12.1 最重要の結論を先に

> **「cumulative counterを保存し、クエリ時に差分計算する」アーキテクチャは、この用途において構造的に脆弱。最初から「個別イベントとして記録し、`sum`で単純合算する」アーキテクチャ（ログベース）を選ぶべき。**

本プロジェクトは Prometheus + cumulative counter方式から出発し、次々に発覚する問題への対症療法を重ねた末（ADR 003→006→007→008→010→011→012→013→014→015）、最終的に「個別イベント保存」(ADR 016) に全面移行した。**再実装するなら、この移行プロセスをスキップして最初からログベースにするべき**。以下、なぜそうすべきかの根拠を示す。

### 12.2 cumulative counter方式が抱える構造的な脆弱性（一般化した知見）

「累計値を保存し、後からクエリ時に差分計算する」モデルは、以下のエッジケース全てに弱い:

1. **counter reset誤検知**: プロセス再起動で値が0に戻ると、`rate()`/`increase()` が「減少 = リセット」とみなして直前値を加算し、実際の数百〜数千倍の異常値を出す
2. **同label set + 異なるstart_timestampのstream並走**: 同一ラベルの組み合わせを持つ複数のcumulative streamが交互に上書きし合い、値が振動する（例: $48 ⇄ $102 の交互スクレイプ）。これはtemporalityをdeltaにしても解消しない（後述12.9）
3. **ラベル変更によるseries分裂**: あるラベルをdropする等の変更を行うと、新旧series（旧: ラベルあり、新: ラベルなし）が並存し、`min_over_time` 等の「過去を遡る」クエリが片方しか拾えず、長期レンジで値が頭打ちになる
4. **retention切れ**: 古いseriesが消えた瞬間、差分計算の起点が消失しダッシュボードが壊れる
5. **部分リセット**: SDK内部の状態変化やCtrl+C再起動等で、プロセス再起動を伴わずにcounterが部分的に巻き戻ることがある（原因不明）。差分がマイナスになり得る

個別イベント保存（ログベース、`sum_over_time`で単純合算）なら、これら全てが**構造的に発生しない**:
- counterリセットの概念がない（各イベントは絶対値）
- 並走しても単純に合算されるだけ
- ラベル変更しても過去のイベントは過去のラベルのまま集計可能
- retention切れは「古いデータが自然に消える」だけで新しい集計を壊さない
- 部分リセットという概念自体が存在しない

### 12.3 workspace/work_typeのラベル昇格は「選択的に」行う

`resource_to_telemetry_conversion: true` で全resource attributeを一括でmetric/log attributeに昇格させると、`service.instance.id`（起動毎に変わるUUID）等の高カーディナリティ属性まで昇格し、series数が爆発する。**transform processorで意図した属性だけを個別に昇格**させること。未設定時のフォールバック値（`"(unset)"`）を必ず設定する（Prometheusはラベル不在と空文字を同一視し、`label_values()` は明示値しか返さないため、フォールバックがないと選択肢に出てこない）。

### 12.4 高カーディナリティ属性（session_id等）は基盤の性質に応じて扱いを変える

- **メトリクス基盤（Prometheusのようなラベルベースのシステム）に載せる場合**: session_id（UUID、1セッション1値）のような高カーディナリティ属性はseries数を単調増加させ、かつ12.2の並走振動問題の直接原因になる。**drop（削除）してSUM統合**するのが安全策
- **ログ基盤（Lokiのようなイベントベースのシステム）に載せる場合**: session_idはstream labelにせず**structured metadata**として保持すればよい。クエリでのフィルタ・グルーピングに使えるが、stream系列数は増えない。これにより「セッション別の詳細分析」機能を犠牲にせずに済む

つまり、**同じ属性でも「基盤のindex構造に登録するか (label)」と「値を保持するだけか (metadata)」を明確に区別**することが、カーディナリティ問題とセッション別分析機能の両立の鍵。

### 12.5 差分計算のクエリパターンとその限界（Prometheus/PromQL特有だが一般化可能な教訓）

cumulative counterの「期間内の純増分」計算で試行錯誤した順序と、それぞれの限界:

1. `rate()`/`increase()`: counter reset検知が「減少=リセット」前提のため、振動状態（12.2-2）で誤検知し破滅的な異常値を出す。**却下**
2. `<metric> - <metric> offset $__range`: 振動には強いが、「offset時刻にデータがないと空ベクトルになる」弱点があり、新規セッション開始直後に全パネルがNo Dataになる。**却下**
3. `<metric> - min_over_time(<metric>[$__range])`: 期間内最小値を起点にする方式。offsetの弱点を回避しつつ振動にも比較的強い。ただし「同一label setでしか過去を遡れない」制約があり、ラベル変更後のseries分裂問題（12.2-3）には無力。`clamp_min(0)` と併用して部分リセット対策も必要
4. **結論**: PromQLレベルでの工夫には限界があり、根本解決には至らなかった。**最終的にログベースの個別イベント + sum_over_time に全面移行**（12.1）

この経緯を辿らず、**最初からログベースで設計する**のが最も効率的。

### 12.6 派生値の計算はデータ収集層で1回だけ行う

「stat panelとtimeseriesで同じはずの値が食い違う」というバグが2回発生した。共通原因は「複数フィールドの算術演算をダッシュボードのクエリ側で毎回書いていた」こと。例えば `context_tokens = cache_read_tokens + cache_creation_tokens` をパネルごとに `+` で結合すると、集約軸（`by (session_id)` の有無等）によって「異なる時刻・異なるセッションの最大値同士の合計」のような無意味な値になり得る。

**教訓**: 複数フィールドの算術演算が必要な派生指標は、**収集パイプライン（本構成ではOTEL Collectorのtransform processor）で単一フィールドとして事前計算**し、ダッシュボード側は単一フィールドをunwrap/参照するだけにする。DRY原則をデータ収集層で守ることで、パネル間の値の整合性が構造的に保証される。

### 12.7 ダッシュボードには異常値ガードを最初から入れる

「実際には使っていないのに$1,990と表示される」というバグにユーザーが偶然気づくまで発覚しなかった。**3層のガードを最初から設計に組み込む**べき:

1. **stat panelの閾値色変化**: 経験則ベースで黄/橙/赤の閾値を設定し、目視で異常に気づけるようにする
2. **timeseriesのY軸上限固定**: 自動スケールのままだと1点の異常スパイクで他の正常値が「平地」に潰されて見えなくなる。通常運用では超えない値でY軸上限を固定する
3. **比率系はクエリレベルで100%上限を保証**: `clamp_max(x, 1)` が使えるならそれを使う。使えない場合（LogQLなど）は「分子＝分母の真部分集合になる式」を設計し、**数学的に**100%を超えられない構造にする

「異常検知は目視に頼らずアラートで」という発想もあるが、個人ローカル利用では過剰。まず目視ベースのガードを入れ、閾値超過時に「計測バグの可能性を疑う」よう説明文に明記するだけで十分実用的。

### 12.8 長期レンジ検証を初期から行う

「短期レンジ（〜1h）では正しく見えるが、3h以降で頭打ちになる」というバグは、**短期レンジだけでテストしていたため長期間気づかれなかった**。ダッシュボード実装時は必ず 5分・15分・1h・3h・6h・12h・24h・2日 のように**レンジを掃引して値が単調に増加するか検証**すること。「同じ指標を2つの粒度で見比べて食い違いがないか」も併せて確認する（stat panel = instant的な集計、pie chart = range query による集計、というGrafanaの評価方式の違いにも注意。`reduceOptions.calcs` の設定漏れでも同様の乖離が起きる）。

### 12.9 delta temporalityへの変更では振動問題は解決しない（検証済み・重要な反面教師）

「cumulative counterの振動問題は、SDKのtemporalityをdeltaにして送信すれば解決するのでは」という仮説を検証したが、**結論は否定**だった。

- delta送信 → Collectorの`deltatocumulative` processorでcumulativeに変換 → Prometheus保存、という構成を試したが、**振動は解消しなかった**
- 真の原因は「同一label set + 異なるStartTimestampを持つ複数のcumulative streamが並走し、後から書いた値で上書きし合う」ことであり、temporalityの種類（delta/cumulative）とは無関係。delta入力もCollector内部で結局cumulative化されるため、同じ並走問題を再現する
- **教訓**: 「保存形式を累積値（cumulative）にする」こと自体が問題の本質であり、送信時のtemporalityを変えても解決しない。解決には**保存形式そのものを個別イベントに変える**必要がある（= 12.1の結論）

ただし、「delta（=直前からの増分を独立値として送る）」という発想自体は「個別イベントとして保存する」という最終形と本質的に近い。**保存先をPrometheus（累積値ストア）に固定したままdeltaを試すのが誤りであり、「個別イベント + ログストア」の組み合わせが正解**、という整理がこの検証から得られた。

### 12.10 cumulative counterの部分リセット対策

原因不明だが、プロセス再起動を伴わずにcumulative counterが部分的に巻き戻る事象が観測された（Ctrl+C再起動、session resume、SDK内部状態変化等が疑われるが未解明）。差分計算で負値が出ると「コストがマイナス」等の意味不明な表示になる。**個別seriesの差分計算結果を`clamp_min(0)`で必ずラップ**すること（`sum`の外側ではなく内側でラップし、リセットしたseriesだけを0扱いにして他のseriesの純増分を殺さないようにする）。ログベースの個別イベント方式ではこの種のリセット概念自体が存在しないため、この対策も不要になる。

### 12.11 分析の高度化: 「重い」だけでなく「質が悪い」prompt/sessionを検出する

コスト・時間・トークン量のような量的指標だけでは「無駄なcontext消費」を見逃す。本プロジェクトでは以下の3指標を追加してcontext品質を可視化した:

1. **Read重複率** = `1 - unique_files_read / total_files_read`。同じファイルを何度も読んでいれば汚染シグナル（閾値目安: >40%、実データのp80相当）
2. **取り込み/生成比** = `cache_creation_tokens / output_tokens`（ダッシュボードのLogQL版）または `tool_result_chars / output_chars`（transcript解析版）。読んだ量に対して生成した量が少ないほど非効率（閾値目安: >15:1、p90相当）
3. **turn数** = 1つのuser promptあたりのAPI呼び出し回数。多いほど1プロンプトでcontextを膨らませすぎ（閾値目安: >50、p90相当）

判定ロジック: 2つ以上該当で「🔴汚染」、1つで「🟡注意」、0で「🟢妥当」。**閾値は実運用データの分布（p80〜p90）から事後的に調整する**方式を推奨する（最初から絶対値を決め打ちしない）。

この分析は (a) Grafanaダッシュボードのtable/timeseriesパネル（LogQLで完結、`prompt_id`単位）と (b) ローカルのtranscript(jsonl)を直接解析するPythonスクリプト（Claude Codeのセッション記録から `parentUuid` を辿って `promptId` を復元し、tool呼び出し内容まで踏み込んで分析）の**2段構え**で実装した。前者は「気づくためのダッシュボード」、後者は「原因を掘り下げるための詳細分析ツール」という役割分担。

分析スクリプトの主要ロジック:
- Lokiから `sum by (prompt_id) (sum_over_time({event_name="api_request"} | unwrap cost_usd [Nh]))` で高コストprompt上位を取得
- `~/.claude/projects/*/*.jsonl` のtranscriptを全走査し、`session_id` → records の辞書を構築
- 各recordの `parentUuid` を再帰的に辿って `promptId` を特定（`promptId` が直接ないrecordは親を遡って解決）
- 対象prompt_idに属するrecordのみ抽出し、`tool_use`/`tool_result`のcontentから文字数・ファイルパス・turn数を集計
- 3指標を計算し、閾値判定して改善提案（例: 「Read済みファイルをTaskCreateにメモして再Readを防ぐ」「Explore/Planサブエージェントへ調査を委譲してmainのcontextを温存」「プロンプトをフェーズで分割」）を出力
- `--by-command` オプションでslash command別の集計（どのスキルがcontextを汚染しやすいか）も可能

### 12.12 「Prometheusデータの完全リセット」という運用手段も選択肢に持つ

series分裂によるダッシュボード破綻がPromQLレベルで修復不能と判明した場合、**Prometheusのvolumeだけを削除して再起動する**という運用対応も有効な選択肢（Loki/Grafana/OTEL Collectorのvolumeには触らない）。

```bash
docker compose stop prometheus
docker compose rm -f prometheus
docker volume rm <project>_prometheus-data
docker compose up -d prometheus
```

過去のメトリクス履歴（30日分）を失うトレードオフはあるが、「30日間ダッシュボードが壊れたまま運用する」よりは現実的。**この判断が必要になること自体が、cumulative counter方式の根本的な脆さを示すシグナル**であり、最終的にはログベース方式へ移行して再発を防ぐのが望ましい（12.1）。

### 12.13 電源断・予期せぬシャットダウンへの耐性

ローカル環境（個人PC）でDockerを動かす場合、**スリープ・再起動・電源断でdaemonが突然停止する**ことを前提に設計する。

- Prometheusはデフォルトでブロック確定間隔が2時間、WAL圧縮も無効。**`min-block-duration`/`max-block-duration`を30分に短縮し、`wal-compression`を有効化**することで最悪データロス幅を2時間→30分に圧縮できる
- 全サービスに `restart: unless-stopped` を設定し、Docker daemon復帰時に自動的にコンテナが立ち上がるようにする

---

## 13. 分析スクリプト: context-pollution-analyzer.py

`scripts/context-pollution-analyzer.py` として実装。使い方:

```bash
# 過去24h, top10 (デフォルト)
./context-pollution-analyzer.py

# 期間と件数を指定
./context-pollution-analyzer.py --hours 168 --top 20

# Slash command別の集計を末尾に追加
./context-pollution-analyzer.py --hours 168 --by-command

# JSON出力 (自動化用)
./context-pollution-analyzer.py --json | jq '.[] | select(.metrics.read_dup_rate > 0.5)'
```

前提: Lokiが `localhost:3100` で稼働、Claude Code transcriptが `~/.claude/projects/` にあること。詳細アルゴリズムは12.11参照。

---

## 14. UIとアクセス

| サービス | URL |
|---|---|
| Grafana | http://localhost:3033 (admin/admin, viewer권限は認証不要) |
| Prometheus | http://localhost:9090 |
| Loki | http://localhost:3100 |

> ⚠️ **ローカル開発専用**: Grafanaが `admin/admin` 固定 + anonymous viewer有効、各サービスが `127.0.0.1` バインド前提。**外部公開・共有環境では絶対に使用しないこと**。

---

## 15. 再構築時の推奨実装順序

ゼロから再実装する場合、以下の順序を推奨する（本プロジェクトの試行錯誤を踏まえた最短経路）。

1. **アーキテクチャ選定を12.1の結論に従って決定する**: cumulative counter保存 + 差分計算方式は選ばず、**個別イベントをログストア（Loki等）に保存し `sum_over_time` 的な単純合算で集計する方式を最初から採用する**。Prometheusはオプション（SDKの標準メトリクスを一応受けたい場合のみ）とし、ダッシュボードの主戦力にはしない
2. **Docker Compose基盤を組む**: OTEL Collector (contrib版) + Loki + Grafana（+ 任意でPrometheus）。全てlocalhost限定bindにする
3. **OTEL Collectorのtransform processorでラベル整形**: workspace/work_typeを選択的に昇格、フォールバック値`"(unset)"`を設定、高カーディナリティ属性(session_id等)はメトリクス系なら削除・ログ系ならstructured metadataのまま保持
4. **Lokiのラベル昇格設定**: `otlp_config.resource_attributes` / `log_attributes` で必要最小限（5個程度）だけをindex_labelに昇格。それ以外はstructured metadataへ
5. **Claude Code側のtelemetry設定を有効化**し、まず素のOTLPイベント（`api_request`等）がLokiに届くことを確認する
6. **workspace判定用のshell wrapperを実装**（環境変数はプロセス起動時に必要という制約に注意）
7. **SDKが出さない指標（待機時間・permission待ち時間・変更行数）をhookで個別イベントとして送信**する仕組みを実装（11章）。design原則（11.1）を厳守
8. **ダッシュボードのパネルをLogQLで実装**（10章のクエリ一覧を参照）。実装したら**必ず短期〜長期のレンジを掃引して値が単調増加するか検証**（12.8）
9. **異常値ガードを最初から組み込む**（12.7）: 閾値色分け、Y軸上限、比率の数学的100%キャップ
10. **context品質分析（12.11）を追加**: ダッシュボードパネル + 詳細分析スクリプトの2段構え
11. 変更を加えるたびに、**「なぜその設計にしたか」をADR形式で記録**する運用を回す（O-06/O-07）。今回のように後から一括で知見を集約するのではなく、意思決定の都度小さく記録するほうが情報が失われにくい

---

## 16. コスト最適化サイクル（設計仕様v1の実装）

第1〜15章の観測基盤の上に、「Claude Code のコストを**客観的に最適化する**」ためのサイクルを
実装した層。既存の Usage / Context ダッシュボードが量的指標の**観測**に閉じていたのに対し、
本章は「観測 → 診断 → 施策 → 検証」を回すための新イベント・派生値・専用ダッシュボード・
分析スクリプト・検証手法を定義する。名前・スキーマの契約は `docs/CONTRACT.md`、個々の設計判断は
`docs/adr/0001`〜`0005` を参照（本章末にポインタ）。

### 16.1 Inform / Optimize / Operate の3フェーズ

- **Inform（観測・診断）**: どこにコストが集中しているかを寄与度分解で特定する。単位コスト
  （CPSO、コスト/1k実効トークン）で「安さ」と「成果」を分離して見る。
- **Optimize（施策立案・実行）**: 寄与度上位に対しキャッシュ導入・モデル切替・context 削減等の
  施策を打ち、その瞬間に `intervention_marker` で時刻を記録する。
- **Operate（検証・定着）**: 施策マーカーを起点に before/after を準実験（ITSA / 管理図 /
  変化点検出）で照合し、効いた施策を定着させ効かない施策を捨てる。

### 16.2 新イベント2種（ログベース個別イベント）

第11章のログベース方式（ADR 0001）にそのまま載せる。詳細スキーマは CONTRACT §3.2 / §3.3。

- **`task_outcome`**（成果 = CPSO の分母）: `outcome` = `completed`/`success`/`failure`/
  `abandoned`。Stop hook (`scripts/task-outcome-on-stop.sh`) が `completed`
  （`outcome_proxy=stop_reached`、Stop 到達の弱いプロキシ）を自動送信し、CLI
  (`scripts/task-outcome.sh <success|failure|abandoned> [note]`) で人間が上書き記録する
  （`outcome_proxy=manual`）。**限界**: プロキシは真の品質劣化を取りこぼす（説明文に明記）。
- **`intervention_marker`**（before/after 検証の起点）: `intervention_id=iv-<epoch>`、
  `category` = `caching`/`model_switch`/`context_reduction`/`compaction_tuning`/`prompt_edit`/
  `subagent_policy`/`other`、`description`/`scope`。CLI
  (`scripts/intervention-marker.sh --category ... --desc "..." [--scope "..."]`) で記録し、
  Grafana annotation として Cost ダッシュボード全パネルに縦線を重畳する。

### 16.3 effective_tokens（実効トークン）

価格ウェイトで正規化した「効率比の共通分母」。`effective_tokens = input*1.0 + cache_read*0.1
+ cache_creation*1.25 + output*5.0`。DRY のため **OTEL Collector 側で1回だけ事前計算**し、
ダッシュボード/スクリプトは単一フィールドを参照するだけにする（12.6 の教訓、CONTRACT §5、
ADR 0002）。単位コスト「コスト/1k実効トークン」の分母になる。

### 16.4 pricing SSOT とコスト再計算

`cost_usd` は SDK 推定値のため、`pricing/pricing.yaml` を唯一の価格表（モデルID × 発効日）とし、
唯一のローダ `analysis/pricing.py` が `cost_recomputed` を自前計算して乖離を検証する。cache 係数・
request 修飾子・トークナイザ世代差補正（`new_vs_old_ratio: 1.3` ≒ +30%）を集約し、Sonnet5 の
2026-09 値上げは発効日で解決する。**価格は best-effort（2026-07 調査値）。利用前に必ず公式
pricing ページと突合すること**（CONTRACT §7、ADR 0004）。

### 16.5 Cost Optimization ダッシュボード

`grafana/provisioning/dashboards/claude-code-cost.json`（uid `claude-code-cost`、既定 now-7d、
Loki datasource `uid: loki`）。主なパネル: 総コスト / 完了アウトカム数(proxy) /
成果あたりコスト(CPSO) / コスト/1k実効トークン / キャッシュ有効率 / 単位コスト分布 p50/p90/p95 /
バーンレートと月次着地予測 / 日次コスト推移（施策マーカーを縦線で重畳）/ 管理図近似 /
prompt・work_type・model・query_source 別コスト寄与 / 実効トークン vs 生トークン /
直近の施策マーカー・task_outcome の生ログ。

**LogQL の落とし穴（実機検証で確認済み）**: `quantile_over_time` / `avg_over_time` 等のunwrapped range aggregation は、グルーピング（`by (...)` / `by ()`）を付けないと stream（さらに structured metadata の組）単位に系列が分裂し、「全体の分位点」にならない（実質1リクエスト=1系列となり `max_query_series` 超過も起こす）。全体集約には `... by ()`、workspace 単位には `... by (workspace)` を必ず付けること（10.2 の compaction p95/avg も同様に `by ()` を適用済み）。

### 16.6 分析スクリプト

`analysis/cost-optimization-analyzer.py`（Loki `localhost:3100` から集計、`--hours` / `--top` /
`--json` / `--loki`）。出力: (a) CPSO、(b) パレート寄与度分解（prompt_id / work_type 別、
累積80%までハイライト、今期 vs 前期の増分）、(c) ベースライン percentile p50/p90/p95（prompt_id ごとの期間合計コストの分布。ダッシュボードの「api_request単位コスト分布」= リクエスト1件あたり分位点とは集計単位が異なる）、(d) 変化点検出
（CUSUM 内蔵、`ruptures` があれば PELT 併用）、(e) RICE スコア表の雛形。Loki 不在でも import 時に
エラーにしない設計。context 品質は第13章の `context-pollution-analyzer.py` と役割分担する。

### 16.7 検証手法（準実験）

利用者は**個人ひとり**（1章）のため A/B 不能。中断時系列に成立する準実験に限定する（ADR 0005）:
**ITSA**（施策マーカーを中断点に前後の水準/傾きを見る）、**I-MR 管理図**（日次コスト/単位コストの
管理限界超過を検出、ダッシュボード「管理図近似」）、**CUSUM/PELT**（分析スクリプトの変化点検出）。
**A/B・DiD・重量級ベイズ・p値追跡・即時アラート基盤は採らない**（単一ユーザーで不成立、または
個人ローカル利用に過剰、12.7 と整合）。準実験は因果を厳密には証明せず交絡を排除できない限界がある。

### 16.8 優先度マップ（施策の選び方）

寄与度分解（16.6-b）で「累積コストの80%を占める少数の prompt_id / work_type」を特定し、そこへ
施策を集中する（パレート）。各候補は分析スクリプトが出す **RICE 雛形**（Reach / Impact /
Confidence / Effort）でスコアリングし、高 Impact × 低 Effort から着手する。施策実行時に
`intervention_marker` を打ち、16.7 の準実験で効果を検証して定着 or 破棄を判断する。

### 16.9 ADR ポインタ

- `docs/adr/0001-log-based-event-architecture.md` — cumulative counter を採らずログベース個別
  イベント方式（12.1/12.2 の踏襲）。
- `docs/adr/0002-effective-tokens-in-collector.md` — effective_tokens を Collector で1回だけ
  事前計算（12.6 DRY、CONTRACT §5）。
- `docs/adr/0003-outcome-signal-and-intervention-marker.md` — task_outcome / intervention_marker
  の新設とプロキシの限界。
- `docs/adr/0004-pricing-ssot-and-cost-recompute.md` — pricing.yaml を SSOT にコスト再計算、
  best-effort 価格の運用注意。
- `docs/adr/0005-objective-verification-methods.md` — ITSA + 管理図 + CUSUM/PELT の準実験に限定。