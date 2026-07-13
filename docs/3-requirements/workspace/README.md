# Workspace / Session（Today 絞り込み）ページ要件

dashboard uid: `claude-code-workspace` / 時間: Today と同一（各パネル timeFrom 固定・picker 非表示）/ refresh: 30s

## 背景 — なぜこのページがあるのか

「このタスク（workspace）／このセッションだけ、Today の視点で見たい」に応える。
当初は workspace 専用の bespoke ダッシュボードを用意したが、**専用ページは
「当日の把握」「通算の分析」「診断」など複数の用途が混ざって複雑化するリスク**があった
（2026-07-13 見直し）。そこで方針を転換し、**Today と同一のコンテンツを workspace / session
変数で絞り込むだけの画面**にした。コンテンツ定義は Today 1つに集約され、この画面の目的は
「Today を、指定タスク／セッションに絞って見る」の1点に固定される。

## 目的

1. **タスク／セッション単位の当日把握**: Today の各パネル（本日コスト・背景比率・キャッシュ・
   累積コスト・バーン・query_source構成・直近プロンプト）を、選択した workspace / session に
   絞って読む
2. **絞り込みの受け皿**: Today の workspace別サマリ行リンクから `var-workspace` 付きで着地。
   セッション別context推移の線クリックで `var-session_id` を立てて同じ画面をセッションに絞る

## 設計思想

- **Today の完全なクローン + 2変数**: パネル・レイアウト・timeFrom・色判定は Today と同一。
  差分は (a) 全クエリに `workspace=~"$workspace"` と `| session_id=~"$session_id"` を付与、
  (b) `workspace`（query・複数選択・includeAll）と `session_id`（textbox・既定 `.*`）の変数追加のみ
- **用途を混ぜない**: 「通算の分析」は Cost Opt、「持ち方の診断」は Context の管轄。ここは
  あくまで **Today の当日視点をタスク/セッションに絞るだけ**。パネルの新規追加はしない
  （Today に追加され次第、絞り込み版にも自動で載る運用）
- **Today と定義がズレない**: パネルは Today と同一クエリ（スコープ句を機械的に付与しただけ）。
  Today の改修はこの画面へ scope 変換で反映する（生成器 `scripts/gen-workspace-filtered-today.py`）
- **時間は Today と同じ「本日」**: 各パネルは timeFrom（now/d・3h・12h）固定で picker 非表示。
  workspace/session の絞り込みは時間ではなく変数で行う

## Today との差分（scoped 化の例外）

| パネル | Today | この画面 |
|---|---|---|
| 本日の累積コスト | vs 同曜日平均・上限 **$150**（グローバル予算） | **累積曲線のみ**（$150 と同曜日平均は削除。予算はタスク単体では無意味なため。Today/Cost Opt の管轄） |
| セッション別context推移 | 線クリック → Context（セッション診断） | 線クリック → **この画面を当該 session に絞る**（`var-session_id`） |
| 本日のworkspace別サマリ | 全 workspace の一覧 | 絞り込み中の workspace 行（＝現在地）。行リンクで別 workspace へ切替 |
| 施策マーカー annotation | 全 workspace | `workspace=~"$workspace"` で絞る |

その他のパネル（本日コスト / 背景コスト比率 / キャッシュ有効率 / 直近プロンプト /
query_source別構成 / $/5分バーン）は Today と同一で、workspace / session 絞り込みのみ効く。

## 入口

- **Today → 本日のworkspace別サマリ** の行リンク（`var-workspace` + `from=now/d&to=now`）
- **Today → セッション別context推移** の線クリック（`var-session_id` + `var-workspace` +
  本日レンジ。session 単位で着地）
- **この画面内 → セッション別context推移** の線クリック（`var-session_id` を追加して自己絞り込み）
- 変数バーの `workspace` / `session_id` を直接操作

## 出口

- ヘッダの共通ナビ **「Sessions」ボタン**（`includeVars` で現 workspace を引き継ぐ）
  → [Session List](../session-list/README.md) → 行クリックで [Session Detail](../session-detail/README.md)
- 共通ナビ **Usage / Cost Optimization / Sessions / Prompts** の4ボタンは
  **workspace/session で絞り込んだドリルページ（filtered-Today / Session List / Session Detail）
  のみ**に表示。Today などベース／全体ページには出さない（正本 `scripts/apply-nav-links.py`）

## 関連

- コンテンツの正本: [today/](../today/README.md)（このページは Today のクローン）
- 通算の分析: [cost-optimization/](../cost-optimization/README.md) / 持ち方の診断:
  [context-usage/](../context-usage/README.md)
- 数式・uid の正本: [../../CONTRACT.md](../../CONTRACT.md) §8
