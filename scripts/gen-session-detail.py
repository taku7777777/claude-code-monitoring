#!/usr/bin/env python3
"""Generate claude-code-session (Session Detail) from the Usage dashboard.

Session Detail = **Usage の丸ごとクローン**を `workspace` + `session_id` で絞った画面
（ユーザー方針「session詳細 = Usage をベースに session 絞り込み」）。

Usage は 2026-07-14 のゼロベース再構築で「利用量の記述」に純化された（比率ガードレール・
日次ペーシング・全体/ws別の二重構成が消えた）ため、旧版のような行の取捨（curate）は不要に
なり、全パネルをそのままスコープするだけでよい。Usage 改修時はこれを再実行する。
"""
import json, copy, re

BASE = "grafana/provisioning/dashboards"
usage = json.load(open(f"{BASE}/claude-code.json"))
d = copy.deepcopy(usage)

SEL = re.compile(r'\{event_name=[^}]*\}')

def scope_expr(expr):
    def add_ws(m):
        sel = m.group(0)
        return sel if "workspace" in sel else sel[:-1] + ', workspace=~"$workspace"}'
    expr = SEL.sub(add_ws, expr)
    return SEL.sub(lambda m: m.group(0) + ' | session_id=~"$session_id"', expr)

def walk(panels):
    for p in panels:
        if p.get("type") == "row" and p.get("panels"):
            yield from walk(p["panels"])
        else:
            yield p

for p in walk(d["panels"]):
    for t in p.get("targets", []):
        if "expr" in t:
            t["expr"] = scope_expr(t["expr"])
            # 放置率(16)/待機時間(18)/Permission待ち(19) は wait_time+permission_wait を
            # 加算する。単一 session では一方が空ベクトルになり `A + B` 全体が空→No data に
            # なるため、各 sum(sum_over_time(...)) を `or vector(0)` で 0 埋めして堅牢化する。
            if p.get("id") in (16, 18, 19):
                t["expr"] = t["expr"].replace("[$__range]))", "[$__range]) or vector(0))")

d["uid"] = "claude-code-session"
d["title"] = "Session Detail（このセッション）"
d["tags"] = ["claude-code", "session"]
d["templating"] = {"list": [
    {
        "name": "workspace", "type": "query", "label": "workspace",
        "datasource": {"type": "loki", "uid": "loki"},
        "definition": "label_values(workspace)",
        "query": {"label": "workspace", "stream": "", "type": 1,
                  "refId": "LokiVariableQueryEditor-VariableQuery"},
        "refresh": 2, "sort": 1, "multi": False, "includeAll": True,
        "allValue": ".*", "current": {}, "options": [], "hide": 0,
    },
    {
        "name": "session_id", "type": "textbox", "label": "session_id",
        "query": ".*", "current": {"text": ".*", "value": ".*"},
        "options": [{"text": ".*", "value": ".*", "selected": True}], "hide": 0,
    },
]}
# canonical 4-button nav (正本 scripts/apply-nav-links.py)
def _nav(u, t):
    return {"type": "link", "title": t, "url": u, "tags": [], "asDropdown": False,
            "targetBlank": False, "includeVars": True, "keepTime": False, "icon": "external link"}
d["links"] = [
    _nav("/d/claude-code-usage/claude-code-usage", "Usage"),
    _nav("/d/claude-code-cost/claude-code-cost", "Cost Optimization"),
    _nav("/d/claude-code-session-list/claude-code-session-list", "Sessions"),
    _nav("/d/claude-code-prompt-list/claude-code-prompt-list", "Prompts"),
]
d["version"] = 1

out = f"{BASE}/claude-code-session.json"
with open(out, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
    f.write("\n")

json.load(open(out))
blob = open(out).read()
assert '$session_id' in blob and '$workspace' in blob
print("wrote", out, "| panels:",
      len([p for p in walk(d['panels'])]),
      "| rows:", len([p for p in d['panels'] if p.get('type') == 'row']))
print("bare unscoped api_request:", len(re.findall(r'\{event_name="api_request"\}', blob)))
