#!/bin/bash
# =============================================================================
# stack-up.sh — 監視スタック起動 (Compose v2 が無い環境向け docker run フォールバック付き)
# =============================================================================
# 1. `docker compose` / `docker-compose` があればそれで起動する (推奨経路)。
# 2. どちらも無い場合、docker-compose.yml と同一構成 (イメージ/ポート/volume/エイリアス/
#    再起動ポリシー) を `docker run` で再現する。
#    volume 名は compose の既定プロジェクト名に合わせてあるため、後日 compose を導入して
#    `docker compose up -d` に移行してもデータは引き継がれる。
# 使い方: ./scripts/stack-up.sh   (リポジトリルートから)
set -euo pipefail

cd "$(dirname "$0")/.."

# muti-repo-workspace のエージェントコンテナが OTLP を届けるための内部専用ネットワーク。
# compose は external ネットワークが無いと起動に失敗するため、先に冪等に作成しておく。
docker network create --internal mrw-telemetry 2>/dev/null || true

if docker compose version >/dev/null 2>&1; then
  exec docker compose up -d
fi
if command -v docker-compose >/dev/null 2>&1; then
  exec docker-compose up -d
fi

echo "[stack-up] Compose が見つからないため docker run フォールバックで起動します"

# compose の既定命名 (<dir>_<volume>) に合わせる
PROJECT="claude-code-monitoring"
NET="${PROJECT}_default"

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

start() { # name 以降は docker run 引数
  local name="$1"; shift
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker start "$name" >/dev/null
    echo "[stack-up] started (existing): $name"
  else
    docker run -d --name "$name" --restart unless-stopped "$@" >/dev/null
    echo "[stack-up] created: $name"
  fi
}

start "${PROJECT}-loki" \
  --network "$NET" --network-alias loki \
  -p 127.0.0.1:3100:3100 \
  -v "$PWD/loki-config.yml:/etc/loki/local-config.yaml" \
  -v "${PROJECT}_loki-data:/loki" \
  grafana/loki:3.5.1 -config.file=/etc/loki/local-config.yaml

start "${PROJECT}-prometheus" \
  --network "$NET" --network-alias prometheus \
  -p 127.0.0.1:9090:9090 \
  -v "$PWD/prometheus.yml:/etc/prometheus/prometheus.yml" \
  -v "${PROJECT}_prometheus-data:/prometheus" \
  prom/prometheus:v3.11.3 \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=30d \
  --storage.tsdb.retention.size=2GB \
  --storage.tsdb.min-block-duration=30m \
  --storage.tsdb.max-block-duration=30m \
  --storage.tsdb.wal-compression \
  --web.console.libraries=/usr/share/prometheus/console_libraries \
  --web.console.templates=/usr/share/prometheus/consoles

start "${PROJECT}-otel-collector" \
  --network "$NET" --network-alias otel-collector \
  -p 127.0.0.1:4317:4317 -p 127.0.0.1:4318:4318 \
  -v "$PWD/otel-collector-config.yml:/etc/otelcol-contrib/config.yaml" \
  otel/opentelemetry-collector-contrib:0.150.1

# docker run は作成時に 1 ネットワークしか接続できないため、mrw-telemetry は後付けで接続する
docker network connect mrw-telemetry "${PROJECT}-otel-collector" 2>/dev/null || true

start "${PROJECT}-grafana" \
  --network "$NET" \
  -p 127.0.0.1:3033:3000 \
  -v "${PROJECT}_grafana-data:/var/lib/grafana" \
  -v "$PWD/grafana/provisioning:/etc/grafana/provisioning" \
  -e GF_SECURITY_ADMIN_USER=admin \
  -e GF_SECURITY_ADMIN_PASSWORD=admin \
  -e GF_AUTH_ANONYMOUS_ENABLED=true \
  -e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
  grafana/grafana:13.0.1

echo "[stack-up] 完了: Grafana http://localhost:3033 (匿名 Viewer / admin/admin)"
