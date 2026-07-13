#!/usr/bin/env python3
"""Apply the top nav (dashboard `links`) policy across claude-code dashboards.

方針: 共通ナビ（4ボタン）は **workspace / session で絞り込んだドリルページだけ**に出す。
Today などの絞り込んでいないベース／全体ページにはナビを出さない（画面を占有しないため）。

- ナビあり（4ボタン: Usage / Cost Optimization / Sessions / Prompts）:
    claude-code-workspace（filtered-Today）/ claude-code-session-list / claude-code-session
    `includeVars=True` で現ページの workspace/session を引き継ぐ。
- ナビなし（links=[]）: claude-code-today / claude-code(Usage) / claude-code-cost /
    claude-code-context / claude-code-prompt / claude-code-prompt-list

生成器（gen-workspace-filtered-today / gen-session-detail / gen-session-list）も同じ4ボタンを
埋め込むため再生成しても一致する。ここが**正本**。
"""
import json, glob, os

def nav(url, title):
    return {"type": "link", "title": title, "url": url, "tags": [],
            "asDropdown": False, "targetBlank": False,
            "includeVars": True, "keepTime": False, "icon": "external link"}

LINKS = [
    nav("/d/claude-code-usage/claude-code-usage", "Usage"),
    nav("/d/claude-code-cost/claude-code-cost", "Cost Optimization"),
    nav("/d/claude-code-session-list/claude-code-session-list", "Sessions"),
    nav("/d/claude-code-prompt-list/claude-code-prompt-list", "Prompts"),
]

# ナビを出すドリルページ（uid）
NAV_UIDS = {"claude-code-workspace", "claude-code-session-list", "claude-code-session"}

BASE = "grafana/provisioning/dashboards"
changed = []
for f in sorted(glob.glob(f"{BASE}/claude-code*.json")):
    d = json.load(open(f))
    want = [dict(x) for x in LINKS] if d.get("uid") in NAV_UIDS else []
    if d.get("links") == want:
        continue
    d["links"] = want
    with open(f, "w") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    changed.append(f"{os.path.basename(f)}={'nav' if want else 'none'}")

print("updated:", changed or "(all already correct)")
