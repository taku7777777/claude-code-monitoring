#!/bin/bash
# init-task-workspace.sh — タスクdir に workspace ラベル (.claude/settings.json) を生成する。
#
#   使い方: init-task-workspace.sh <dir> [--type <feature|incident|design|refactor|chore>]
#
# 運用前提「タスク = workspace」(docs/USECASE.md) の土台。settings.json が無い新規
# タスクdirは親 (~/github 等) のフォールバックに吸われて workspace=unlabeled に化けるため、
# タスクdir作成フローに本スクリプトを組み込んでラベル漏れを構造的に防ぐ。
# workspace 名は dir 名をそのまま使う (再利用しない名前を推奨: task-YYYYMMDD-<内容>)。
set -euo pipefail

PROG=$(basename "$0")
TAXONOMY="feature|incident|design|refactor|chore"

usage() {
  echo "Usage: $PROG <dir> [--type <feature|incident|design|refactor|chore>]" >&2
  echo "  Creates <dir>/.claude/settings.json with workspace=<dir名>, work_type=<種別>." >&2
}

DIR=""
TYPE="feature"
while [ $# -gt 0 ]; do
  case "$1" in
    --type) TYPE="${2:-}"; shift 2 || { usage; exit 2; } ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "$PROG: unknown option '$1'" >&2; usage; exit 2 ;;
    *) DIR="$1"; shift ;;
  esac
done

[ -n "$DIR" ] || { usage; exit 2; }
if ! printf '%s' "$TYPE" | grep -Eqx "$TAXONOMY"; then
  echo "$PROG: 警告: work_type '$TYPE' は taxonomy ($TAXONOMY) 外です。そのまま使用します" >&2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "$PROG: jq is required" >&2
  exit 1
fi

mkdir -p "$DIR/.claude"
NAME=$(basename "$(cd "$DIR" && pwd)")
ATTRS="workspace=${NAME},work_type=${TYPE}"
FILE="$DIR/.claude/settings.json"

if [ -f "$FILE" ]; then
  EXISTING=$(jq -r '.env.OTEL_RESOURCE_ATTRIBUTES // empty' "$FILE" 2>/dev/null)
  if [ -n "$EXISTING" ]; then
    echo "$PROG: 既にラベルあり: $EXISTING ($FILE) — 変更しません" >&2
    exit 0
  fi
  # 既存 settings.json に env だけマージ
  TMP=$(mktemp)
  jq --arg a "$ATTRS" '.env = (.env // {}) + {OTEL_RESOURCE_ATTRIBUTES: $a}' "$FILE" > "$TMP" && mv "$TMP" "$FILE"
else
  printf '{\n  "env": {\n    "OTEL_RESOURCE_ATTRIBUTES": "%s"\n  }\n}\n' "$ATTRS" > "$FILE"
fi

jq -e . "$FILE" >/dev/null
echo "labeled: $NAME (work_type=$TYPE) -> $FILE"
