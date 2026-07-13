#!/bin/bash
# PostToolUse hook: Edit / Write / MultiEdit / NotebookEdit の変更行数を
#   lines_changed イベントとして OTLP HTTP 経由で Loki に送信する。
# 行数差分は python3 で計算（改行 split による単純 line diff、SDK の diff ロジックとは乖離しうる）。
# 設計原則 (requirements 11.1): 常に exit 0 / stderr 無出力 / 重い処理は ( … ) & disown。
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

  ENDPOINT="${CLAUDE_LINES_OTLP_LOGS_ENDPOINT:-http://localhost:4318/v1/logs}"

  PAYLOAD=$(cat 2>/dev/null || true)
  TOOL=$(printf '%s' "$PAYLOAD" | jq -r '.tool_name // empty' 2>/dev/null)

  # 対象ツール以外は即 noop。
  case "$TOOL" in
    Edit|Write|MultiEdit|NotebookEdit) : ;;
    *) exit 0 ;;
  esac

  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  CWD=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
  RES_ATTRS=$(resolve_res_attrs "${CWD:-$PWD}")
  WORKSPACE=$(attr_val workspace)
  WORK_TYPE=$(attr_val work_type)
  [ -n "$WORKSPACE" ] || WORKSPACE="(unset)"
  [ -n "$WORK_TYPE" ] || WORK_TYPE="(unset)"

  PYCODE='import sys, json

def nlines(s):
    if not s:
        return 0
    return len(s.splitlines())

def diff(old, new):
    o = nlines(old)
    n = nlines(new)
    if n > o:
        return (n - o, 0)
    if o > n:
        return (0, o - n)
    return (0, 0)

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

sid, ws, wt, ns = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
tool = data.get("tool_name", "") or ""
ti = data.get("tool_input", {}) or {}

added = 0
removed = 0
if tool == "Edit":
    a, r = diff(ti.get("old_string", "") or "", ti.get("new_string", "") or "")
    added += a
    removed += r
elif tool == "MultiEdit":
    for e in (ti.get("edits", []) or []):
        if not isinstance(e, dict):
            continue
        a, r = diff(e.get("old_string", "") or "", e.get("new_string", "") or "")
        added += a
        removed += r
elif tool == "Write":
    added += nlines(ti.get("content", "") or "")
elif tool == "NotebookEdit":
    added += nlines(ti.get("new_source", "") or "")
else:
    sys.exit(0)

# added=0 かつ removed=0 は送信しない（ノイズ削減）。
if added == 0 and removed == 0:
    sys.exit(0)

file_path = ti.get("file_path", "") or ti.get("notebook_path", "") or ""

payload = {
    "resourceLogs": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "claude-code-lines-tracker"}},
            {"key": "workspace", "value": {"stringValue": ws}},
            {"key": "work_type", "value": {"stringValue": wt}},
        ]},
        "scopeLogs": [{
            "scope": {"name": "claude_code_lines_tracker", "version": "0.1.0"},
            "logRecords": [{
                "timeUnixNano": ns,
                "observedTimeUnixNano": ns,
                "severityNumber": 9,
                "severityText": "INFO",
                "body": {"stringValue": "lines_changed"},
                "attributes": [
                    {"key": "event.name", "value": {"stringValue": "lines_changed"}},
                    {"key": "lines_added", "value": {"intValue": added}},
                    {"key": "lines_removed", "value": {"intValue": removed}},
                    {"key": "tool_name", "value": {"stringValue": tool}},
                    {"key": "file_path", "value": {"stringValue": file_path}},
                    {"key": "session_id", "value": {"stringValue": sid}},
                    {"key": "workspace", "value": {"stringValue": ws}},
                    {"key": "work_type", "value": {"stringValue": wt}},
                ],
            }],
        }],
    }]
}
sys.stdout.write(json.dumps(payload))
'

  (
    NOW_NS=$(python3 -c 'import time; print(time.time_ns())' 2>/dev/null || date +%s%N 2>/dev/null)
    LOG_JSON=$(printf '%s' "$PAYLOAD" | python3 -c "$PYCODE" "$SESSION_ID" "$WORKSPACE" "$WORK_TYPE" "$NOW_NS" 2>/dev/null)
    if [ -n "$LOG_JSON" ]; then
      curl -sS -m 5 -X POST -H 'Content-Type: application/json' \
        -d "$LOG_JSON" "$ENDPOINT" >/dev/null 2>&1
    fi
  ) >/dev/null 2>&1 &
  disown 2>/dev/null || true
} >/dev/null 2>&1
exit 0
