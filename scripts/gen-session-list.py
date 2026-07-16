#!/usr/bin/env python3
"""Generate claude-code-session-list (Session List).

Workspace 単位のセッション一覧（コスト/context/リクエスト数でソート・session_id で検索）。
Context の「セッション一覧」パネルを基に、期間内の合計コスト、開始日時、最終更新、
継続時間、$/1M実効を加え、行リンクを Session Detail へ向ける。
Context 改修で基礎パネルが変わったら再実行する。
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
    "行クリックで Session Detail（このセッションで絞った Usage）へ遷移。"
    "読み方: 開始/最終更新でいつ動いていたか、継続時間で長さ、$/1M実効で単価効率"
    "（高い＝割高／要最適化）、最大context で圧迫度を把握。")
tbl["gridPos"] = {"x": 0, "y": 4, "w": 24, "h": 20}
tbl["fieldConfig"]["defaults"]["custom"]["filterable"] = True  # search

# Context の3指標に、効率単価と稼働時間を算出するための系列を追加する。
tbl["targets"].extend([
    {"refId": "D", "datasource": {"type": "loki", "uid": "loki"},
     "expr": 'sum by (session_id) (sum_over_time({event_name="api_request", workspace=~"$workspace"} | label_format eff_m="{{ divf .effective_tokens 1000000 }}" | unwrap eff_m [$__range]))',
     "queryType": "instant"},
    {"refId": "E", "datasource": {"type": "loki", "uid": "loki"},
     "expr": 'min by (session_id) (min_over_time({event_name="api_request", workspace=~"$workspace"} | label_format ts_ms="{{ divf .observed_timestamp 1000000 }}" | unwrap ts_ms [$__range]))',
     "queryType": "instant"},
    {"refId": "F", "datasource": {"type": "loki", "uid": "loki"},
     "expr": 'max by (session_id) (max_over_time({event_name="api_request", workspace=~"$workspace"} | label_format ts_ms="{{ divf .observed_timestamp 1000000 }}" | unwrap ts_ms [$__range]))',
     "queryType": "instant"},
])

tbl["transformations"] = [
    {"id": "labelsToFields", "options": {}},
    {"id": "joinByField", "options": {"byField": "session_id", "mode": "outer"}},
    {"id": "organize", "options": {"renameByName": {
        "Value #A": "コスト", "Value #B": "最大context", "Value #C": "リクエスト数",
        "Value #D": "実効M", "Value #E": "開始日時", "Value #F": "最終更新"}}},
    {"id": "calculateField", "options": {"mode": "binary", "binary": {
        "left": "コスト", "operator": "/", "right": "実効M"},
        "alias": "$/1M実効", "replaceFields": False}},
    {"id": "calculateField", "options": {"mode": "binary", "binary": {
        "left": "最終更新", "operator": "-", "right": "開始日時"},
        "alias": "継続時間", "replaceFields": False}},
    {"id": "organize", "options": {
        "excludeByName": {"Time": True, **{f"Time {i}": True for i in range(1, 7)}, "実効M": True},
        "renameByName": {}, "indexByName": {"session_id": 0, "開始日時": 1,
        "最終更新": 2, "継続時間": 3, "コスト": 4, "$/1M実効": 5,
        "リクエスト数": 6, "最大context": 7}}},
    {"id": "sortBy", "options": {"sort": [{"field": "コスト", "desc": True}]}},
]

def field_override(name, unit, width, decimals=None):
    properties = [{"id": "unit", "value": unit}]
    if decimals is not None:
        properties.append({"id": "decimals", "value": decimals})
    properties.append({"id": "custom.width", "value": width})
    return {"matcher": {"id": "byName", "options": name}, "properties": properties}

tbl["fieldConfig"]["overrides"] = [
    {"matcher": {"id": "byName", "options": "session_id"}, "properties": [
        {"id": "custom.width", "value": 260},
        {"id": "links", "value": [{
            "title": "このセッションの詳細（Usage 絞り込み）を開く",
            "url": ("/d/claude-code-session/claude-code-session"
                    "?var-workspace=$workspace&var-session_id=${__data.fields.session_id}"
                    "&${__url_time_range}")}]}]},
    field_override("開始日時", "dateTimeAsIso", 170),
    field_override("最終更新", "dateTimeAsIso", 170),
    field_override("継続時間", "ms", 110, 0),
    field_override("コスト", "currencyUSD", 100, 2),
    field_override("$/1M実効", "currencyUSD", 110, 2),
    field_override("リクエスト数", "short", 100, 0),
    field_override("最大context", "short", 110, 0),
]
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
    "description": "選択期間・workspace 内で api_request が1件以上あった session_id のユニーク数。",
    "datasource": {"type": "loki", "uid": "loki"},
    "gridPos": {"x": 0, "y": 0, "w": 12, "h": 4},
    "targets": [{"refId": "A", "datasource": {"type": "loki", "uid": "loki"},
        "expr": 'count(count by (session_id) (count_over_time({event_name="api_request", workspace=~"$workspace"} [$__range])))',
        "queryType": "instant"}],
    "options": {"colorMode": "none", "graphMode": "none", "justifyMode": "auto",
        "orientation": "horizontal", "textMode": "value",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    "fieldConfig": {"defaults": {"unit": "short", "decimals": 0,
        "color": {"mode": "fixed", "fixedColor": "#B0B7C3"}}, "overrides": []},
}

cost = {
    "id": 3, "type": "stat", "title": "合計コスト（期間内）",
    "description": "選択期間・workspace 内の全 api_request のコスト合計（cost_recalc 優先、無ければ cost_usd）。",
    "datasource": {"type": "loki", "uid": "loki"},
    "gridPos": {"x": 12, "y": 0, "w": 12, "h": 4},
    "targets": [{"refId": "A", "datasource": {"type": "loki", "uid": "loki"},
        "expr": 'sum(sum_over_time({event_name="api_request", workspace=~"$workspace"} | label_format cost_v="{{ if .cost_recalc }}{{ .cost_recalc }}{{ else }}{{ .cost_usd }}{{ end }}" | unwrap cost_v [$__range]))',
        "queryType": "instant"}],
    "options": {"colorMode": "none", "graphMode": "none", "justifyMode": "auto",
        "orientation": "horizontal", "textMode": "value",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    "fieldConfig": {"defaults": {"unit": "currencyUSD", "decimals": 2,
        "color": {"mode": "fixed", "fixedColor": "#B0B7C3"}}, "overrides": []},
}

dash = {
    "uid": "claude-code-session-list", "title": "Session List（workspace のセッション）",
    "tags": ["claude-code", "session-list"], "timezone": "browser",
    "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 0,
    "refresh": "1m", "time": {"from": "now-7d", "to": "now"}, "timepicker": {},
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
        "refresh": 2, "sort": 1, "multi": False, "includeAll": True,
        "allValue": ".*", "current": {}, "options": [], "hide": 0}]},
    "panels": [cnt, cost, tbl],
}
out = f"{BASE}/claude-code-session-list.json"
with open(out, "w") as f:
    json.dump(dash, f, ensure_ascii=False, indent=2)
    f.write("\n")
json.load(open(out))
print("wrote", out)
