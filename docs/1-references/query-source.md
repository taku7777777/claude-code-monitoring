# query_source リファレンス（発生経路の種別）

> **層: L1（観測可能なデータ＝事実）** — [1-references/](README.md) 参照。
> Claude Code SDK が `api_request` に付けてくる値であり、加工(L2)・表示(L3)より上流の事実。

`api_request` イベントの `query_source` 属性＝**どの処理系統が API を呼んだか**の識別子。
コスト内訳（Cost Optimization「query_source別コスト」/ Usage「背景コスト比率」）の読み解きに使う。

> ⚠️ **重要な前提**
> - 下表の細粒度の値は**この環境で実際に観測された値**（過去30d）であり、
>   **Claude Code の公式ドキュメントが列挙しているのは粗い5カテゴリのみ**
>   （`repl_main_thread` / `compact` / `main` / `subagent` / `auxiliary`。
>   [公式: Claude Code 監視ドキュメント](https://code.claude.com/docs/en/monitoring-usage)）。
> - 細粒度の値（`away_summary` 等）は**非公式・内部実装依存で、Claude Code の
>   バージョンで変わりうる**。新しい値が出たら本ファイルに追記すること。
> - 「意味」列の確度ラベルに従うこと。**低**は推測混じり（一次ソース未確認）。

## 前景（自分が指示した処理）

| 観測値 | 公式カテゴリ | 意味 | 確度 |
|---|---|---|---|
| `repl_main_thread` | `repl_main_thread` | 本体の対話。ユーザーがプロンプトを送るたびの主系統。通常は総コストの大半を占める（正常） | 高 |
| `agent:builtin:general-purpose` | `subagent` | Task 委任で起動した組込み general-purpose サブエージェントの呼び出し。`agent:<種別>` 形式で本体と区別（[06-subagent](../3-requirements/cost-optimization/06-subagent.md) の計測原理） | 高 |

## 背景（裏で自動発生する処理）

| 観測値 | 公式カテゴリ | 意味 | 確度 |
|---|---|---|---|
| `compact` | `compact` | コンテキスト圧縮（`/compact` 手動 / auto-compaction）の要約 API | 高 |
| `generate_session_title` | `auxiliary` | セッションのタイトル自動生成。1セッション1回程度、極小コスト | 中〜高 |
| `prompt_suggestion` | `auxiliary` | 入力補完 / サジェスト機能の生成。使用頻度の割に嵩むことがある（削減候補） | 中 |
| `away_summary` | `auxiliary` | **離席/中断したセッションに戻った際の背景要約**（セッション再開ごと程度）と理解。ただし発火閾値・無効化方法は**公式未記載**。`CLAUDE_CODE_ENABLE_AWAY_SUMMARY` 等のトグルは**一次ソース未確認**で断定不可 | 低（非公式・推測） |
| `web_search_tool` | `auxiliary` | WebSearch ツールの内部 API 呼び出し | 中 |
| `web_fetch_apply` | `auxiliary` | WebFetch で取得した内容の処理 / 適用 | 中 |

## 公式カテゴリ（監視ドキュメント記載の粗粒度）

公式ドキュメントが列挙するのは以下の5つ。上表の細粒度値はこれらに丸められる関係:

| 公式値 | 説明 | この環境での観測 |
|---|---|---|
| `repl_main_thread` | メイン対話スレッド | ✅ そのまま観測 |
| `compact` | 会話圧縮由来 | ✅ そのまま観測 |
| `subagent` | サブエージェント発の要求 | `agent:builtin:general-purpose` として細粒度で観測 |
| `auxiliary` | 背景 / 補助的な内部処理 | `away_summary` / `prompt_suggestion` / `generate_session_title` / `web_*` に分かれて観測 |
| `main` | 主セッション（メトリクス文脈） | 本環境のログでは未観測 |

## 活用（読み解きの型）

- **前景と背景を切り分ける**。`repl_main_thread`（実作業）が大半なのは正常。問題は
  **背景コストの絶対額**（自分が直接頼んでいないのに掛かっている額）。
- **背景の各行に「払う価値があるか」を問う** → 価値に見合わなければ Claude Code の設定で
  機能オフ・頻度調整（実作業に影響ゼロで削れる「タダで拾える施策」）。
  詳細は [cost-optimization/05-diagnosis §4](../3-requirements/cost-optimization/05-diagnosis.md)。
- `agent:*` は意図した委任なので [06-subagent](../3-requirements/cost-optimization/06-subagent.md) で妥当性を見る。

## 観測値の再取得（新しい値の確認）

```bash
# 直近30日に出現した query_source の全値
END=$(python3 -c 'import time;print(int(time.time()))'); START=$(python3 -c 'import time;print(int(time.time())-30*86400)')
curl -s -G "http://localhost:3100/loki/api/v1/label/query_source/values" \
  --data-urlencode "start=${START}000000000" --data-urlencode "end=${END}000000000" | python3 -m json.tool
```

未知の値が出たら本ファイルに追記し、可能なら公式カテゴリ（前景/背景）に分類する。
