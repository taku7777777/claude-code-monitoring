#!/usr/bin/env python3
"""Generate claude-code-session-list (Session List).

Workspace 単位のセッション一覧（コスト/context/リクエスト数でソート・session_id で検索）。
Context の「セッション一覧」パネル（複雑な transformation 付き）を再利用し、行リンクを
Session Detail（claude-code-session）へ向ける。Context 改修でこのパネルが変わったら再実行。
"""
import json, copy

BASE = "grafana/provisioning/dashboards"
ctx = json.load(open(f"{BASE}/claude-code-context.json"))

def walk(ps):
    for p in ps:
        if p.get("type") == "row" and p.get("panels"):
            yield from walk(p["panels"])
        else:
            yield p

tbl = copy.deepcopy([x for x in walk(ctx["panels"]) if x.get("id") == 20][0])
# show ALL sessions of the workspace: drop the session_id filter
for t in tbl["targets"]:
    t["expr"] = t["expr"].replace(' | session_id=~"${session_id}"', "")
tbl["id"] = 1
tbl["title"] = "セッション一覧（コスト/context/リクエスト数でソート・session_id で検索）"
tbl["description"] = ("この workspace のセッション一覧。列ヘッダでソート、session_id 列は検索可。"
    "行クリックで Session Detail（このセッションで絞った Usage）へ遷移。")
tbl["gridPos"] = {"x": 0, "y": 3, "w": 24, "h": 20}
tbl["fieldConfig"]["defaults"]["custom"]["filterable"] = True  # search
for ov in tbl["fieldConfig"]["overrides"]:
    if ov["matcher"].get("options") == "session_id":
        for pr in ov["properties"]:
            if pr["id"] == "links":
                pr["value"] = [{
                    "title": "このセッションの詳細（Usage 絞り込み）を開く",
                    "url": ("/d/claude-code-session/claude-code-session"
                            "?var-workspace=$workspace&var-session_id=${__data.fields.session_id}"
                            "&${__url_time_range}"),
                }]

cnt = {
    "id": 2, "type": "stat", "title": "セッション数（期間内）",
    "datasource": {"type": "loki", "uid": "loki"},
    "gridPos": {"x": 0, "y": 0, "w": 24, "h": 3},
    "targets": [{"refId": "A", "datasource": {"type": "loki", "uid": "loki"},
        "expr": 'count(count by (session_id) (count_over_time({event_name="api_request", workspace=~"$workspace"} [$__range])))',
        "queryType": "instant"}],
    "options": {"colorMode": "none", "graphMode": "none", "justifyMode": "auto",
        "orientation": "horizontal", "textMode": "value",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    "fieldConfig": {"defaults": {"unit": "short", "decimals": 0,
        "color": {"mode": "fixed", "fixedColor": "#B0B7C3"}}, "overrides": []},
}

dash = {
    "uid": "claude-code-session-list", "title": "Session List（workspace のセッション）",
    "tags": ["claude-code", "session-list"], "timezone": "browser",
    "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 0,
    "refresh": "30s", "time": {"from": "now-7d", "to": "now"}, "timepicker": {},
    "annotations": {"list": []},
    "links": [  # canonical 4-button nav (正本 scripts/apply-nav-links.py)
        {"type": "link", "title": t, "url": u, "tags": [], "asDropdown": False,
         "targetBlank": False, "includeVars": True, "keepTime": False, "icon": "external link"}
        for u, t in [
            ("/d/claude-code-usage/claude-code-usage", "Usage"),
            ("/d/claude-code-cost/claude-code-cost", "Cost Optimization"),
            ("/d/claude-code-session-list/claude-code-session-list", "Sessions"),
            ("/d/claude-code-prompt-list/claude-code-prompt-list", "Prompts"),
        ]],
    "templating": {"list": [{
        "name": "workspace", "type": "query", "label": "workspace",
        "datasource": {"type": "loki", "uid": "loki"}, "definition": "label_values(workspace)",
        "query": {"label": "workspace", "stream": "", "type": 1,
                  "refId": "LokiVariableQueryEditor-VariableQuery"},
        "refresh": 2, "sort": 1, "multi": True, "includeAll": True,
        "allValue": ".*", "current": {}, "options": [], "hide": 0}]},
    "panels": [cnt, tbl],
}
out = f"{BASE}/claude-code-session-list.json"
with open(out, "w") as f:
    json.dump(dash, f, ensure_ascii=False, indent=2)
    f.write("\n")
json.load(open(out))
print("wrote", out)
