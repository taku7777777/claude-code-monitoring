# model カタログ（観測されるモデルID）

> **層: L1（観測可能な事実）** — [1-references/](README.md) 参照。
> `api_request.model` に現れるモデルIDの観測記録。**価格・係数は L2**（自分たちの単価表
> [../../pricing/pricing.yaml](../../pricing/pricing.yaml)。詳細は [../CONTRACT.md §7](../CONTRACT.md)）。

## この環境で観測されたモデル（過去30d）

| model | リクエスト数 | 単価 input/output ($/MTok) | 備考 |
|---|---|---|---|
| `claude-fable-5` | 1837 | 10 / 50 | 最上位・最多利用。tokenizer=new |
| `claude-opus-4-8` | 1023 | 5 / 25 | tokenizer=new |
| `claude-sonnet-5` | 369 | 3 / 15 | tokenizer=new |
| `claude-haiku-4-5-20251001` | 121 | 1.5 / 7.5 | 日付サフィックス付き（下記） |

> リクエスト数は**30d の移動スナップショット**（2026-07-15 時点）で日々変動する。絶対値でなく
> 概ねの利用順位（fable ≫ opus > sonnet > haiku）の目安として読む。再取得は本ファイル末尾のコマンド。
> 単価は pricing.yaml の値（best-effort・2026-07-12 校正）。**表示コストの基準は
> pricing.yaml × 実トークンの再計算値 cost_recalc**（[api-request-attributes.md](api-request-attributes.md)）。

## モデルIDの正規化

- モデルIDは**日付サフィックス（`-YYYYMMDD`）付きで届くことがある**
  （例 `claude-haiku-4-5-20251001`）。単価照合時は `analysis/pricing.py` が
  サフィックスを剥がして `claude-haiku-4-5` にマッチさせる（唯一のローダ）。
- 未定義モデルの api_request は `cost_recalc_src="sdk_fallback"` となり cost_usd をコピー。
  fallback の混入は Cost Opt 概況「SDK推定との乖離」stat で気づける。

## pricing.yaml に価格定義はあるが本環境で未観測のモデル

`opus-4-7/4-6/4-5/4-1` / `sonnet-4-6/4-5` / `mythos-5` 等（[pricing.yaml](../../pricing/pricing.yaml) 参照）。
価格改定・新モデル追加時は **pricing.yaml を是正 → `scripts/gen-cost-recalc.py` 再実行 →
collector 再起動**（ADR 0006 / RUNBOOK §5）。

## 注

- 新しい model を観測したら本表に追記し、pricing.yaml に単価が有るか確認する
  （無ければ sdk_fallback になる）。
- 観測モデル一覧の再取得:
  ```bash
  END=$(python3 -c 'import time;print(int(time.time()))')
  curl -s -G "http://localhost:3100/loki/api/v1/query" \
    --data-urlencode 'query=sum by (model) (count_over_time({event_name="api_request"} [30d]))' \
    --data-urlencode "time=${END}000000000" | python3 -m json.tool
  ```
