#!/bin/bash
# task-outcome.sh — task_outcome を手動送信する CLI。
#
#   使い方: task-outcome.sh <success|failure|abandoned> [note]
#
# Stop hook が自動記録する outcome="completed" (Stop 到達の弱いプロキシ) に対し、
# 人間の判断で success / failure / abandoned を追加記録するためのツール
# (ログベースのため上書きではない。集計は成功系−失敗系の減算で相殺する — CONTRACT §3.2)。
# outcome_proxy="manual" として送信する (CONTRACT §3.2)。
#
# workspace / work_type は OTEL_RESOURCE_ATTRIBUTES を優先し、無ければ CWD から親へ
# .claude/settings(.local).json を遡って解決、それも無ければ
# CLAUDE_WORKSPACE / CLAUDE_WORK_TYPE 環境変数を見る。
#
# 送信先: CLAUDE_OUTCOME_OTLP_LOGS_ENDPOINT (既定 http://localhost:4318/v1/logs)。

set -u

PROG=$(basename "$0")

usage() {
  echo "Usage: $PROG <success|failure|abandoned> [note]" >&2
  echo "  Manually record a task_outcome (outcome_proxy=manual)." >&2
}

OUTCOME="${1:-}"
NOTE="${2:-}"

case "$OUTCOME" in
  success|failure|abandoned) ;;
  ""|"-h"|"--help")
    usage
    [ "$OUTCOME" = "-h" ] || [ "$OUTCOME" = "--help" ]
    exit $?
    ;;
  *)
    echo "$PROG: invalid outcome '$OUTCOME'" >&2
    usage
    exit 2
    ;;
esac

if ! command -v jq >/dev/null 2>&1; then
  echo "$PROG: jq is required" >&2
  exit 1
fi

ENDPOINT="${CLAUDE_OUTCOME_OTLP_LOGS_ENDPOINT:-http://localhost:4318/v1/logs}"

# resource attributes の解決: 環境変数優先、無ければ CWD から
# .claude/settings(.local).json を遡る (hook スクリプト群と同一方式)。
resolve_res_attrs() { # $1: 起点ディレクトリ
  if [ -n "${OTEL_RESOURCE_ATTRIBUTES:-}" ]; then
    printf '%s' "$OTEL_RESOURCE_ATTRIBUTES"
    return
  fi
  local d="$1" f a
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    for f in "$d/.claude/settings.local.json" "$d/.claude/settings.json"; do
      a=$(jq -r '.env.OTEL_RESOURCE_ATTRIBUTES // empty' "$f" 2>/dev/null)
      if [ -n "$a" ]; then printf '%s' "$a"; return; fi
    done
    d="${d%/*}"
  done
}

# workspace / work_type: OTEL_RESOURCE_ATTRIBUTES / settings 解決を優先、次に専用環境変数。
RES_ATTRS=$(resolve_res_attrs "$PWD")
WORKSPACE=$(printf '%s' "$RES_ATTRS" | tr ',' '\n' \
  | awk -F= '$1=="workspace"{sub(/^workspace=/,"");print;exit}')
WORK_TYPE=$(printf '%s' "$RES_ATTRS" | tr ',' '\n' \
  | awk -F= '$1=="work_type"{sub(/^work_type=/,"");print;exit}')
[ -n "$WORKSPACE" ] || WORKSPACE="${CLAUDE_WORKSPACE:-}"
[ -n "$WORK_TYPE" ] || WORK_TYPE="${CLAUDE_WORK_TYPE:-}"
[ -n "$WORKSPACE" ] || WORKSPACE="(unset)"
[ -n "$WORK_TYPE" ] || WORK_TYPE="(unset)"

SESSION_ID="${CLAUDE_SESSION_ID:-}"

NOW_NS=$(python3 -c 'import time;print(int(time.time()*1e9))' 2>/dev/null || printf '%s000000000' "$(date +%s)")

ATTRS=$(jq -nc \
  --arg outcome "$OUTCOME" \
  --arg proxy "manual" \
  --arg session_id "$SESSION_ID" \
  --arg workspace "$WORKSPACE" \
  --arg work_type "$WORK_TYPE" \
  --arg note "$NOTE" \
  '
  [
    {key:"event.name",    value:{stringValue:"task_outcome"}},
    {key:"outcome",       value:{stringValue:$outcome}},
    {key:"outcome_proxy", value:{stringValue:$proxy}},
    {key:"workspace",     value:{stringValue:$workspace}},
    {key:"work_type",     value:{stringValue:$work_type}}
  ]
  + (if $session_id != "" then [{key:"session_id", value:{stringValue:$session_id}}] else [] end)
  + (if $note       != "" then [{key:"note",       value:{stringValue:$note}}]       else [] end)
  ')

LOG_JSON=$(jq -nc \
  --arg now "$NOW_NS" \
  --arg workspace "$WORKSPACE" \
  --arg work_type "$WORK_TYPE" \
  --argjson attrs "$ATTRS" \
  '
  {
    resourceLogs: [{
      resource: {
        attributes: [
          {key:"service.name", value:{stringValue:"claude-code-task-outcome"}},
          {key:"workspace",    value:{stringValue:$workspace}},
          {key:"work_type",    value:{stringValue:$work_type}}
        ]
      },
      scopeLogs: [{
        scope: {name:"claude_code_task_outcome", version:"0.1.0"},
        logRecords: [{
          timeUnixNano: $now,
          observedTimeUnixNano: $now,
          severityNumber: 9,
          severityText: "INFO",
          body: {stringValue:"task_outcome"},
          attributes: $attrs
        }]
      }]
    }]
  }')

if curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d "$LOG_JSON" "$ENDPOINT" >/dev/null 2>&1; then
  echo "task_outcome recorded: outcome=$OUTCOME workspace=$WORKSPACE work_type=$WORK_TYPE"
else
  echo "$PROG: failed to send task_outcome to $ENDPOINT" >&2
  exit 1
fi
