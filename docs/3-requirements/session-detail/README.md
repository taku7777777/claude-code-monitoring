# Session Detail ページ要件

dashboard uid: `claude-code-session` / 既定レンジ: now-24h / 生成器: `scripts/gen-session-detail.py`

## 背景・目的

エンティティ階層 Workspace → **Session** → Prompt → Api Request の「Session の詳細」層。
ユーザー方針「session 詳細 = Usage をベースに session 絞り込み」に沿い、**Usage の
1エンティティ分メトリクスを workspace + session_id で絞った画面**。1セッションの
コスト・トークン・行数・待機/放置・モデル/ソース内訳を一望する。

## 設計思想

- **Usage の丸ごとクローン**（2026-07-14〜）: Usage がゼロベース再構築で「利用量の記述」に
  純化された（比率ガードレール・日次ペーシング・全体/ws別の二重が消えた）ため、旧版のような
  行の取捨（curate）は不要になった。全パネルをそのままスコープするだけでよい
- **全クエリに `workspace=~"$workspace"` と `| session_id=~"$session_id"` を機械付与**（生成器）
- **加算クエリの堅牢化**: 放置率/待機時間/permission は `wait_time + permission_wait` を加算する。
  単一 session では一方が空ベクトルになり `A + B` 全体が空→No data になるため、各項を
  `or vector(0)` で 0 埋めする（生成器が id 16/18/19 に適用）
- 変数は `workspace` ＋ `session_id`（通常はリンクで両方セットされて着地）
- **Usage を改修したら `python3 scripts/gen-session-detail.py` を再実行**して追従させる

## パネル（Usage と同一構成・session スコープ）

Usage の4セクション（[usage/README.md](../usage/README.md) 参照）をそのまま session に絞る:
総量サマリ（コスト/トークン/行数/セッション数/起動・待機・放置・permission）/
推移（コスト・トークン）/ 内訳（ws・model・source・トークン種・work_type・tool）/
行動・時間配分（起動処理時間）。効率診断（キャッシュ率の谷・compaction）は Context の管轄

## 入口 / 関連

- 入口: [Session List](../session-list/README.md) の行クリック（`var-workspace` + `var-session_id`）
- 土台: [usage/](../usage/README.md)（Usage のクローン＋スコープ）
- より深い診断: Context（cacheRead谷・compaction）/ [prompt-detail/](../prompt-detail/README.md)
- 数式・uid の正本: [../../CONTRACT.md](../../CONTRACT.md) §8
