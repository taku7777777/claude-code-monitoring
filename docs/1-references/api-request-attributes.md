# api_request 属性カタログ

> **層: L1（観測可能な事実）＋一部 L2（派生）** — [1-references/](README.md) 参照。
> 中核イベント `api_request`（[events.md](events.md)）が持つ属性の一覧。
> **L1**＝Claude Code SDK が吐く生の値 / **L2**＝collector が取り込み時に計算する派生値
> （なぜそう計算するかは [../CONTRACT.md §3/§5](../CONTRACT.md) と ADR）。

Loki で **index_label** に昇格するのは5つのみ（`service_name` / `workspace` / `work_type` /
`event_name` / `query_source`）。それ以外は structured metadata（`| key="..."` でフィルタ・
`unwrap key` で数値化できるが series は増やさない）。CONTRACT §4。

## コスト

| 属性 | 型 | 層 | 意味 | 確度 |
|---|---|---|---|---|
| `cost_usd` | double | L1(SDK) | SDK 推定コスト（**公式請求ではない**）。乖離監視の参照値 | 高 |
| `cost_usd_micros` | int | L1(SDK) | 同上のマイクロUSD表現（cost_usd×1e6） | 高 |
| `cost_recalc` | double | **L2(派生)** | pricing.yaml 単価×実トークンの再計算値。**ダッシュボードの全コスト表示の基準**（ADR 0006） | 高 |
| `cost_recalc_src` | string | **L2(派生)** | `pricing`（単価表で計算）/ `sdk_fallback`（未定義モデルで cost_usd をコピー） | 高 |

## トークン

| 属性 | 型 | 層 | 意味 | 確度 |
|---|---|---|---|---|
| `input_tokens` | int | L1(SDK) | キャッシュ未使用の新規入力トークン | 高 |
| `output_tokens` | int | L1(SDK) | 出力トークン | 高 |
| `cache_read_tokens` | int | L1(SDK) | キャッシュ読取トークン（1/10 価格） | 高 |
| `cache_creation_tokens` | int | L1(SDK) | キャッシュ書込トークン | 高 |
| `context_tokens` | int | **L2(派生)** | `cache_read_tokens + cache_creation_tokens`。1リクエストが抱える履歴量 | 高 |
| `effective_tokens` | double | **L2(派生)** | 価格ウェイト正規化した処理量＝input×1 + cache_read×0.1 + cache_creation×1.25 + output×5（CONTRACT §5） | 高 |

## 分類・モデル

| 属性 | 型 | 層 | 意味 | 確度 |
|---|---|---|---|---|
| `model` | string | L1(SDK) | 使用モデル（例 `claude-opus-4-8` / `claude-fable-5`） | 高 |
| `query_source` | string | L1(SDK)・**index** | 発生経路（詳細 [query-source.md](query-source.md)） | 高 |
| `effort` | string | L1(SDK) | 推論の effort（`low`/`medium`/`high`/`xhigh`/`max`） | 高 |
| `speed` | string | L1(SDK) | 速度モード（例 `normal` / `fast`） | 中〜高 |
| `detected_level` | string | L1(SDK) | 検出されたレベル（観測では `unknown` 等）。意味は公式未確認 | 低 |

## 識別子

| 属性 | 型 | 層 | 意味 | 確度 |
|---|---|---|---|---|
| `prompt_id` | string | L1(SDK) | プロンプト（1往復）単位ID。Prompt明細のキー | 高 |
| `session_id` | string | L1(SDK) | セッション（起動〜終了）単位ID | 高 |
| `request_id` | string | L1(SDK) | API リクエストID（`req_...`） | 高 |

## 時間・連番

| 属性 | 型 | 層 | 意味 | 確度 |
|---|---|---|---|---|
| `duration_ms` | double | L1(SDK) | このリクエストの所要時間（ms） | 高 |
| `event_timestamp` | string(ISO) | L1(SDK) | イベント発生時刻 | 高 |
| `observed_timestamp` | int(ns) | L1(SDK) | 観測時刻（ナノ秒） | 中〜高 |
| `event_sequence` | int | L1(SDK) | セッション内のイベント連番 | 中 |

## 帰属（自前注入）

| 属性 | 型 | 層 | 意味 | 確度 |
|---|---|---|---|---|
| `workspace` | string | 自前(resource)・**index** | タスク=ワークスペース識別（`OTEL_RESOURCE_ATTRIBUTES` で注入。CONTRACT §5.5/§6） | 高 |
| `work_type` | string | 自前(resource)・**index** | 作業種別（feature/incident/... / main-dev） | 高 |

## 環境・リソース（SDK/OTel 標準）

いずれも L1(SDK/実行環境)。基本フィルタには使わないが、トラブルシュートや帰属確認に。

| 属性 | 意味 |
|---|---|
| `service_name`（**index**） / `service_version` | サービス名（`claude-code`）とバージョン |
| `scope_name` / `scope_version` | 計測スコープ（`com.anthropic.claude_code.events`）とバージョン |
| `host_arch` / `os_type` / `os_version` / `terminal_type` | 実行ホスト情報（例 arm64 / darwin / ghostty） |
| `organization_id` / `user_id` / `user_email` / `user_account_id` / `user_account_uuid` | アカウント識別子（個人ローカル前提。外部送出しない） |

## 注

- 型は Loki には文字列で届くが `unwrap` で数値化される（CONTRACT §8 / requirements.md 8）。
- L2(派生)の値は pricing.yaml やウェイト変更で意味が変わる。変更時は
  [../CONTRACT.md §5/§7](../CONTRACT.md) と ADR を参照し、必要なら施策マーカーで時刻を記録。
- 未知の属性を観測したら本表に追記（L1事実 or L2派生 かを明記）。
