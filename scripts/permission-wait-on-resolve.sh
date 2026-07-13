#!/bin/bash
# PostToolUse / Stop hook: permission_start が存在すれば表示開始からの差分を
#   permission_wait_observed イベントとして OTLP HTTP 経由で Loki に送信し、state を削除する。
# state が無ければ即 noop（通常の PostToolUse はほぼコストゼロで通過）。
# 設計原則 (requirements 11.1): 常に exit 0 / stderr 無出力 / 重い処理は ( … ) & disown /
#   state file は session_id 別 / 古い state は 24h で自動削除。
{
  # resource attributes の解決: 環境変数優先、無ければ payload cwd から
  # .claude/settings(.local).json を遡る (wait-time-on-prompt.sh と同一方式)。
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

  attr_val() {
    printf '%s' "${RES_ATTRS:-}" | awk -v k="$1" -F, '{
      for (i = 1; i <= NF; i++) {
        n = index($i, "=")
        if (n > 0 && substr($i, 1, n - 1) == k) { print substr($i, n + 1); exit }
      }
    }'
  }

  STATE_DIR="${TMPDIR:-/tmp}/claude-permission-tracker"
  ENDPOINT="${CLAUDE_WAIT_OTLP_LOGS_ENDPOINT:-http://localhost:4318/v1/logs}"

  PAYLOAD=$(cat 2>/dev/null || true)
  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)

  # state が無ければ何もしない（早期リターンで通常経路を軽量化）。
  if [ -n "$SESSION_ID" ] && [ -f "$STATE_DIR/${SESSION_ID}.permission_start" ]; then
    CWD=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
    RES_ATTRS=$(resolve_res_attrs "${CWD:-$PWD}")
    WORKSPACE=$(attr_val workspace)
    WORK_TYPE=$(attr_val work_type)
    [ -n "$WORKSPACE" ] || WORKSPACE="(unset)"
    [ -n "$WORK_TYPE" ] || WORK_TYPE="(unset)"

    # PostToolUse と Stop の並行発火に備え、mv で state をアトミックに claim する
    # (claim できたプロセスだけが送信するため二重送信しない)
    CLAIMED="$STATE_DIR/${SESSION_ID}.permission_claimed.$$"
    if ! mv "$STATE_DIR/${SESSION_ID}.permission_start" "$CLAIMED" 2>/dev/null; then
      exit 0
    fi

    (
      STATE_FILE="$CLAIMED"
      START_NS=$(cat "$STATE_FILE" 2>/dev/null)
      rm -f "$STATE_FILE" 2>/dev/null
      NOW_NS=$(python3 -c 'import time; print(time.time_ns())' 2>/dev/null || date +%s%N 2>/dev/null)
      if printf '%s' "$START_NS" | grep -Eq '^[0-9]+$' && printf '%s' "$NOW_NS" | grep -Eq '^[0-9]+$'; then
        DIFF_NS=$(( NOW_NS - START_NS ))
        if [ "$DIFF_NS" -gt 0 ]; then
          DIFF_SEC=$(LC_ALL=C awk -v d="$DIFF_NS" 'BEGIN { printf "%.3f", d / 1000000000 }')
          LOG_JSON=$(jq -n \
            --arg svc "claude-code-wait-tracker" \
            --arg ws "$WORKSPACE" \
            --arg wt "$WORK_TYPE" \
            --arg ev "permission_wait_observed" \
            --argjson dur "$DIFF_SEC" \
            --arg sid "$SESSION_ID" \
            --arg ns "$NOW_NS" \
            '{resourceLogs:[{
               resource:{attributes:[
                 {key:"service.name",value:{stringValue:$svc}},
                 {key:"workspace",value:{stringValue:$ws}},
                 {key:"work_type",value:{stringValue:$wt}}
               ]},
               scopeLogs:[{
                 scope:{name:"claude_code_wait_tracker",version:"0.1.0"},
                 logRecords:[{
                   timeUnixNano:$ns,
                   observedTimeUnixNano:$ns,
                   severityNumber:9,
                   severityText:"INFO",
                   body:{stringValue:$ev},
                   attributes:[
                     {key:"event.name",value:{stringValue:$ev}},
                     {key:"duration_seconds",value:{doubleValue:$dur}},
                     {key:"session_id",value:{stringValue:$sid}},
                     {key:"workspace",value:{stringValue:$ws}},
                     {key:"work_type",value:{stringValue:$wt}}
                   ]
                 }]
               }]
             }]}')
          if [ -n "$LOG_JSON" ]; then
            curl -sS -m 5 -X POST -H 'Content-Type: application/json' \
              -d "$LOG_JSON" "$ENDPOINT" >/dev/null 2>&1
          fi
        fi
      fi
      find "$STATE_DIR" -type f -mtime +1 -delete 2>/dev/null
    ) >/dev/null 2>&1 &
    disown 2>/dev/null || true
  fi
} >/dev/null 2>&1
exit 0
