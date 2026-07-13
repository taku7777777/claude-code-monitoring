#!/bin/bash
# task-outcome-on-stop.sh — Claude Code "Stop" hook.
#
# Stop hook が発火したタイミングで task_outcome イベント
#   outcome="completed" / outcome_proxy="stop_reached"
# を Loki (OTLP HTTP) へ送信する。
#
# ★重要な限界: ここで言う "completed" は「Claude が応答を完了して Stop に到達した」
#   という *弱いプロキシ* に過ぎない。ユーザーが結果に満足したか、タスクが本当に
#   成功したかは判定できない（途中放棄・失敗・品質劣化を completed として取りこぼす）。
#   真の成否は CLI (task-outcome.sh <success|failure|abandoned>) で追加記録する (集計は減算相殺 — CONTRACT §3.2)。
#   ダッシュボードの説明文にもこのプロキシ限界を明記すること。
#
# 設計原則 (requirements.md 11.1): 常に exit 0 / stderr 無出力 /
#   重い処理は ( ... ) & disown でバックグラウンド化 / session_id は payload から取得。
{
  PAYLOAD=$(cat 2>/dev/null || true)

  ENDPOINT="${CLAUDE_OUTCOME_OTLP_LOGS_ENDPOINT:-http://localhost:4318/v1/logs}"

  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  PROMPT_ID=$(printf '%s' "$PAYLOAD" | jq -r '.prompt_id // empty' 2>/dev/null)

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

  CWD=$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)
  RES_ATTRS=$(resolve_res_attrs "${CWD:-$PWD}")

  # RES_ATTRS ("workspace=...,work_type=...") から workspace / work_type を抽出
  WORKSPACE=$(printf '%s' "$RES_ATTRS" | tr ',' '\n' \
    | awk -F= '$1=="workspace"{sub(/^workspace=/,"");print;exit}')
  WORK_TYPE=$(printf '%s' "$RES_ATTRS" | tr ',' '\n' \
    | awk -F= '$1=="work_type"{sub(/^work_type=/,"");print;exit}')
  [ -n "$WORKSPACE" ] || WORKSPACE="(unset)"
  [ -n "$WORK_TYPE" ] || WORK_TYPE="(unset)"

  (
    NOW_NS=$(python3 -c 'import time;print(int(time.time()*1e9))' 2>/dev/null || printf '%s000000000' "$(date +%s)")

    ATTRS=$(jq -nc \
      --arg outcome "completed" \
      --arg proxy "stop_reached" \
      --arg session_id "$SESSION_ID" \
      --arg prompt_id "$PROMPT_ID" \
      --arg workspace "$WORKSPACE" \
      --arg work_type "$WORK_TYPE" \
      '
      [
        {key:"event.name",     value:{stringValue:"task_outcome"}},
        {key:"outcome",        value:{stringValue:$outcome}},
        {key:"outcome_proxy",  value:{stringValue:$proxy}},
        {key:"workspace",      value:{stringValue:$workspace}},
        {key:"work_type",      value:{stringValue:$work_type}}
      ]
      + (if $session_id != "" then [{key:"session_id", value:{stringValue:$session_id}}] else [] end)
      + (if $prompt_id  != "" then [{key:"prompt_id",  value:{stringValue:$prompt_id}}]  else [] end)
      ' 2>/dev/null)
    [ -n "$ATTRS" ] || exit 0

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
      }' 2>/dev/null)
    [ -n "$LOG_JSON" ] || exit 0

    curl -sS -m 5 -X POST -H 'Content-Type: application/json' -d "$LOG_JSON" "$ENDPOINT" >/dev/null 2>&1

    # 古い wait-tracker state file の掃除に倣い、他 hook と同居しても無害な範囲で終了。
  ) >/dev/null 2>&1 &
  disown 2>/dev/null || true
} >/dev/null 2>&1
exit 0
