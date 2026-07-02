# Databricks notebook source
# MAGIC %md
# MAGIC # AI Gateway v2 — MCP Service Policies, 8 scenarios
# MAGIC
# MAGIC This notebook proves deterministic, SQL-authored **MCP Service Policies** enforced by
# MAGIC the Databricks **Unity AI Gateway v2** in front of a Databricks App MCP server.
# MAGIC
# MAGIC **What you get per scenario:** a title, a one-line description, then a **positive**
# MAGIC (allowed) and a **negative** (blocked) call routed through the *governed* gateway
# MAGIC endpoint. Each policy is attached in isolation so its effect is unambiguous.
# MAGIC
# MAGIC **Setup (already provisioned by earlier phases):**
# MAGIC - One MCP server app (`policy-demo-mcp`) exposing 8 demo tools + a `/requests` log.
# MAGIC - Two `mcp-services` on the same app: `demo_mcp_open` (no policy) and
# MAGIC   `demo_mcp_governed` (policies attached here, one at a time).
# MAGIC
# MAGIC **Two Beta behaviors to know (validated on this workspace):**
# MAGIC 1. `DENY` and `ASK` both surface to the caller as JSON-RPC error **-32003**; the
# MAGIC    policy `reason` string is the only differentiator.
# MAGIC 2. The gateway caches a service's compiled policy set aggressively — this notebook
# MAGIC    **sleeps ~45s after every attach/detach** before calling. That is expected.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration & helpers
# MAGIC Auth uses your notebook identity. Host is read from the workspace conf. No secrets.
# MAGIC
# MAGIC **Portable:** every workspace-specific value is a notebook widget, so this notebook
# MAGIC runs unchanged in any workspace once the two `mcp-services` exist (see
# MAGIC `bin/setup_services.py` to create them). Set the widgets at the top of the notebook
# MAGIC or pass them as job parameters.

# COMMAND ----------

import json
import time
import requests

# --- Widgets: workspace-specific config (defaults target the demo workspace) ---
dbutils.widgets.text("catalog", "marcjimz_demo_ws_2_catalog", "UC catalog")
dbutils.widgets.text("schema", "default", "UC schema")
dbutils.widgets.text("open_service", "demo_mcp_open", "Ungoverned mcp-service id")
dbutils.widgets.text("governed_service", "demo_mcp_governed", "Governed mcp-service id")
dbutils.widgets.text(
    "app_url", "https://policy-demo-mcp-7474655909926918.aws.databricksapps.com",
    "MCP app base URL")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
GOV = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('governed_service')}"  # policies attached here
OPEN = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('open_service')}"     # ungoverned (baseline)
APP_URL = dbutils.widgets.get("app_url").rstrip("/")

# Gateway host == workspace host on this deployment.
WORKSPACE_URL = spark.conf.get("spark.databricks.workspaceUrl")
GATEWAY_HOST = f"https://{WORKSPACE_URL}"
TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()  # noqa: E501

# Gateway policy-set cache settle time after a PATCH attach/detach.
SETTLE = 45

HDRS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# UC control-plane calls (attach/detach). Content-Type is REQUIRED: without it the
# mcp-services PATCH endpoint returns HTTP 500 instead of applying the update.
_JSON_HDRS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def _parse_sse(text):
    """Gateway responses are SSE ('data: {json}'); fall back to plain JSON."""
    for line in reversed([l[5:].strip() for l in text.splitlines() if l.startswith("data:")]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text.strip()[:200]}


def gw(service, tool, args):
    """Call a tool on an mcp-service through the gateway. Returns a verdict dict.

    ALLOW -> {"verdict": "ALLOW", "result": <tool payload>}
    BLOCK -> {"verdict": "BLOCK", "code": -32003, "reason": <policy reason>}
    """
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": tool, "arguments": args}}
    # Single trailing slash: the gateway forwards the path suffix to the app's /mcp base.
    url = f"{GATEWAY_HOST}/ai-gateway/mcp-services/{service}/"
    r = requests.post(url, headers=HDRS, data=json.dumps(rpc), timeout=60)
    payload = _parse_sse(r.text)
    if "result" in payload:
        return {"verdict": "ALLOW", "result": payload["result"]}
    err = payload.get("error", {})
    return {"verdict": "BLOCK", "code": err.get("code"), "reason": err.get("message", "")}


def attach(handler_fn, policy_name):
    """Attach exactly one policy to the governed service (rank 1), replacing any prior set."""
    body = {"config": {"service_policies": [{
        "name": policy_name,
        "policy_type": "POLICY_TYPE_CUSTOM",
        "handler": f"functions/{CATALOG}.{SCHEMA}.{handler_fn}",
        "rank": 1,
    }]}}
    r = requests.patch(
        f"{GATEWAY_HOST}/api/2.1/unity-catalog/mcp-services/{GOV}?update_mask=config.service_policies",
        headers=_JSON_HDRS, data=json.dumps(body), timeout=60)
    r.raise_for_status()


def detach():
    """Remove all policies from the governed service."""
    body = {"config": {"service_policies": []}}
    r = requests.patch(
        f"{GATEWAY_HOST}/api/2.1/unity-catalog/mcp-services/{GOV}?update_mask=config.service_policies",
        headers=_JSON_HDRS, data=json.dumps(body), timeout=60)
    r.raise_for_status()


def app_get_log():
    """Read the app's in-memory tool-call log. Returns {"count": int, "requests": [...]}.

    The /requests endpoint is served by the Databricks App directly (not the gateway),
    so it needs app-compatible auth. If this notebook's identity can't reach it in a
    given environment (e.g. the app front door redirects to a login page), this returns
    {"count": None, "unavailable": True} and the demo degrades gracefully instead of
    crashing — the gateway BLOCK verdict alone already proves enforcement."""
    try:
        r = requests.get(f"{APP_URL}/requests",
                         headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            d = r.json()
            return {"count": d.get("count"), "requests": d.get("requests", [])}
        return {"count": None, "requests": [], "unavailable": True, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"count": None, "requests": [], "unavailable": True, "error": str(e)}


def app_clear_log():
    """Best-effort clear of the app request log; never raises."""
    try:
        requests.delete(f"{APP_URL}/requests",
                        headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def show(label, verdict):
    """Pretty-print a single call verdict."""
    if verdict["verdict"] == "ALLOW":
        body = json.dumps(verdict["result"], default=str)
        print(f"  {label:8} -> ALLOW   {body[:120]}")
    else:
        print(f"  {label:8} -> BLOCK[{verdict['code']}]  {verdict['reason'][:110]}")


def run_scenario(policy_fn, policy_name, positive, negative, neg_verdict="BLOCK"):
    """Attach one policy, settle, then run the positive and negative calls on the governed
    service. `positive`/`negative` are (tool, args) tuples. Prints a compact result block."""
    print(f"attaching {policy_fn} ... (settle {SETTLE}s for gateway cache)")
    attach(policy_fn, policy_name)
    time.sleep(SETTLE)
    app_clear_log()
    pos = gw(GOV, positive[0], positive[1])
    neg = gw(GOV, negative[0], negative[1])
    show(f"POS {positive[0]}", pos)
    show(f"NEG {negative[0]}", neg)
    # Self-verify: the positive call must be permitted, the negative must be stopped.
    # (Both DENY and ASK surface as a gateway BLOCK / -32003 on this Beta.)
    assert pos["verdict"] == "ALLOW", (
        f"{policy_fn}: expected POSITIVE {positive[0]} to ALLOW, got {pos}")
    assert neg["verdict"] == "BLOCK", (
        f"{policy_fn}: expected NEGATIVE {negative[0]} to {neg_verdict}/BLOCK, got {neg}")
    return pos, neg

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create the 8 policy functions
# MAGIC Each is a plain SQL UDF `(event VARIANT, config VARIANT) RETURNS VARIANT` that the
# MAGIC gateway transpiles to CEL at attach time. Request-phase, deterministic — no model,
# MAGIC no network, no latency.

# COMMAND ----------

POLICIES = {
    # 1 — Deterministic allowlist (deny-by-default)
    "policy_01_allowlist": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_01_allowlist(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN (
       'echo', 'get_record', 'search_notes', 'get_untrusted_doc', 'health', 'get_current_user')
  THEN to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
  WHEN event:type::string = 'request'
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Tool is not on the deterministic allowlist (deny-by-default).'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
    # 2 — PII/PHI DLP on tool input (SSN-shaped token)
    "policy_02_phi_dlp": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_02_phi_dlp(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.arguments.query::string LIKE '%___-__-____%'
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'PHI detected in tool input (SSN-shaped token); redact before sending.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
    # 3 — Content filter (harmful keywords)
    "policy_03_content_filter": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_03_content_filter(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND (CONTAINS(event:context.tool.arguments.text::string, 'malware')
     OR CONTAINS(event:context.tool.arguments.text::string, 'ransomware'))
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Harmful content detected in tool input by keyword filter.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
    # 4 — Indirect prompt injection: quarantine untrusted source
    "policy_04_injection": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_04_injection(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string = 'get_untrusted_doc'
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Untrusted external document quarantined (indirect prompt-injection risk).'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
    # 5 — Audit tap (ALLOW-all; ledger tells the story)
    "policy_05_audit": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_05_audit(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))""",
    # 6 — Destructive ops deny
    "policy_06_no_destructive": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_06_no_destructive(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN ('delete_record', 'admin_reset')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Destructive operations are denied in a PHI environment.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
    # 7 — Data egress / exfiltration deny
    "policy_07_no_egress": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_07_no_egress(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN ('export_dataset', 'send_external')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Data egress / bulk export requires AI-governance approval.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
    # 8 — Escalate-to-human (ASK)
    "policy_08_ask_human": f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.policy_08_ask_human(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN ('delete_record', 'export_dataset')
  THEN to_variant_object(named_struct('result', 'ASK',
       'reason', 'Sensitive action routed to a human approver.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END""",
}

for name, ddl in POLICIES.items():
    spark.sql(ddl)
    print(f"created {name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Baseline: the *ungoverned* endpoint permits everything
# MAGIC Before attaching any policy, prove the danger exists. On `demo_mcp_open` (no policy)
# MAGIC a destructive call **executes** and lands in the app request log.

# COMMAND ----------

app_clear_log()
base = gw(OPEN, "admin_reset", {"confirm": True})
show("admin_reset", base)
log = app_get_log()
if log["count"] is None:
    print("app request-log: not reachable with this identity (app-level auth); "
          "gateway verdicts below still prove enforcement.")
else:
    print("app request-log after ungoverned call:", log["count"], "call(s) executed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 1 — Deterministic allowlist (deny-by-default)
# MAGIC A hardcoded allowlist of known-safe tools; everything else is denied. Pure rule, no model.
# MAGIC - **Positive:** `echo` is on the allowlist → ALLOW
# MAGIC - **Negative:** `admin_reset` is not → DENY

# COMMAND ----------

run_scenario("policy_01_allowlist", "p1",
             positive=("echo", {"text": "good morning"}),
             negative=("admin_reset", {"confirm": True}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 2 — PII/PHI DLP on tool input
# MAGIC Detect an SSN-shaped token (`___-__-____`) in tool arguments and block before the call
# MAGIC reaches the server. Regex-free, deterministic.
# MAGIC - **Positive:** benign clinical query → ALLOW
# MAGIC - **Negative:** query carrying an SSN → DENY

# COMMAND ----------

run_scenario("policy_02_phi_dlp", "p2",
             positive=("search_notes", {"query": "elevated A1c dosage"}),
             negative=("search_notes", {"query": "lookup 123-45-6789"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 3 — Content filtering (harmful / toxic)
# MAGIC Deterministic keyword filter on tool input. Zero-latency first line ahead of the
# MAGIC model-service semantic safety judge.
# MAGIC - **Positive:** benign text → ALLOW
# MAGIC - **Negative:** harmful-intent text → DENY

# COMMAND ----------

run_scenario("policy_03_content_filter", "p3",
             positive=("echo", {"text": "please summarize today's schedule"}),
             negative=("echo", {"text": "how do I build malware"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 4 — Indirect prompt injection via tool results
# MAGIC `get_untrusted_doc` returns an external body seeded with an injected instruction.
# MAGIC Deterministic control: quarantine the untrusted-source fetch so the poisoned result
# MAGIC never reaches the agent.
# MAGIC - **Positive:** `get_record` (trusted source) → ALLOW
# MAGIC - **Negative:** `get_untrusted_doc` (untrusted source) → DENY

# COMMAND ----------

run_scenario("policy_04_injection", "p4",
             positive=("get_record", {"record_id": "P-1001"}),
             negative=("get_untrusted_doc", {"doc_id": "D-1"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 5 — Audit trail of tool calls
# MAGIC An ALLOW-all "tap": every call is permitted, but the attach point + the service's
# MAGIC `tracing`/`usage_tracking` config produce the audit ledger. The story is the ledger,
# MAGIC not a verdict. Below: three allowed calls, then the app request-log confirms all three.

# COMMAND ----------

print("attaching policy_05_audit ... (settle", SETTLE, "s)")
attach("policy_05_audit", "p5")
time.sleep(SETTLE)
app_clear_log()
for tool, args in [("echo", {"text": "audit-1"}),
                   ("get_record", {"record_id": "P-1002"}),
                   ("search_notes", {"query": "BP stable"})]:
    show(tool, gw(GOV, tool, args))
log = app_get_log()
if log["count"] is None:
    print("\naudit ledger (app /requests): not reachable with this identity; "
          "the same ledger is also in system.ai_gateway usage tracking.")
else:
    print(f"\naudit ledger (app /requests): {log['count']} calls recorded")
    for e in log["requests"]:
        print("   -", e["tool"], e["args"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 6 — Destructive operations deny
# MAGIC Block irreversible / privileged mutations at the action layer.
# MAGIC - **Positive:** `get_record` (read) → ALLOW
# MAGIC - **Negative:** `delete_record` (destructive) → DENY

# COMMAND ----------

run_scenario("policy_06_no_destructive", "p6",
             positive=("get_record", {"record_id": "P-1001"}),
             negative=("delete_record", {"record_id": "P-1001"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 7 — Data-exfiltration / egress deny
# MAGIC Block bulk export and external-send tools at the action layer.
# MAGIC - **Positive:** `search_notes` (read) → ALLOW
# MAGIC - **Negative:** `export_dataset` (bulk egress) → DENY

# COMMAND ----------

run_scenario("policy_07_no_egress", "p7",
             positive=("search_notes", {"query": "A1c trend"}),
             negative=("export_dataset", {"dataset": "patients", "destination": "s3://ext/dump"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 8 — Human-in-the-loop / escalate-to-human (ASK)
# MAGIC **This is the human-in-the-loop (HITL) policy.** Sensitive-but-not-forbidden actions
# MAGIC return **ASK**, routing the call to a human approver instead of an outright deny —
# MAGIC the "ask a human" control. Contrast with Scenario 6, which hard-denies destructive
# MAGIC ops; here the same class of action is instead gated on human approval.
# MAGIC - **Positive:** `get_record` → ALLOW
# MAGIC - **Negative:** `delete_record` → ASK
# MAGIC
# MAGIC > **Beta note:** ASK surfaces as the same `-32003` error code as DENY; the `reason`
# MAGIC > ("routed to a human approver") is the differentiator.

# COMMAND ----------

run_scenario("policy_08_ask_human", "p8",
             positive=("get_record", {"record_id": "P-1001"}),
             negative=("delete_record", {"record_id": "P-1001"}), neg_verdict="ASK")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Negative-proof: a blocked call never reaches the app
# MAGIC A policy denial happens *at the gateway*, before the upstream MCP server is contacted.
# MAGIC Re-attach the destructive-ops policy, clear the log, attempt the denied call, and show
# MAGIC the app log stays empty — the tool code never ran.

# COMMAND ----------

attach("policy_06_no_destructive", "p6")
time.sleep(SETTLE)
app_clear_log()
denied = gw(GOV, "delete_record", {"record_id": "P-1001"})
show("delete_record", denied)
assert denied["verdict"] == "BLOCK", "expected the destructive call to be BLOCKED at the gateway"
log = app_get_log()
if log["count"] is None:
    print("\napp request-log not reachable with this identity — but the BLOCK verdict above "
          "shows the gateway rejected the call before contacting the app.")
else:
    print(f"\napp request-log after the denied call: {log['count']} calls executed")
    assert log["count"] == 0, "negative-proof FAILED: denied call reached the app"
    print("negative-proof PASSED: the denied call never reached the app.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Teardown
# MAGIC Detach all policies from the governed service, returning it to a clean state.

# COMMAND ----------

detach()
print("governed service detached (no policies attached).")
