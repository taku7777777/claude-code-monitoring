# reference — 観測可能データのカタログ（L1）

このディレクトリは **L1＝「Claude Code / SDK が何を吐くか」という観測可能な事実**を集約する。
自分たちの加工や表示の判断ではなく、**上流の事実そのもの**（バージョンで変わりうる）を記述する。

## ドキュメントの3層モデル

コスト監視のドキュメントは、関心（＝変化する要因）で3層に分ける:

| 層 | 内容 | 変化する要因 | 置き場所 |
|---|---|---|---|
| **L1 観測可能な事実** | SDK が吐くイベント・属性・その取りうる値 | **Claude Code のバージョン** | **`docs/1-references/`（ここ）** + `CONTRACT.md §2/§3`（スキーマ SSOT） |
| **L2 蓄積・加工** | 派生値(cost_recalc/effective_tokens)・単価表・集計・保存スキーマ | **自分たちの設計判断** | **`docs/2-pipelines/`**（解説）＋ `CONTRACT.md`（SSOT）/ `pricing/` / `docs/adr/`（なぜ） |
| **L3 表示** | 各パネルが何を表示し、どう活用するか | **ダッシュボードの要件** | `docs/3-requirements/` |

L1 は「事実の確認」、L2 は「その事実をどう料理したか」、L3 は「料理をどう盛り付けたか」。
Claude Code の更新で影響を受けるのは主に L1。

この3層の前提となる **L0＝LLM 課金の仕組み・最適化レバーのドメイン知識**（変化要因:
Anthropic の API 仕様・価格）は [`../0-fundamentals/`](../0-fundamentals/README.md) に置く。

## 収録

| ファイル | 内容 |
|---|---|
| [events.md](events.md) | 観測される `event_name`（SDK由来・自前由来）の一覧・意味・確度 |
| [api-request-attributes.md](api-request-attributes.md) | 中核イベント `api_request` の全属性（L1生値 / L2派生の別・型・意味） |
| [event-attributes.md](event-attributes.md) | `tool_decision` / `compaction` 等 api_request 以外のイベント固有属性 |
| [query-source.md](query-source.md) | `api_request` の `query_source` 属性の取りうる値・意味・確度 |
| [models.md](models.md) | 観測される `model` ID の一覧（価格は L2 pricing.yaml へ） |

## 追記の指針

- 新しい観測値（未知の `event_name`・`query_source`・属性・`model` 等）を見つけたら、まずここに
  「観測された事実」として記録する（公式/非公式・確度を明記）。
- スキーマ全体の SSOT は [../CONTRACT.md](../CONTRACT.md)。本ディレクトリは
  その **値の詳細カタログ**（CONTRACT が指す先）として補完する。
- 将来候補: `tool_result` / `subagent_completed` 等の属性、`work_type` の taxonomy 実測分布。
