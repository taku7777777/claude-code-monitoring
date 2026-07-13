# pipeline — 蓄積・加工・集計（L2）

このディレクトリは **L2＝「L1 の観測事実を、どう蓄積・加工・集計して表示可能な値にするか」**を
記述する。**自分たちの設計判断**の層（Claude Code のバージョンではなく、我々の意思で変わる）。

- 上流の事実（何が観測可能か）＝ **L1**: [../1-references/](../1-references/README.md)
- 下流の表示（各パネルが何を出すか）＝ **L3**: [../3-requirements/](../3-requirements/README.md)

> **方針**: 名前・スキーマ・数式の唯一の正本（SSOT）は [../CONTRACT.md](../CONTRACT.md)、
> 不可逆な設計判断の記録は [../adr/](../adr/) と [../../requirements.md](../../requirements.md)。
> 本ディレクトリはそれらを**二重化せず、「なぜ・どう加工するか」を解説してリンクする**層。

## パイプライン全体像

```
L1 事実                         L2 加工・蓄積（ここ）                         L3 表示
─────────           ──────────────────────────────────────           ─────────
Claude Code SDK ──OTLP──▶ OTEL Collector                          ┌─▶ Grafana
  (api_request 等)          ├─ 派生値を1回だけ事前計算              │   (3-requirements/)
自前 hook/CLI ──OTLP──────▶ │   cost_recalc / effective_tokens /   │
  (task_outcome 等)         │   context_tokens                     │
                            └─ workspace/work_type を属性へ昇格      │
                                     │                              │
                                     ▼                              │
                              Loki（保存スキーマ:                    │
                              index_label 5つ + structured metadata）│
                                     │                              │
                            analysis/*.py（集計: CPSO/パレート/       │
                              管理図/変化点）── 補助的に ────────────┘
                                     ▲
                            pricing/pricing.yaml（単価表 SSOT）
```

## 収録

| ファイル | 内容 | 主な正本/コード |
|---|---|---|
| [derivations.md](derivations.md) | collector が事前計算する派生値（cost_recalc / effective_tokens / context_tokens）の作り方 | CONTRACT §3/§5, ADR 0002/0006, `scripts/gen-cost-recalc.py`, `otel-collector-config.yml` |
| [pricing.md](pricing.md) | 単価表 SSOT とコスト校正ループ | `pricing/pricing.yaml`, `analysis/pricing.py`, CONTRACT §7, RUNBOOK §5 |
| [storage.md](storage.md) | Loki の保存スキーマ（index_label / structured metadata / retention） | CONTRACT §4, `loki-config.yml` |
| [hook-events.md](hook-events.md) | 自前イベント（wait_time / permission_wait / lines_changed / task_outcome / intervention）の生成 | CONTRACT §3/§6, requirements.md §11, `scripts/*.sh` |
| [aggregation.md](aggregation.md) | analyzer の集計ロジック（CPSO / パレート / I-MR 管理図 / 変化点） | `analysis/cost-optimization-analyzer.py`, ADR 0005 |

## 設計原則（この層の要点）

1. **派生値は収集層で1回だけ計算する**（ダッシュボードで毎回計算しない）。パネル間の整合を
   構造的に保証する（ADR 0002）。
2. **価格は SSOT（pricing.yaml）に一元化**し、表示コストは自前再計算 `cost_recalc` を使う（ADR 0006）。
3. **保存はログベースの個別イベント**（cumulative counter を後から差分計算しない。ADR 0001）。
4. 加工の変更（ウェイト・単価・スキーマ）は**過去データとの連続性**に注意し、必要なら
   施策マーカーで時刻を記録する。
