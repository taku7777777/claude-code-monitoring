#!/usr/bin/env python3
"""Rebuild claude-code-workspace as a *filtered Today*: same content as the Today
dashboard, parameterized by `workspace` and `session_id` template variables.

Design (agreed 2026-07-13):
- Reuse uid `claude-code-workspace` so Today's row link keeps working.
- Every panel query is scoped by `workspace=~"$workspace"` (into the stream selector)
  and `| session_id=~"$session_id"` (default `.*` = no filter).
- Panel 12 (本日の累積コスト vs 同曜日平均・上限$150) is reduced to the cumulative
  curve of THIS task only: drop the same-weekday-average target, its cumulative
  transform, and the global $150 threshold line.
- The session-context panel self-links to re-scope THIS page to one session.
"""
import json, copy, re

BASE = "grafana/provisioning/dashboards"
today = json.load(open(f"{BASE}/claude-code-today.json"))
d = copy.deepcopy(today)

SEL = re.compile(r'\{event_name=[^}]*\}')

def scope_expr(expr):
    """Add workspace= into each event_name selector (if absent), then append
    `| session_id=~"$session_id"` after every selector."""
    def add_ws(m):
        sel = m.group(0)
        if "workspace" in sel:
            return sel
        return sel[:-1] + ', workspace=~"$workspace"}'
    expr = SEL.sub(add_ws, expr)
    def add_sess(m):
        return m.group(0) + ' | session_id=~"$session_id"'
    expr = SEL.sub(add_sess, expr)
    return expr

def walk(panels):
    for p in panels:
        if p.get("type") == "row" and p.get("panels"):
            yield from walk(p["panels"])
        else:
            yield p

# --- 1. scope every panel target (except panel 12, handled specially) ---
for p in walk(d["panels"]):
    if p.get("id") == 12:
        continue
    for t in p.get("targets", []):
        if "expr" in t:
            t["expr"] = scope_expr(t["expr"])

# --- 2. panel 12: cumulative curve of THIS task only ---
p12 = [p for p in walk(d["panels"]) if p.get("id") == 12][0]
p12["title"] = "本日の累積コスト（このタスク）"
p12["description"] = ("本日 07:00 からの累積コスト（5分ビン累積・step 5m 固定）を、"
    "絞り込み中の workspace / session について表示。全体の日次予算 $150 と同曜日平均は"
    "タスク単体では意味を持たないため除いてある（それらは Today / Cost Opt の管轄）。")
# keep only refId A (raw_today), scoped
p12["targets"] = [t for t in p12["targets"] if t.get("refId") == "A"]
p12["targets"][0]["expr"] = scope_expr(p12["targets"][0]["expr"])
# drop the raw_avg cumulative transform; keep raw_today cumulative + organize(exclude raw_today)
new_tf = []
for tf in p12.get("transformations", []):
    if tf.get("id") == "calculateField" and tf["options"].get("cumulative", {}).get("field") == "raw_avg":
        continue
    if tf.get("id") == "organize":
        tf["options"]["excludeByName"] = {"raw_today": True}
    new_tf.append(tf)
p12["transformations"] = new_tf
# drop $150 threshold line + baseline series override
p12["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute",
    "steps": [{"color": "green", "value": None}]}
p12["fieldConfig"]["defaults"].setdefault("custom", {})["thresholdsStyle"] = {"mode": "off"}
p12["fieldConfig"]["overrides"] = [ov for ov in p12["fieldConfig"].get("overrides", [])
    if ov.get("matcher", {}).get("options") != "同曜日平均（3週・累積）"]

# --- 3. session-context panel (Today panel id 21): self-link to re-scope THIS page ---
# id で判定する（Today 側でタイトルを変えても壊れないように。title に "context" を含む
# 前提は「コンテキスト遷移」等への改称で崩れた 2026-07-13）
for p in walk(d["panels"]):
    if p.get("id") == 21:
        p["fieldConfig"]["defaults"]["links"] = [{
            "title": "このセッションだけに絞る",
            "url": ("/d/claude-code-workspace/claude-code-workspace"
                    "?var-workspace=$workspace&var-session_id=${__field.labels.session_id}"
                    "&${__url_time_range}"),
        }]

# --- 4. scope the intervention_marker annotation by workspace ---
for ann in d.get("annotations", {}).get("list", []):
    if ann.get("expr", "").startswith('{event_name="intervention_marker"'):
        ann["expr"] = '{event_name="intervention_marker", workspace=~"$workspace"}'

# --- 5. dashboard identity, variables, nav ---
d["uid"] = "claude-code-workspace"
d["title"] = "Workspace / Session（Today 絞り込み）"
d["tags"] = ["claude-code", "workspace"]
d["templating"] = {"list": [
    {
        "name": "workspace", "type": "query", "label": "workspace",
        "datasource": {"type": "loki", "uid": "loki"},
        "definition": "label_values(workspace)",
        "query": {"label": "workspace", "stream": "", "type": 1,
                  "refId": "LokiVariableQueryEditor-VariableQuery"},
        "refresh": 2, "sort": 1, "multi": True, "includeAll": True,
        "allValue": ".*", "current": {}, "options": [], "hide": 0,
    },
    {
        "name": "session_id", "type": "textbox", "label": "session_id",
        "query": ".*", "current": {"text": ".*", "value": ".*"},
        "options": [{"text": ".*", "value": ".*", "selected": True}], "hide": 0,
    },
]}
# canonical 4-button nav (順: Usage / Cost Optimization / Sessions / Prompts)。
# 正本は scripts/apply-nav-links.py。includeVars で現ページの workspace/session を引き継ぐ。
def _nav(u, t):
    return {"type": "link", "title": t, "url": u, "tags": [], "asDropdown": False,
            "targetBlank": False, "includeVars": True, "keepTime": False, "icon": "external link"}
d["links"] = [
    _nav("/d/claude-code-usage/claude-code-usage", "Usage"),
    _nav("/d/claude-code-cost/claude-code-cost", "Cost Optimization"),
    _nav("/d/claude-code-session-list/claude-code-session-list", "Sessions"),
    _nav("/d/claude-code-prompt-list/claude-code-prompt-list", "Prompts"),
]
# keep Today's fixed-today time + hidden picker (variables carry the workspace/session scope)
d["version"] = 1

out = f"{BASE}/claude-code-workspace.json"
with open(out, "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
    f.write("\n")

# validate + sanity checks
json.load(open(out))
blob = open(out).read()
assert '$workspace' in blob and '$session_id' in blob
# every api_request selector must now carry workspace (spot check: no bare {event_name="api_request"} )
bare = re.findall(r'\{event_name="api_request"\}', blob)
print("wrote", out)
print("bare unscoped api_request selectors remaining:", len(bare))
print("panel12 targets:", len(p12["targets"]), "| transforms:", [t['id'] for t in p12['transformations']])
