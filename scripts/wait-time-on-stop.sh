#!/bin/bash
# Stop hook: Claude が応答を完了した時刻を state file に記録するだけ。
# 設計原則 (requirements 11.1): 常に exit 0 / stderr 無出力 / 重い処理はしない /
#   state file は session_id 別 / 古い state は 24h で自動削除。
{
  STATE_DIR="${TMPDIR:-/tmp}/claude-wait-tracker"
  mkdir -p "$STATE_DIR" 2>/dev/null

  PAYLOAD=$(cat 2>/dev/null || true)
  SESSION_ID=$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)

  if [ -n "$SESSION_ID" ]; then
    NOW_NS=$(python3 -c 'import time; print(time.time_ns())' 2>/dev/null || date +%s%N 2>/dev/null)
    if [ -n "$NOW_NS" ]; then
      printf '%s' "$NOW_NS" > "$STATE_DIR/${SESSION_ID}.last_stop" 2>/dev/null
    fi
  fi

  # 古い state file を掃除（未送信のまま終了したセッションは計上しない設計）
  find "$STATE_DIR" -type f -mtime +1 -delete 2>/dev/null
} >/dev/null 2>&1
exit 0
