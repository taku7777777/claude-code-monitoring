# ダッシュボード要件 — index

各ページ・各セクション・各パネルの「表示している値（定義）」と「活用（実現したい効果・
アクション・分析・判断）」を、パネル単位の要件として管理する。存在理由が失われて
形骸化するのを防ぐためのドキュメント群。

- **層の位置づけ**: 本ディレクトリ `3-requirements/` は **L3＝表示**（何を表示し・どう活用するか）。
  上流の **L1＝観測可能な事実**は [../1-references/](../1-references/README.md)、**L2＝蓄積・加工**は
  [../2-pipelines/](../2-pipelines/README.md)。3層モデルの全体像は [2-pipelines/README](../2-pipelines/README.md) 参照
- 数式・スキーマの正本: [../CONTRACT.md](../CONTRACT.md) / 運用手順: [../RUNBOOK.md](../RUNBOOK.md)
- 判断例: [../CASEBOOK.md](../CASEBOOK.md) / ユースケース: [../USECASE.md](../USECASE.md)
- 記載規約: [TEMPLATE.md](TEMPLATE.md)（セクションファイルの雛形と記載規約。
  見出し=簡潔なパネル名 / 紐付けはパネルID / 表示・活用の箇条書き形式）

## 構成

| ページ | 役割 | レンジ | 頻度 | 要件 |
|---|---|---|---|---|
| Today | 進行中の資源配分・当日検知（常時・pull型） | now/d・1h・now/w・now/M 固定 | 日中随時（5秒） | [today/](today/README.md)（セクション別 4ファイル） |
| Cost Optimization | 意思決定・効果検証（Optimize/Operate） | 7d | 週1回15分 | [cost-optimization/](cost-optimization/README.md)（セクション別 7ファイル） |
| Usage | 利用量の記述（Inform — 何に・どれだけ使ったか） | 24h | 毎日2分＋Today からのドリル先 | [usage/](usage/README.md)（2026-07-14 量に純化・単一ファイル） |
| Workspace / Session（Today絞り込み） | Today を workspace/session に絞って見る（当日視点） | Today同一 | 事象駆動（Today からリンク遷移） | [workspace/](workspace/README.md) |
| Session List | workspace のセッション一覧（選ぶ・ソート・検索） | 7d | 事象駆動（filtered-Today のボタン） | [session-list/](session-list/README.md) |
| Session Detail | Usage を1セッションに絞った詳細 | 24h | 事象駆動（Session List 行クリック） | [session-detail/](session-detail/README.md) |
| Context（診断ドリルダウン） | workspace → session の深掘り（当面並行維持） | 3h〜当日 | 事象駆動（リンク遷移） | [context-usage/](context-usage/README.md)（セクション別 3ファイル） |
| プロンプト一覧 | 時間窓ドリルダウン（この時間帯に何が走っていたか） | 任意（Today バークリックで5分窓） | 事象駆動（バークリック遷移） | [prompt-list/](prompt-list/README.md) |
| Prompt明細 | 1往復単位のドリルダウン（因果の確認） | 24h | 随時（行リンク遷移） | [prompt-detail/](prompt-detail/README.md)（セクション別 4ファイル） |

## 共通の型

**検知 → ベースライン比較 → 帰属 → 明細で因果確認 → 施策はマーカーを打って1本ずつ検証**。

## 用語解釈（全ページ共通の前提）

- **プロンプト完遂数** = 応答完了（Stop）まで完走したプロンプトの数。送信数より少ない
  （追い打ち送信・中断・ローカルコマンドを数えない）。実測でコスト発生 prompt 数とほぼ一致
- **prompt 単位** = 「依頼の仕方」の値段 / **APIリクエスト単位** = 「セッションの持ち方
  （contextの重さ）」の値段。対処が違うため分布を分けて持つ
- **キャッシュ原則** = リクエスト先頭からの完全一致（prefix）に効く。TTL 5分。
  下げる行動は「途切れさせる」系（放置・/clear・設定変更・モデル切替・compaction）

---

# 検討中の拡張（未実装・方向性は合意済み）

**イベント帰属分析（analyzer [i] 構想）**: コストを「平常やりとり」と「イベント税」に分解する。

- 平常状態の2分類: ①通常セッション継続（repl_main_thread） ②サブエージェント（agent:*）
- イベント3種:
  - a. 放置→再開（wait_time_observed > 300s ＝ TTL失効の払い直し。実測 23回/日）
  - b. モデル切替（同一 session 内の model 遷移として検出。analyzer 向き）
  - c. compaction（イベント記録済み。直後の書き直しコストを定量）
- 狙い: a/b/c を除いた「**素の使い方の単価**」で依頼の仕方を評価し、a/b/c は
  「**実行時の影響が期待通りか**」を個別評価する。イベント税が大きければセッション運用の
  施策、素の単価が高ければ依頼の仕方の施策、と処方箋が自動で分かれる。
