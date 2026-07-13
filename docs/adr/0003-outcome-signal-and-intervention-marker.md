# ADR 0003: task_outcome と intervention_marker を新設する

- ステータス: 採用
- 日付: 2026-07

## Context

既存ダッシュボード（Usage / Context）はコスト・時間・トークン・コンテキストという**量的**
指標を可視化していた。しかし「コストを客観的に最適化する」には量的指標だけでは不十分で、
2 つの軸が欠けていた。

1. **成果の分母**: コストを下げても成果が伴わなければ意味がない。「コスト削減」を単独で
   追うと分子最適化が自己目的化する。単位コスト（Cost Per Successful Outcome, CPSO =
   期間コスト ÷ 成果数）を測るには、分母となる**成果シグナル**が要る。SDK は成果の
   成否を出さない。
2. **施策の起点**: コスト削減施策（キャッシュ導入・モデル切替・context 削減等）を打ったとき、
   「いつ・何を・どの範囲に」実施したかを記録しないと、施策の before/after を後から照合できない。
   単一ユーザー環境では A/B ができず、時系列上の準実験（ITSA / 管理図、
   [ADR 0005](0005-objective-verification-methods.md)）で検証するしかないため、
   **施策の発生時刻マーカー**が不可欠になる。

## Decision

**客観サイクル（観測 → 診断 → 施策 → 検証）を回すため、2 種類の新イベントを新設する。**
いずれもログベース個別イベント（[ADR 0001](0001-log-based-event-architecture.md)）として
Loki に届ける。

### `task_outcome`（成果 = CPSO の分母）

- `outcome`: `completed` | `success` | `failure` | `abandoned`
- Stop hook (`task-outcome-on-stop.sh`) が `completed`（`outcome_proxy=stop_reached`）を自動送信。
  Stop 到達を「タスクが一区切りついた」ことの**弱いプロキシ**として扱う。
- CLI (`scripts/task-outcome.sh <success|failure|abandoned> [note]`) で人間が
  `outcome_proxy=manual` として上書き記録する。
- CPSO の分母に使い、コスト削減が「安く済ませた」のか「成果を落とした」のかを分離する。

### `intervention_marker`（before/after 検証の起点）

- `intervention_id`: `iv-<epoch>` 形式で自動採番。
- `category`: `caching` | `model_switch` | `context_reduction` | `compaction_tuning` |
  `prompt_edit` | `subagent_policy` | `other`
- `description`, `scope`（任意: 対象 prompt_id/work_type 等）。
- CLI (`scripts/intervention-marker.sh --category ... --desc "..." [--scope "..."]`) で記録し、
  Grafana annotation として Cost Optimization ダッシュボードの全パネルに縦線を重畳する。

スキーマは CONTRACT §3.2 / §3.3、送信仕様は §6 に従う。

## Consequences

**利点（トレードオフの表）**

- CPSO という単位コスト指標が算出可能になり、「安さ」と「成果」を両立して評価できる。
- 施策の時刻が全パネルに縦線で重畳され、ITSA / 管理図による準実験的な効果検証の起点になる。
- 両イベントとも既存のログベース基盤にそのまま載るため、追加の保存系は不要。

**代償・限界（明記必須）**

- **プロキシの限界**: `task_outcome=completed` は Stop 到達という弱いプロキシであり、
  「途中で諦めた」「品質は劣化したが止まった」ケースを `completed` と誤ラベルし得る。真の
  品質劣化を取りこぼす。ダッシュボード説明文にこの限界を明記している（CONTRACT §3.2）。
- 手動 outcome / 手動 intervention は**運用規律**に依存する。記録を怠ると分母がノイズになり、
  施策検証の縦線が欠ける。
- `intervention_marker` の `scope` は自由記述に近く、集計時の名寄せは利用者責任。

**関連**: requirements.md 第 16 章、CONTRACT §2 / §3.2 / §3.3 / §6、
[ADR 0005](0005-objective-verification-methods.md)。
