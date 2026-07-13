#!/bin/bash
# =============================================================================
# stack-down.sh — 監視スタック停止 (stack-up.sh の対、volume は保持)
# =============================================================================
# compose 経路で起動していた場合は compose down、フォールバック経路の場合は
# コンテナ停止+削除のみ行う。volume (計測データ) はどちらの経路でも削除しない。
# データごと消すには: docker volume rm claude-code-monitoring_{loki,grafana,prometheus}-data
set -euo pipefail

cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1; then
  docker compose down
  exit 0
fi
if command -v docker-compose >/dev/null 2>&1; then
  docker-compose down
  exit 0
fi

PROJECT="claude-code-monitoring"
for name in "${PROJECT}-grafana" "${PROJECT}-otel-collector" "${PROJECT}-prometheus" "${PROJECT}-loki"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker rm -f "$name" >/dev/null && echo "[stack-down] removed: $name"
  fi
done
docker network rm "${PROJECT}_default" >/dev/null 2>&1 || true
echo "[stack-down] 完了 (volume は保持。データ削除は README 参照)"
