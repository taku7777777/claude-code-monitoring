#!/bin/bash
# Notification hook (matcher=permission_prompt): permission ダイアログの表示開始時刻を記録するだけ。
# 設計原則 (requirements 11.1): 常に exit 0 / stderr 無出力 / 重い処理はしない /
#   state file は session_id 別 / 古い state は 24h で自動削除。
{
  STATE_DIR="${TMPDIR:-/tmp}/claude-permission-tracker"
  mkdir -p "$STATE_DIR" 2>/dev/null

  PAYLOAD=$(cat 2>/dev/null || true)
  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)
  # matcher で既に絞り込まれている前提だが、念のため notification_type が
  # 明示的に permission_prompt 以外の場合はスキップ（idle_prompt / auth_success 等）。
  NTYPE=$(printf '%s' "$PAYLOAD" | jq -r '.notification_type // empty' 2>/dev/null)

  if [ -n "$SESSION_ID" ] && { [ -z "$NTYPE" ] || [ "$NTYPE" = "permission_prompt" ]; }; then
    NOW_NS=$(python3 -c 'import time; print(time.time_ns())' 2>/dev/null || date +%s%N 2>/dev/null)
    if [ -n "$NOW_NS" ]; then
      printf '%s' "$NOW_NS" > "$STATE_DIR/${SESSION_ID}.permission_start" 2>/dev/null
    fi
  fi

  find "$STATE_DIR" -type f -mtime +1 -delete 2>/dev/null
} >/dev/null 2>&1
exit 0
