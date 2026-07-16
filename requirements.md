# Claude Code OTEL Monitoring — 再構築仕様書 (requirements.md)

このドキュメントは、本リポジトリと**同等の機能をゼロから再現性高く再構築する**ために必要な要件・設計・実装知見を1ファイルに集約したものである。

対象読者は「このリポジトリの過去のファイルを一切参照できない別のAI/エンジニア」。したがって:
- 単なる「何を作るか」ではなく「どう作るべきか」「なぜそう作るのか」「何を避けるべきか」まで含める
- 過去に踏んだ失敗（§12 の教訓16件と、それを支える ADR 6件）を要約し、**同じ轍を踏まないための情報**を優先的に残す
- 実装方式（Prometheus / Loki / Grafana / OTEL Collector）は本リポジトリで採用した具体解だが、別の技術スタックで再実装する場合も本質的な要件（第1〜2章）は変わらない

> ⚠️ **正典は `docs/` 側。本ファイルは旧モノリスで、詳細仕様は陳腐化している。**
> このファイルは `docs/` の3層構造（L1 `docs/1-references/` 観測事実 / L2 `docs/CONTRACT.md`・`docs/2-pipelines/`・`docs/adr/` / L3 `docs/3-requirements/` 表示要件）が抽出される前の一枚岩である。
> - **本文として残すのは §0-2（目的・背景・用語）・§12（過去の教訓）・§15（再構築順序）** のみ。
> - **§3-11 / §13-14 / §16 は docs/ へのポインタに縮約済み**（旧本文は git 履歴に残る）。正典は次の通り:
>   - ダッシュボード表示要件 → [`docs/3-requirements/`](docs/3-requirements/README.md)
>   - 名前・スキーマ・集計規約 → [`docs/CONTRACT.md`](docs/CONTRACT.md)
>   - 観測事実（イベント/属性/model/query_source） → [`docs/1-references/`](docs/1-references/README.md)
>   - パイプライン（集計/派生/hook/pricing/storage） → [`docs/2-pipelines/`](docs/2-pipelines/README.md)
>   - 設計判断 → [`docs/adr/`](docs/adr/)
>   - 各設定の実体は `docker-compose.yml` / `otel-collector-config.yml` / `loki-config.yml` / `prometheus.yml` / `grafana/provisioning/`（本文中のコピーではなくファイル本体が正）。
>
> 本ファイルと docs/ が食い違う箇所は **docs/ を優先**する。

---

## 0. エグゼクティブサマリー

**何を作るか**: Claude Code (Anthropic の CLI コーディングエージェント) が OpenTelemetry で export する metrics / logs をローカルに集約し、Grafana ダッシュボードで「コスト・時間配分・コンテキスト消費・セッション活動」を可視化するローカル観測基盤。

**最終的な技術構成（本リポジトリの到達点）**:
- **収集**: OpenTelemetry Collector (contrib版) が Claude Code から OTLP (gRPC/HTTP) を受信
- **保存**: **ダッシュボードで使う値は全て Loki (ログ) ベース**。Prometheus は SDK 標準メトリクスの受け皿として稼働だけしているが、ダッシュボードからは参照していない（歴史的経緯があり後述）
- **可視化**: Grafana、利用量・コスト・コンテキスト分析など9枚のダッシュボード
- **独自計測**: Claude Code Hooks (Stop / UserPromptSubmit / Notification / PostToolUse) を使い、SDK が出さない「待機時間」「permission待ち時間」「変更行数」を独自イベントとして Loki に送信

**最重要の設計原則（先に知っておくべきこと）**:
> **「累積値 (cumulative counter) を保存して後からクエリ時に差分計算する」アーキテクチャは、この用途では構造的に破綻する。**
> 理由は第12章で詳述するが、結論として **「個別イベントを記録し、`sum_over_time` で単純合算する」方式（ログベース）を最初から採用すべき**。本プロジェクトは cumulative counter 方式 (Prometheus) から個別イベント方式 (Loki) へ段階移行する過程で §12 の教訓16件と ADR 6件を積み上げた。ゼロから作るなら、最初からログベースのイベント記録方式を選び、この移行コストを払わずに済ませられる。

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
| **ワークスペース (workspace)** | 1つのチケット作業に対応する作業ディレクトリ単位。例: `PROJ-1234` |
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

→ 機能要件・ダッシュボード表示要件は [`docs/3-requirements/`](docs/3-requirements/README.md)、共通スキーマ・集計規約は [`docs/CONTRACT.md`](docs/CONTRACT.md) が正典。

## 4. システム全体構成（実装ベースライン）

→ システム全体構成・データフロー・永続化は実体ファイル（`docker-compose.yml` / `loki-config.yml` / `prometheus.yml`）と [`docs/2-pipelines/storage.md`](docs/2-pipelines/storage.md)、[ADR 0001](docs/adr/0001-log-based-event-architecture.md) が正典。

## 5. Claude Code側の設定

→ Claude Code 側の設定は [`README` セットアップ](README.md)＋[`settings/settings.example.json`](settings/settings.example.json)、workspace 判定の解決順序は [`docs/CONTRACT.md` §6](docs/CONTRACT.md) が正典。

## 6. OTEL Collector 設定

→ Collector 設定の実体は [`otel-collector-config.yml`](otel-collector-config.yml)。派生値・cost_recalc の設計は [`docs/2-pipelines/derivations.md`](docs/2-pipelines/derivations.md)、[ADR 0002](docs/adr/0002-effective-tokens-in-collector.md) / [ADR 0006](docs/adr/0006-cost-recalc-in-collector.md)。

## 7. Prometheus 設定（SDK標準メトリクスの受け皿として残置）

→ Prometheus 設定の実体は [`prometheus.yml`](prometheus.yml)（SDK標準メトリクスの受け皿・ダッシュボード未参照）。

## 8. Loki 設定

→ Loki 設定の実体は [`loki-config.yml`](loki-config.yml)。retention/limits の解説は [`docs/2-pipelines/storage.md`](docs/2-pipelines/storage.md)。

## 9. Grafana Provisioning

→ Grafana provisioning の実体は [`grafana/provisioning/`](grafana/provisioning/)（datasources / dashboards）。

## 10. ダッシュボード仕様

→ ダッシュボード仕様は画面ごとに [`docs/3-requirements/`](docs/3-requirements/README.md) が正典。

## 11. 独自イベント（Claude Code Hooks経由）

→ 独自イベント（Hooks）の設計原則・定義は [`docs/2-pipelines/hook-events.md`](docs/2-pipelines/hook-events.md) と `scripts/*.sh`、共通契約は [`docs/CONTRACT.md`](docs/CONTRACT.md) が正典。

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

→ 分析スクリプトは [`analysis/cost-optimization-analyzer.py`](analysis/cost-optimization-analyzer.py)（旧 context-pollution-analyzer は統合済み）。使い方は [`docs/RUNBOOK.md`](docs/RUNBOOK.md)。

## 14. UIとアクセス

→ UI・アクセスは [`README` の Grafana を開く](README.md) を参照。

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

→ コスト最適化サイクルの仕様は [`docs/3-requirements/cost-optimization/`](docs/3-requirements/cost-optimization/README.md)、設計判断は [ADR 0003](docs/adr/0003-outcome-signal-and-intervention-marker.md) / [ADR 0005](docs/adr/0005-objective-verification-methods.md) が正典。
