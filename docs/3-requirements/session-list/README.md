# Session List ページ要件

dashboard uid: `claude-code-session-list` / 既定レンジ: now-7d / 生成器: `scripts/gen-session-list.py`

## 背景・目的

エンティティ階層 **Workspace → Session → Prompt → Api Request** の「Session を選ぶ」層。
filtered-Today（workspace 絞り込み）から「▸ Session List」ボタンで着地し、その workspace の
セッションを一覧・ソート・検索して、深掘り対象の1セッションを選んで Session Detail へ渡す。

## 設計思想

- **List に徹する**: 分析はしない。選ぶための表（コスト降順・列ソート・session_id 検索）に限定
- **クエリは Context「セッション一覧」を流用**（`sum by (session_id)` のコスト/最大context/
  リクエスト数）。Context 改修で当該パネルが変わったら生成器を再実行
- **7d 既定**: セッションは複数日にまたがるため、当日より広めに一覧する
- 変数は `workspace` のみ（session は選ぶ対象なので変数にしない）

## パネル

- **セッション数（期間内）**: 一覧の分母
- **セッション一覧**: session_id / コスト / 最大context / リクエスト数（列ヘッダでソート、
  session_id 列は検索フィルタ可）。**行クリックで Session Detail
  （`claude-code-session`・`var-workspace` + `var-session_id` 付き）へ遷移**

## 入口 / 出口

- 入口: 全画面共通ナビの「Sessions」ボタン（`includeVars` で現 workspace を引き継ぐ。正本 `scripts/apply-nav-links.py`）
- 出口: 行クリック → [Session Detail](../session-detail/README.md)
- 関連: 数式・uid の正本 [../../CONTRACT.md](../../CONTRACT.md) §8
