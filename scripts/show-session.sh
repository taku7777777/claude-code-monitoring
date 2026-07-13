#!/bin/bash
# show-session.sh — telemetry の session_id からローカルの会話履歴をサルベージ表示する。
#
#   使い方: show-session.sh <session_id> [--full]
#
# ダッシュボードで見つけた session_id (= ~/.claude/projects/*/<session_id>.jsonl の
# ファイル名) を指定すると、実際のやりとり（ユーザー発言 / Claude 応答 / ツール実行）を
# 時系列で表示する。既定では各メッセージを 400 文字に切り詰める（--full で全文）。
# UC1「プロンプトと実行にかかるコストの感覚を掴む」(docs/USECASE.md) の振り返り用。
set -euo pipefail

PROG=$(basename "$0")
SID="${1:-}"
FULL="${2:-}"

if [ -z "$SID" ] || [ "$SID" = "-h" ] || [ "$SID" = "--help" ]; then
  echo "Usage: $PROG <session_id> [--full]" >&2
  exit 2
fi

FILE=$(ls "$HOME/.claude/projects"/*/"$SID".jsonl 2>/dev/null | head -1 || true)
if [ -z "$FILE" ]; then
  echo "$PROG: session '$SID' の履歴が見つかりません ($HOME/.claude/projects/*/$SID.jsonl)" >&2
  exit 1
fi

LIMIT=400
[ "$FULL" = "--full" ] && LIMIT=0

python3 - "$FILE" "$LIMIT" <<'PY'
import json, sys

path, limit = sys.argv[1], int(sys.argv[2])

def clip(s):
    s = " ".join(s.split())
    if limit and len(s) > limit:
        return s[:limit] + f" …(+{len(s)-limit}字)"
    return s

print(f"# {path}\n")
with open(path, encoding="utf-8") as f:
    for line in f:
        try:
            e = json.loads(line)
        except Exception:
            continue
        t = e.get("type")
        if t not in ("user", "assistant"):
            continue
        ts = (e.get("timestamp") or "")[:19].replace("T", " ")
        msg = e.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and (b.get("text") or "").strip():
                role = "USER  " if t == "user" else "CLAUDE"
                print(f"[{ts}] {role} | {clip(b['text'])}")
            elif bt == "tool_use":
                name = b.get("name", "?")
                inp = json.dumps(b.get("input", {}), ensure_ascii=False)
                print(f"[{ts}]  tool  | {name} {clip(inp)}")
            elif bt == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                if isinstance(c, str) and c.strip():
                    print(f"[{ts}]  ->    | {clip(c)}")
PY
