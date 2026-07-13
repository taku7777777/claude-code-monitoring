#!/bin/bash
# intervention-marker.sh — intervention_marker イベントを手動送信する CLI。
#
#   使い方:
#     intervention-marker.sh --category <caching|model_switch|context_reduction|\
#         compaction_tuning|prompt_edit|subagent_policy|other> \
#         --desc "..." [--scope "..."]
#
# 施策(介入)を実施した瞬間にマーカーを打ち、Grafana annotation として全パネルに
# 縦線を重畳して before/after を照合するためのツール (CONTRACT §3.3)。
# intervention_id は iv-<epoch> 形式で自動採番し、成功時に stdout へ出力する
# (このCLIは対話的呼び出し前提のため stdout 出力可)。
#
# workspace / work_type は OTEL_RESOURCE_ATTRIBUTES を優先し、無ければ CWD から親へ
# .claude/settings(.local).json を遡って解決、それも無ければ
# CLAUDE_WORKSPACE / CLAUDE_WORK_TYPE 環境変数を見る。
#
# 送信先: CLAUDE_INTERVENTION_OTLP_LOGS_ENDPOINT (既定 http://localhost:4318/v1/logs)。

set -u

PROG=$(basename "$0")

usage() {
  cat >&2 <<EOF
Usage: $PROG --category <CATEGORY> --desc "..." [--scope "..."]
  CATEGORY: caching | model_switch | context_reduction | compaction_tuning
          | prompt_edit | subagent_policy | other
  Emits an intervention_marker event (intervention_id=iv-<epoch>).
EOF
}

CATEGORY=""
DESC=""
SCOPE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --category) CATEGORY="${2:-}"; shift 2 || { usage; exit 2; } ;;
    --desc)     DESC="${2:-}";     shift 2 || { usage; exit 2; } ;;
    --scope)    SCOPE="${2:-}";    shift 2 || { usage; exit 2; } ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "$PROG: unknown argument '$1'" >&2; usage; exit 2 ;;
  esac
done

case "$CATEGORY" in
  caching|model_switch|context_reduction|compaction_tuning|prompt_edit|subagent_policy|other) ;;
  "") echo "$PROG: --category is required" >&2; usage; exit 2 ;;
  *)  echo "$PROG: invalid --category '$CATEGORY'" >&2; usage; exit 2 ;;
esac

if [ -z "$DESC" ]; then
  echo "$PROG: --desc is required" >&2
  usage
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "$PROG: jq is required" >&2
  exit 1
fi

ENDPOINT="${CLAUDE_INTERVENTION_OTLP_LOGS_ENDPOINT:-http://localhost:4318/v1/logs}"

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

RES_ATTRS=$(resolve_res_attrs "$PWD")
WORKSPACE=$(printf '%s' "$RES_ATTRS" | tr ',' '\n' \
  | awk -F= '$1=="workspace"{sub(/^workspace=/,"");print;exit}')
WORK_TYPE=$(printf '%s' "$RES_ATTRS" | tr ',' '\n' \
  | awk -F= '$1=="work_type"{sub(/^work_type=/,"");print;exit}')
[ -n "$WORKSPACE" ] || WORKSPACE="${CLAUDE_WORKSPACE:-}"
[ -n "$WORK_TYPE" ] || WORK_TYPE="${CLAUDE_WORK_TYPE:-}"
[ -n "$WORKSPACE" ] || WORKSPACE="(unset)"
[ -n "$WORK_TYPE" ] || WORK_TYPE="(unset)"

EPOCH=$(date +%s)
INTERVENTION_ID="iv-${EPOCH}"
NOW_NS=$(python3 -c 'import time;print(int(time.time()*1e9))' 2>/dev/null || printf '%s000000000' "$EPOCH")

ATTRS=$(jq -nc \
  --arg id "$INTERVENTION_ID" \
  --arg category "$CATEGORY" \
  --arg description "$DESC" \
  --arg scope "$SCOPE" \
  --arg workspace "$WORKSPACE" \
  --arg work_type "$WORK_TYPE" \
  '
  [
    {key:"event.name",     value:{stringValue:"intervention_marker"}},
    {key:"intervention_id",value:{stringValue:$id}},
    {key:"category",       value:{stringValue:$category}},
    {key:"description",    value:{stringValue:$description}},
    {key:"workspace",      value:{stringValue:$workspace}},
    {key:"work_type",      value:{stringValue:$work_type}}
  ]
  + (if $scope != "" then [{key:"scope", value:{stringValue:$scope}}] else [] end)
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
          {key:"service.name", value:{stringValue:"claude-code-intervention"}},
          {key:"workspace",    value:{stringValue:$workspace}},
          {key:"work_type",    value:{stringValue:$work_type}}
        ]
      },
      scopeLogs: [{
        scope: {name:"claude_code_intervention", version:"0.1.0"},
        logRecords: [{
          timeUnixNano: $now,
          observedTimeUnixNano: $now,
          severityNumber: 9,
          severityText: "INFO",
          body: {stringValue:"intervention_marker"},
          attributes: $attrs
        }]
      }]
    }]
  }')

if curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d "$LOG_JSON" "$ENDPOINT" >/dev/null 2>&1; then
  echo "$INTERVENTION_ID"
else
  echo "$PROG: failed to send intervention_marker to $ENDPOINT" >&2
  exit 1
fi
