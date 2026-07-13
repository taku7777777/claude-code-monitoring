# ADR 0006: ダッシュボードのコスト表示を collector 計算の cost_recalc に切り替える

- ステータス: 採用
- 日付: 2026-07-12

## Context

ADR 0004 で pricing.yaml を価格 SSOT とし、analyzer [g] が SDK 推定 (`cost_usd`) と
再計算の乖離を監視できるようにした。しかしダッシュボードの表示自体は `cost_usd` の
ままで、以下の問題が残っていた。

- SDK の内蔵価格表はブラックボックスであり、請求と乖離しても**こちらから是正する
  手段がない**。単価表×実トークンなら、請求と突合して pricing.yaml を直すことで
  表示を実態に収束させられる（ユーザー要件）。
- 2026-07-12 の実測校正で、当初 -17% あった乖離の主因は次の3点と判明した
  （長コンテキスト割増という当初仮説は否定）:
  1. **キャッシュ書込 TTL 仮定**: SDK は実質 2.0x（1h TTL）で計算しており、
     pricing.yaml の 1.25x（5m）仮定がズレていた（opus の含意単価がバケット別逆算で
     w=2.0 のときちょうど $5.000）
  2. **sonnet-5 の単価誤り**: 実際は $3/$15（「2026-09 値上げ予定」としていた値が現行価格）
  3. **haiku の日付付きモデル ID** (`claude-haiku-4-5-20251001`) が未定義で再計算から
     脱落していた（含意単価は $1.5/$7.5）
- なお SDK 推定は >200K の長コンテキスト割増を**織り込んでいない**ことも判明
  （fable >200K バケットの含意単価がベース単価ちょうど）。割増が実請求に現れる場合、
  SDK 推定は過小になる — これも自前再計算に軸足を移す理由になる。

## Decision

1. **pricing.yaml を実測校正**する（sonnet-5=3/15、haiku-4-5=1.5/7.5、
   `cache.write_default_ttl: "1h"` 新設）。校正後の乖離は 24h 合計で +3.9%
   （opus は $0.0000 一致）。
2. **collector が取り込み時に `cost_recalc` を各 api_request ログへ付与**する
   （transform/cost_recalc）。計算式は `scripts/gen-cost-recalc.py` が pricing.yaml
   から生成して otel-collector-config.yml のマーカー間に書き込む（SSOT 維持。
   pricing.py 経由でロードするため二重パースもしない）。
3. **未知モデルは `cost_usd` にフォールバック**し `cost_recalc_src="sdk_fallback"` を
   付ける（定義済みは `"pricing"`）。新モデル登場時に合計が欠けて過小表示になる事故を
   防ぐ。フォールバックの発生は analyzer [g] の未定義モデル警告で検知する
   （乖離 stat は pricing 分のみの比較のため fallback は写らない）。
4. **全ダッシュボードのコスト表示を `cost_recalc` に切替**（unwrap 54 箇所）。
   `cost_usd` は SDK 推定の参照値としてイベントに残し、概況の「SDK推定との乖離」
   stat と analyzer [g] で突合を続ける。
5. モデル ID は日付サフィックス付きでも解決する（pricing.py が `-YYYYMMDD` を正規化、
   OTTL 側は IsMatch の正規表現）。
6. 長コンテキスト割増は pricing.yaml の `long_context` として**スキーマだけ用意**し、
   既定では無効（SDK 推定に現れず、実請求での有無が未確認のため）。請求に現れたら
   有効化して校正する。

## Consequences

- **価格はログ受信時点で焼き込まれる**。pricing.yaml の是正は将来分にのみ効き、
  過去分は analyzer（Python 再計算）でのみ遡及できる。「表示の安定性（過去が動かない）」
  を「遡及是正」より優先した選択。
- **切替日以前のイベントには cost_recalc が無い**。当初は「切替後のみ集計」で運用予定
  だったが、比率系パネル（CPSO・コスト/1M実効トークン。分母は全期間・分子は切替後のみ）と
  前週比 stat が過渡期に大きく歪むことが実運用初日に判明。対策として全コストクエリを
  **query 側 coalesce** に変更した:
  `label_format cost_v="{{ if .cost_recalc }}{{ .cost_recalc }}{{ else }}{{ .cost_usd }}{{ end }}" | unwrap cost_v`
  （旧イベントは cost_usd 基準で表示継続、新イベントから cost_recalc。両者の差は乖離 stat の
  範囲 ±数%。90日で自然に全量 recalc になる）。「SDK推定との乖離」stat のみ
  coalesce せず純粋比較を維持する。
- **pricing.yaml 変更時は再生成が必須**:
  `python3 scripts/gen-cost-recalc.py && docker compose restart otel-collector`。
  生成漏れは `--check` で検知できる（生成日を焼き込まないため、差分＝価格の実差分）。
- **TTL は単一仮定（1h=2.0x）の近似**。telemetry が cache_creation の TTL 内訳を
  持たないため、5m/1h 混在時は数%過大になりうる（fable ≤200K バケットで実測 +8%）。
  乖離 stat が定常的に負に振れたらこの仮定を疑う。
- **analyzer は当面 `cost_usd` を「観測値」として使い続ける**（[g] の突合には SDK 値が
  必要。その他セクションも 90d 遡及集計のため cost_recalc が揃うまで cost_usd 基準）。
  ダッシュボードとの差は乖離 stat の範囲（±数%）に収まる。
- effective_tokens のウェイト（cache_creation 1.25）は**変更しない**。あちらは
  「作業量の安定した規約値」であり、金額の正確性は cost_recalc が担う（役割分担）。
  CONTRACT §5 の「価格比と一致させる」注記はこの区別を明記するよう更新。

**関連**: ADR 0002（collector 派生値の前例）、ADR 0004（価格 SSOT）、CONTRACT §3.1 / §5 / §7、
`scripts/gen-cost-recalc.py`、RUNBOOK §5（月次の請求突合・是正手順）。
