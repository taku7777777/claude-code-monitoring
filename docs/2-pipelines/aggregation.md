# 集計ロジック（analyzer）

> **層: L2** — [2-pipelines/](README.md)。Loki の個別イベントから、ダッシュボードの LogQL では
> 出せない集計を `analysis/cost-optimization-analyzer.py` が Python で算出する。
> 準実験の方式選定は ADR 0005。金額は SDK 推定 `cost_usd` 基準（[pricing.md](pricing.md) の注意）。

## なぜ analyzer が要るか

LogQL には **prompt 単位の分位点集約**や**変化点検出**が無い。これらは Python 側で計算する。
（一部はダッシュボードでも transform で近似可能になった。例: prompt 単位 p50/p90/p95 は
Grafana の percentile reducer で算出。[../3-requirements/cost-optimization/02-baseline.md](../3-requirements/cost-optimization/02-baseline.md) §4。
ただし analyzer は cost_usd 基準、ダッシュボードは cost_recalc 基準で数値が異なる点に注意。）

## 主な集計 [a]〜[g]

| 記号 | 内容 | 方法 |
|---|---|---|
| CPSO | 成果あたりコスト | 期間コスト ÷ 成功系 outcome 数（`outcome!~"failure\|abandoned"`） |
| [b] パレート | prompt_id / work_type 別寄与度分解 | 累積80%までハイライト・今期 vs 前期の増分 |
| [c] ベースライン | prompt_id ごとの期間合計コストの p50/p90/p95 | 分位点（prompt 単位。per-request とは集計単位が別） |
| [f] 施策効果検証 | 日次単位コスト(cost/1M実効トークン)の before/after | I-MR 管理図（X̄±2.66·MR̄、before 区間から算出）＋シフト率>10% |
| 変化点 | 日次コストの構造変化点 | CUSUM 内蔵、`ruptures` があれば PELT 併用 |
| [g] モデル経済性 | model 別に cost_usd と pricing 再計算の乖離 | 反実仮想・推定誤差の把握 |

## 実行

```bash
python3 analysis/cost-optimization-analyzer.py --hours 168 --top 10
python3 analysis/cost-optimization-analyzer.py --json          # 自動化用
```

依存は `analysis/requirements.txt`（`requests` 必須、`ruptures` 任意）。

## 効果検証との関係（重要な限界）

- [f] の管理図は**単位コスト（cost/1M実効トークン）**で判定するため、**モデル構成を変える施策
  しか動かさない**。キャッシュ/context削減など「量を減らす施策」は単位コストを動かさないため、
  日次総コスト/CPSO の before/after で見る必要がある
  （[../3-requirements/cost-optimization/04-verification.md](../3-requirements/cost-optimization/04-verification.md) §1）。
- 効果検証の狙いは厳密な因果証明でなく「効かない施策を畳む」ための目安。管理図は
  「通常の揺れ帯＝ノイズ判定」の参考として使う（単一ユーザーは統制実験にならない）。
