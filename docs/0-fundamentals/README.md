# fundamentals — LLMコストの前提知識（L0）

このディレクトリは **L0＝「LLM のコストはどういう仕組みで発生し、何で動かせるか」という
ドメイン知識**を集約する。本リポジトリの監視設計（L1〜L3）はすべてこの知識を前提にしており、
「なぜこの指標を見るのか / 赤くなったら何をするのか」の根拠はここに帰着する。

- 主な変化要因: **Anthropic の API 仕様・価格**。Claude Code 実装に依存する部分
  （キャッシュの使い方・背景機能・telemetry 属性）は L1 の観測事実で校正する
- 下流: L1 観測事実 = [../1-references/](../1-references/README.md) /
  L2 加工 = [../2-pipelines/](../2-pipelines/README.md) /
  L3 表示 = [../3-requirements/](../3-requirements/README.md)
- 価格の数値は snapshot。**SSOT は [`pricing/pricing.yaml`](../../pricing/pricing.yaml)**
  （価格改定時は pricing.yaml と本ディレクトリを併せて更新する）

## 収録（読み順）

| ファイル | 内容 | 答える問い |
|---|---|---|
| [01-cost-model.md](01-cost-model.md) | コスト算定の仕組み | プロンプトを送るとどんな API やりとりが起き、何にいくら課金されるか。キャッシュが効く場合/切れる場合、放置・中断で何が起きるか |
| [02-optimization-levers.md](02-optimization-levers.md) | 最適化レバー | コンテキスト・キャッシュ・委任・モデル・effort・依頼の仕方は、コストと実行時間にどう相関するか |
| [03-monitoring-playbook.md](03-monitoring-playbook.md) | 監視とアクション | 各レバーをどの指標・どの画面で監視し、悪化したら何をするか（具体シナリオの索引つき） |

## 位置づけ（他ドキュメントとの分担）

```
0-fundamentals（ここ）  なぜそのコストになるか・何で動かせるか   ← 原理
     │
USECASE.md              何を実現したいか                        ← 目的
ONBOARDING.md           操作すると画面がどう動くか               ← 体験
CASEBOOK.md             実測値でどう診断したか                   ← 実例
     │
1-references / 2-pipelines / 3-requirements / CONTRACT.md       ← 計測の作り（正典）
```

原理を知りたければここ、値の厳密な定義は [CONTRACT.md](../CONTRACT.md)、
手を動かして体感するなら [ONBOARDING.md](../ONBOARDING.md) から入る。
