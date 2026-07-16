# 03 — 何を監視し、どう動くか（プレイブック）

[01](01-cost-model.md) の仕組みと [02](02-optimization-levers.md) のレバーを、
本リポジトリの**指標・画面・アクション**に対応づける。指標の厳密な定義は
[3-requirements/](../3-requirements/README.md)、実測値での判断例は
[CASEBOOK.md](../CASEBOOK.md)、操作して体感するなら [ONBOARDING.md](../ONBOARDING.md)。

## 0. 共通の型

どの異常も **検知 → ベースライン比較 → 帰属 → 明細で因果 → 施策はマーカーを打って1本ずつ**
の5手で閉じる（全画面共通の設計前提。5手の詳細な文言は
[CASEBOOK 共通の型](../CASEBOOK.md) / [ONBOARDING §4](../ONBOARDING.md) を参照）。

## 1. 監視マップ — レバー × 指標 × アクション

| レバー（02） | 指標（画面） | 健全域 | 悪化のシグナルと打ち手 |
|---|---|---|---|
| 1. コンテキスト量 | **取り込み比** = cache_creation/output（Prompt List）/ **コンテキスト遷移**（Today, Context） | 取り込み比 <15 / 150K 破線に余裕 | 比が赤（≥30）の prompt = 読み込み過多の初手。**依頼テンプレを変える**（対象列挙 or Explore 委任）。破線接近×継続なら `/compact`、話題が替わるならセッションを切る |
| 2. キャッシュ継続性 | **キャッシュ有効率**（Today, Cost Opt）/ コンテキスト遷移の**空白** | ≥85%（<85%黄 / <70%赤） | 低下の原因を特定: 新セッション/clear 直後なら正常。**放置後再開**が原因なら、続きが不要な旧セッションは再開せず新規で始める（必要なら再構築費を織り込む）。設定・モデル切替起因ならセッション境界に寄せる |
| 3. 委任と委任モデル | **コスト構成（query_source 別）**（Today）/ **agent種別×モデル**・**委任率・$/起動**（Cost Opt） | 委任の高額モデル比率が低い | 委任のほぼ全額が fable 等なら**委任モデルを Sonnet/Haiku 既定へ**（マーカー `subagent_policy` を打ってから）。委任率が極端に低い＋取り込み比が高い場合は逆に「委任していない」のが問題 |
| 4. モデルミックス | **コスト/1M実効**（Cost Opt, 各サマリ） | 安定していること | 上昇 = 高額モデル比率の増加（fable 10 > opus 5 > sonnet 3 > haiku 1.5）。作業内容に対して過剰スペックか点検。成果定義に依存しない指標なので**モデル系施策の効果検証**にも使う |
| 5. effort | `effort`（structured metadata。専用パネル未設置）/ **実行時間**（Prompt List / Session List） | — | 長時間×高コスト prompt を Prompt List でソートして発見 → 明細で effort・turn 数を確認。定型作業に high/xhigh が常用されていれば下げる |
| 6. 依頼の仕方 | **CPSO**（Cost Opt）/ p50/p90/p95 との乖離 / **未計上コスト**（Cost Opt 計測ヘルス） | CPSO 横ばい / 前週比 +30% 以内 | CPSO 上昇 = 同じ仕事に多く払っている**疑い**（比較は同じ work_type・同等の成果定義が前提。分母 task_outcome の質も先に点検 — completed は Stop 到達の弱い proxy、CONTRACT §3.2）。top15 → 明細で「粗い初手・ラリー・手戻り」を特定し依頼テンプレを直す。中断疑いが多ければ**送信前に依頼を固める**運用へ |
| 7. 放置・背景機能 | **Claude放置率 / 待機時間**（Usage）/ **背景比率**（Today） | 背景比率 <20%（≥20%黄 / ≥35%赤） | 放置率が高い = TTL 失効の再構築費と背景コストの温床。応答が返ったら指示か終了。背景比率が恒常黄なら原因 query_source（away_summary 等）を特定し抑制を検討 |
| （計測自体の健全性） | **SDK推定との乖離**（Cost Opt）/ **unlabeled コスト**（Today workspace別サマリの `unlabeled` 行） | ±10% 以内 / 本日の10%未満（異常カタログ A7） | 乖離超過 = pricing.yaml のズレ → 校正（RUNBOOK §5）。unlabeled 行が目立つ = タスク dir 起動漏れ → その日のうちに `init-task-workspace.sh` |

> グレー「分母不足」表示は正常（比率系は最低分母ガードで判定保留）。閾値の一覧は
> [ONBOARDING §2](../ONBOARDING.md) と各 [3-requirements](../3-requirements/README.md)。

## 2. 巡回のリズム — いつ・どの画面か

| タイミング | 画面 | 見るもの・することの例 |
|---|---|---|
| 作業中（随時） | **Today** | 本日コスト・**ガードレール**（背景比率・キャッシュ有効率の緑黄赤）・コンテキスト遷移・workspace別サマリ（`unlabeled` 行含む）。破線接近 → /compact、放置の自覚。黄赤が出たら §0 の型で降りる |
| 毎日 2分 | **Usage** | 量の振り返り（何に・どれだけ使ったか）と日次ペーシング（ペース比） |
| 週1回 15分 | **Cost Optimization** | CPSO・コスト/1M実効・top15・前週比。**施策を1本決めて**マーカーを打つ |
| 月次 | 請求突合（[RUNBOOK §5](../RUNBOOK.md)） | console 請求 vs cost_recalc。乖離があれば pricing.yaml 校正。未計上コスト（中断分）の規模確認 |

日々の手順の正典は [RUNBOOK.md](../RUNBOOK.md)。本表はレバーとの対応づけのための要約。

## 3. 施策の実行と検証 — 1本ずつ、マーカーを打ってから

単一ユーザーでは A/B ができないため、**マーカー付き前後比較**を採る
（[ADR 0003](../adr/0003-outcome-signal-and-intervention-marker.md) /
[ADR 0005](../adr/0005-objective-verification-methods.md)）:

```bash
scripts/intervention-marker.sh --category <caching|model_switch|context_reduction|\
  compaction_tuning|prompt_edit|subagent_policy|other> --desc "..."
```

1. 施策は**同時に1本だけ**（交絡防止。第2候補は1〜2週間寝かせる）
2. 打った瞬間にマーカー → 全時系列パネルに縦線が重畳される
3. 1〜2週間後、コスト/1M実効・日次コスト推移の水準変化を目視 ＋
   `analysis/cost-optimization-analyzer.py` の管理図（I-MR）で shift を確認
4. **有意でなければ「効果なし」と記録して戻す**（戻すのも施策マーカーを打つ）

厳密な統計判定より「施策ジャーナル＋日次マクロの目視」に重心を置く
（単一ユーザーは統制実験にならない — [ADR 0005](../adr/0005-objective-verification-methods.md)）。

## 4. シナリオ索引 — 「こういうときはどれを見るか」

| 状況 | 入口 | 詳細な実例 |
|---|---|---|
| 昨日、異常に高い prompt があった。理由を説明したい | Cost Opt top15 → Prompt明細 → show-session.sh | [CASEBOOK Case 1](../CASEBOOK.md)（$7.97 の6割が背景機能だった例） |
| 今日のペースが速すぎる。午後どうするか | Usage 日次ペーシング（ペース比） | [CASEBOOK Case 2](../CASEBOOK.md)（ペース比 1.8 → 午後の fan-out 停止） |
| 週次レビューで施策を1本決めたい | ガードレール黄赤 → Cost Opt agent種別×モデルで帰属 → RICE 概算 | [CASEBOOK Case 3](../CASEBOOK.md)（fable 委任 → Sonnet 化を決定、削減見込み 週 $15 前後） |
| 同種タスクなのにコストが倍違う。何が違った？ | Cost Opt タスク別サマリ → 差分3指標（委任/探索/手戻り） | [CASEBOOK Case 4](../CASEBOOK.md)（粗い初手＋150K 張り付きが原因） |
| キャッシュ有効率が急に下がった | Today → コンテキスト遷移の空白の有無 | [ONBOARDING E4](../ONBOARDING.md)（放置→TTL 失効の再現実験） |
| 取り込み比が赤い prompt を出してしまった | Prompt List → 明細で本文確認 | [ONBOARDING E2](../ONBOARDING.md)（bulk read の再現実験） |
| 委任が高くつく気がする | Today コスト構成 → Cost Opt agent種別×モデル | [ONBOARDING E5 / E10](../ONBOARDING.md)（委任・モデル切替の再現実験） |
| 中断が多い週のコストが請求と合わない気がする | Cost Opt 計測ヘルス「未計上コスト」 | [CONTRACT §3.1](../CONTRACT.md)（計上 ≤ 実請求の構造） |

---

**ここまでが L0**。実際の値の読み方の勘を作るには [ONBOARDING.md](../ONBOARDING.md) の
実験 E1〜E10 を一巡するのが最短。値の定義に踏み込むときは
[CONTRACT.md](../CONTRACT.md) と [3-requirements/](../3-requirements/README.md) を正典として参照する。
