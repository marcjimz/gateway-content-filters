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
# MAGIC 2. The gateway caches a service's compiled policy set and applies an attach after a
# MAGIC    non-deterministic delay. To stay **deterministic and repeatable**, this notebook
# MAGIC    **polls the endpoint until the new policy is provably live** (positive ALLOWs and
# MAGIC    negative BLOCKs) before asserting — no blind fixed sleep. Warm-up adds ~30–90s/scenario.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario map — what each policy proves & the customer requirement it closes
# MAGIC
# MAGIC Each scenario attaches one SQL policy to the governed MCP service and shows an **allowed**
# MAGIC vs. **blocked** call. Every scenario maps to a control the customer's AI-security
# MAGIC assessment originally scoped **Partial** — this demo shows the gateway closing that gap.
# MAGIC
# MAGIC | # | Scenario | Policy | Customer requirement (scoped _Partial_) |
# MAGIC |---|----------|--------|------------------------------------------|
# MAGIC | 1 | Deterministic allowlist (deny-by-default) | `policy_01_allowlist` | Shadow AI Detection & Governance |
# MAGIC | 2 | PII/PHI DLP on tool input | `policy_02_phi_dlp` | PII & PHI Detection and Redaction |
# MAGIC | 3 | Content filtering (harmful/toxic) | `policy_03_content_filter` | Threat Detection for Azure OpenAI (Prompt Shield) |
# MAGIC | 4 | Indirect prompt-injection quarantine | `policy_04_injection` | Groundedness Detection Capability |
# MAGIC | 5 | Audit trail of tool calls | `policy_05_audit` | AI Hub Audit Logs / Prompt & Response Logging |
# MAGIC | 6 | Destructive ops deny | `policy_06_no_destructive` | Quarantine / Terminate Risky Agents (auto-deny) |
# MAGIC | 7 | Data-exfiltration / egress deny | `policy_07_no_egress` | Insider Risk Detection for AI Usage Anomalies |
# MAGIC | 8 | Escalate-to-human (ASK) | `policy_08_ask_human` | Quarantine / Terminate Risky Agents (HITL angle) |
# MAGIC
# MAGIC **What `run_scenario()` does:** it attaches one policy to the governed service, then
# MAGIC **polls until the policy is provably live** (the positive call ALLOWs _and_ the negative
# MAGIC call BLOCKs — deterministic, no blind sleep), clears the app request-log, re-runs the
# MAGIC positive and negative calls, prints both verdicts, and **asserts** `POS == ALLOW` and
# MAGIC `NEG == BLOCK`. A failed assert halts the notebook — so a clean run is itself proof that
# MAGIC every enforcement verdict is correct.
# MAGIC
# MAGIC > Tip: the cell right before each scenario calls `print_policy(...)` (SQL policies) or
# MAGIC > `print_builtin(...)` (the semantic LLM-judge guardrails in 3b/3c) so you can read the
# MAGIC > exact rule / attach config being enforced before the scenario runs.

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
# Scenario 4 addendum: reachable stand-in for an external security API (e.g. a
# Zscaler / prompt-injection scanner). google.com is used only to prove live egress
# from a UC function; point this at your real security endpoint in production.
dbutils.widgets.text("ext_security_host", "https://www.google.com",
                     "External security API host (S4 illustration)")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
GOV = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('governed_service')}"  # policies attached here
OPEN = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('open_service')}"     # ungoverned (baseline)
APP_URL = dbutils.widgets.get("app_url").rstrip("/")
EXT_SECURITY_HOST = dbutils.widgets.get("ext_security_host").rstrip("/")

# Gateway host == workspace host on this deployment.
WORKSPACE_URL = spark.conf.get("spark.databricks.workspaceUrl")
GATEWAY_HOST = f"https://{WORKSPACE_URL}"
TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()  # noqa: E501

# The gateway caches a service's compiled policy set and reflects an attach/detach
# after a non-deterministic propagation delay. Rather than a blind fixed sleep (a
# gamble that makes scenarios flaky), we POLL the governed endpoint until the new
# policy is provably live — see wait_until_enforced(). SETTLE is only used by the
# audit tap (Scenario 5), whose ALLOW-all policy has no observable verdict flip to
# poll on and whose correctness does not depend on enforcement timing.
SETTLE = 45
POLL_EVERY = 8        # seconds between readiness probes
POLL_TIMEOUT = 300    # hard cap; propagation is typically well under 90s

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


def attach_builtin(handler, policy_name, model_service, phase, rank=1):
    """Attach one *built-in* LLM-judge guardrail to the governed service.

    Unlike the custom SQL policies (deterministic CEL), built-ins are semantic
    judges evaluated by a foundation model. They attach with a different shape:
    `policy_type=POLICY_TYPE_BUILTIN`, a bare `system.ai.*` handler (no
    `functions/` prefix), and `options={model_service, phases}`. Validated on
    this workspace:
      - handler      : one of system.ai.block_unsafe_content, system.ai.block_pii
                       (block_jailbreak / block_hallucination are model-service
                       only — rejected on an mcp_service).
      - model_service: any chat model serving endpoint; databricks-gpt-5-nano here.
      - phase        : 'pre_call' (inspect the request) or 'post_call' (inspect the
                       tool result). Exactly ONE phase per attachment (the options
                       `phases` array has max size 1); attach twice for both.
    A built-in BLOCK surfaces the *generic* reason "Access denied: this request is
    not permitted by a policy on this service." — built-ins do not emit a custom
    reason string the way the SQL policies do.
    """
    body = {"config": {"service_policies": [{
        "name": policy_name,
        "policy_type": "POLICY_TYPE_BUILTIN",
        "handler": handler,
        "options": {"model_service": model_service, "phases": [phase]},
        "rank": rank,
    }]}}
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


def wait_until_enforced(positive, negative):
    """Poll the governed endpoint until the freshly-attached policy is provably live:
    the positive call ALLOWs AND the negative call BLOCKs. This is the deterministic
    replacement for a blind fixed sleep — the gateway's policy-set cache propagates
    after a non-deterministic delay, so we probe the actual steady-state verdicts
    instead of guessing a duration. Requiring BOTH conditions also guards against a
    stale *previous* policy giving a false-ready signal on the negative alone.
    Returns (seconds_waited, ready: bool); on timeout the caller's asserts surface it."""
    waited = 0
    while waited <= POLL_TIMEOUT:
        p = gw(GOV, positive[0], positive[1])
        n = gw(GOV, negative[0], negative[1])
        if p["verdict"] == "ALLOW" and n["verdict"] == "BLOCK":
            return waited, True
        time.sleep(POLL_EVERY)
        waited += POLL_EVERY
    return waited, False


def run_scenario(policy_fn, policy_name, positive, negative, neg_verdict="BLOCK"):
    """Attach one policy, wait until it is provably enforced, then run the positive and
    negative calls on the governed service. `positive`/`negative` are (tool, args) tuples.
    Deterministic & repeatable: the scenario never asserts until the gateway reflects the
    new policy (or POLL_TIMEOUT elapses). Prints a compact result block."""
    print(f"attaching {policy_fn} ... (polling until enforced)")
    attach(policy_fn, policy_name)
    waited, ready = wait_until_enforced(positive, negative)
    print(f"  policy live after ~{waited}s" if ready
          else f"  WARNING: not enforced after {waited}s — asserting anyway")
    # Clean, logged run for display + assert + negative-proof log semantics.
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


def run_llm_judge(handler, policy_name, model_service, phase, positive, negative):
    """Attach one built-in LLM-judge guardrail, wait until it is provably enforced,
    then run the positive/negative calls and assert. Same self-verifying contract as
    run_scenario() but for the semantic (model-evaluated) policy path instead of the
    deterministic SQL path. `positive`/`negative` are (tool, args) tuples."""
    print(f"attaching built-in {handler} [{phase}, {model_service}] ... (polling until enforced)")
    attach_builtin(handler, policy_name, model_service, phase)
    waited, ready = wait_until_enforced(positive, negative)
    print(f"  guardrail live after ~{waited}s" if ready
          else f"  WARNING: not enforced after {waited}s — asserting anyway")
    pos = gw(GOV, positive[0], positive[1])
    neg = gw(GOV, negative[0], negative[1])
    show(f"POS {positive[0]}", pos)
    show(f"NEG {negative[0]}", neg)
    assert pos["verdict"] == "ALLOW", (
        f"{handler}: expected POSITIVE {positive[0]} to ALLOW, got {pos}")
    assert neg["verdict"] == "BLOCK", (
        f"{handler}: expected NEGATIVE {negative[0]} to BLOCK, got {neg}")
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


def print_policy(name):
    """Print the SQL body of a policy so you can inspect exactly what will be enforced
    before the scenario attaches and runs it."""
    print(f"# {name}")
    print(POLICIES[name].strip())


def print_builtin(handler, model_service, phase, rank=1):
    """Built-in analog of print_policy(): show the exact attach config for a native
    LLM-judge guardrail before it runs. Built-ins have no SQL body — the 'policy' *is*
    this attach shape (POLICY_TYPE_BUILTIN + a system.ai.* handler + options) that the
    gateway evaluates by calling the model_service. Printing it makes the semantic-path
    control just as inspectable as the deterministic SQL policies."""
    cfg = {
        "policy_type": "POLICY_TYPE_BUILTIN",
        "handler": handler,
        "options": {"model_service": model_service, "phases": [phase]},
        "rank": rank,
    }
    print(f"# built-in guardrail: {handler}  (semantic judge, no SQL body)")
    print(json.dumps(cfg, indent=2))

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
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *Shadow AI Detection & Governance*
# MAGIC > "Identifies unauthorized AI service usage and enforces approved model policies."
# MAGIC > **Sheet gap:** "Detection capabilities exist but **blocking not enabled**."
# MAGIC > **How this closes it:** a deterministic deny-by-default allowlist evaluated *at the
# MAGIC > gateway* turns detection into enforcement — any tool not explicitly approved is blocked
# MAGIC > before it runs, no endpoint agent required.

# COMMAND ----------

print_policy("policy_01_allowlist")

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
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *PII & PHI Detection and Redaction*
# MAGIC > "Detects and redacts PII and PHI in AI model inputs and outputs to prevent data leakage."
# MAGIC > **Sheet gap:** "RegEx-based detection currently in place… **EDM implementation pending**;
# MAGIC > baseline DLP is proxy-dependent and limited to supported services."
# MAGIC > **How this closes it:** the SSN-shape rule executes inline at the gateway *before* the
# MAGIC > call reaches the model/app — enforcement doesn't depend on downstream EDM, labeling
# MAGIC > accuracy, or a specific proxy path.

# COMMAND ----------

print_policy("policy_02_phi_dlp")

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
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *Threat Detection for Azure OpenAI*
# MAGIC > "Real-time alerts for unauthorized API calls, policy violations, and suspicious model
# MAGIC > access patterns."
# MAGIC > **Sheet gap:** "Multi-layer approach… Microsoft Foundry has AI Prompt Shield solutions
# MAGIC > but **nothing has been tested yet**."
# MAGIC > **How this closes it:** a deterministic keyword filter gives a zero-latency, provably
# MAGIC > enforced first line ahead of (not instead of) a semantic safety judge — a tested,
# MAGIC > policy-as-code control that ships today.

# COMMAND ----------

print_policy("policy_03_content_filter")

# COMMAND ----------

run_scenario("policy_03_content_filter", "p3",
             positive=("echo", {"text": "please summarize today's schedule"}),
             negative=("echo", {"text": "how do I build malware"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Scenario 3b — the LLM-as-a-judge path (native built-in guardrail)
# MAGIC The keyword rule above is the deterministic first line. MCP service policies also
# MAGIC support a **second path: native, model-evaluated guardrails** in `system.ai` — an
# MAGIC LLM-as-a-judge that catches harmful intent a keyword list would miss (no exact
# MAGIC substring match required). It attaches by API just like a custom policy, but with
# MAGIC `policy_type=POLICY_TYPE_BUILTIN`, a `system.ai.block_*` handler, and an
# MAGIC `options={model_service, phases}` block. Here the judge model is `databricks-gpt-5-nano`.
# MAGIC - **Positive:** benign clinical text → ALLOW
# MAGIC - **Negative:** harmful-intent text with **no filtered keyword** → BLOCK (the judge reasons about intent)
# MAGIC
# MAGIC > **Two paths, one control point.** Deterministic (zero-latency, provable, keyword/pattern)
# MAGIC > and semantic (model judge, generalizes) guardrails attach to the *same* governed MCP
# MAGIC > service. A built-in BLOCK returns the generic reason *"not permitted by a policy"* — the
# MAGIC > judge verdict itself, not a custom string. `block_jailbreak` and `block_hallucination`
# MAGIC > are model-endpoint guardrails (not valid on an MCP service); `block_unsafe_content` and
# MAGIC > `block_pii` are the two that apply to MCP services.

# COMMAND ----------

print_builtin("system.ai.block_unsafe_content", "databricks-gpt-5-nano", "pre_call")

# COMMAND ----------

run_llm_judge("system.ai.block_unsafe_content", "p3_llm_unsafe",
              model_service="databricks-gpt-5-nano", phase="pre_call",
              positive=("echo", {"text": "summarize the patient discharge plan"}),
              negative=("echo", {"text": "detailed instructions to synthesize a nerve agent at home"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Scenario 3c — semantic PII/PHI judge (`system.ai.block_pii`)
# MAGIC Scenario 2 caught an SSN by *shape* (deterministic). The built-in `block_pii` judge is the
# MAGIC semantic complement: it recognizes PII/PHI described in natural language — names, emails,
# MAGIC phone numbers, MRNs — without a hand-written pattern for each. Same MCP service, same
# MAGIC attach mechanics, `databricks-gpt-5-nano` as the evaluator.
# MAGIC - **Positive:** a question with no personal data → ALLOW
# MAGIC - **Negative:** a message packed with identifiers → BLOCK

# COMMAND ----------

print_builtin("system.ai.block_pii", "databricks-gpt-5-nano", "pre_call")

# COMMAND ----------

run_llm_judge("system.ai.block_pii", "p3_llm_pii",
              model_service="databricks-gpt-5-nano", phase="pre_call",
              positive=("echo", {"text": "what time does the cardiology clinic open"}),
              negative=("echo", {"text": "patient John Q Smith SSN 123-45-6789 email jsmith@example.com phone 617-555-0142"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 4 — Indirect prompt injection via tool results
# MAGIC `get_untrusted_doc` returns an external body seeded with an injected instruction.
# MAGIC Deterministic control: quarantine the untrusted-source fetch so the poisoned result
# MAGIC never reaches the agent.
# MAGIC - **Positive:** `get_record` (trusted source) → ALLOW
# MAGIC - **Negative:** `get_untrusted_doc` (untrusted source) → DENY
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *Groundedness Detection Capability*
# MAGIC > "Validates that AI responses are grounded in provided source documents, preventing
# MAGIC > hallucinated or fabricated information."
# MAGIC > **Sheet gap:** "Would need a multi-layer approach… **nothing has been tested yet**."
# MAGIC > **How this closes it:** quarantining the untrusted-source fetch stops poisoned/injected
# MAGIC > content from ever reaching the agent context — a deterministic upstream control that
# MAGIC > complements downstream groundedness scoring.

# COMMAND ----------

print_policy("policy_04_injection")

# COMMAND ----------

run_scenario("policy_04_injection", "p4",
             positive=("get_record", {"record_id": "P-1001"}),
             negative=("get_untrusted_doc", {"doc_id": "D-1"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Scenario 4b — calling out to an external security service (Zscaler-style)
# MAGIC The quarantine rule above is the deterministic, in-gateway control. Some customers
# MAGIC also want the gateway/app layer to **call an external security API** (a Zscaler /
# MAGIC prompt-injection scanner / DLP service) to score untrusted content before it is
# MAGIC trusted. On Databricks that outbound call is a **Unity Catalog HTTP connection** +
# MAGIC the built-in `http_request` SQL function, wrapped in a UC function.
# MAGIC
# MAGIC > **Hard constraint (validated):** `http_request` **cannot** live inside a service
# MAGIC > policy — the policy body must transpile to CEL, and any policy that calls
# MAGIC > `http_request` (or `ai_query`) fails attach with *"SQL cannot be transpiled to
# MAGIC > CEL."* So the external-scan pattern is a **UC function / app-layer** control that
# MAGIC > runs *alongside* the deterministic policy, not a replacement for it. The policy is
# MAGIC > the enforcement gate; the external call is enrichment/scoring.
# MAGIC
# MAGIC The cell below proves the outbound path is real: it creates a UC HTTP connection to
# MAGIC `EXT_SECURITY_HOST` (defaults to `google.com` purely as a reachable stand-in — point
# MAGIC it at your real scanner in production), wraps `http_request` in a UC function, invokes
# MAGIC it, and shows the live HTTP status + a slice of the response body.

# COMMAND ----------

# Reachable stand-in for an external security/DLP API. A bare HTTP connection defaults
# to OAuth DCR discovery (fails on non-OAuth hosts); supplying bearer_token skips DCR.
# Swap host + auth for your real scanner (e.g. Zscaler / a prompt-injection endpoint).
_conn = "s4_ext_security_conn"
_scan_fn = f"{CATALOG}.{SCHEMA}.s4_scan_external"
spark.sql(f"""
CREATE CONNECTION IF NOT EXISTS {_conn} TYPE HTTP
OPTIONS (host '{EXT_SECURITY_HOST}', port '443', base_path '/', bearer_token 'none')
""")
spark.sql(f"""
CREATE OR REPLACE FUNCTION {_scan_fn}(payload STRING)
RETURNS STRUCT<status_code INT, body_head STRING>
RETURN
  SELECT named_struct(
    'status_code', resp.status_code,
    'body_head', substr(resp.text, 1, 160))
  FROM (SELECT http_request(
          conn => '{_conn}', method => 'GET', path => '/') AS resp)
""")
scan = spark.sql(
    f"SELECT {_scan_fn}('untrusted document body to scan') AS r").collect()[0]["r"]
print(f"external security API call -> HTTP {scan['status_code']}")
print(f"  response body (head): {scan['body_head'][:120]}")
assert scan["status_code"] == 200, (
    f"expected the external security API to return 200, got {scan['status_code']}")
print("outbound egress from a UC function is live — wire this to your real scanner.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Scenario 4c — proof: an `http_request` policy **cannot** be the gate (live)
# MAGIC The cell above proved the outbound call works at the *function* layer. The obvious next
# MAGIC question is: *can we just put that scanner call inside a policy so the gateway blocks on
# MAGIC the scanner's verdict?* The answer is **no**, and this cell proves it live rather than
# MAGIC asserting it. A policy body must transpile to CEL (a small, side-effect-free expression
# MAGIC language); `http_request` makes a network call and has no CEL equivalent, so **attach is
# MAGIC rejected**.
# MAGIC
# MAGIC We try **both** shapes and expect both to fail identically:
# MAGIC 1. `http_request` **directly** in the policy body.
# MAGIC 2. `http_request` **hidden behind a wrapper UC function** the policy calls — the
# MAGIC    transpiler inlines the wrapper and still rejects it (indirection does not help).
# MAGIC
# MAGIC > **Takeaway (architecture):** the enforcement *gate* is deterministic CEL (no I/O, no
# MAGIC > latency, provable). An external scanner is **enrichment** that runs at the app / UC
# MAGIC > function layer *alongside* the gate — e.g. score content, write a verdict to a table
# MAGIC > or tool argument, then a deterministic policy blocks on that verdict. Self-verifying:
# MAGIC > this cell **asserts the attach fails** with the transpile error.

# COMMAND ----------

def _try_attach_raw(handler_fqn, policy_name):
    """Attempt a raw policy attach WITHOUT raising; return (ok, message). Used to prove
    an http_request policy is rejected at attach time (transpile-to-CEL failure)."""
    body = {"config": {"service_policies": [{
        "name": policy_name, "policy_type": "POLICY_TYPE_CUSTOM",
        "handler": f"functions/{handler_fqn}", "rank": 1}]}}
    r = requests.patch(
        f"{GATEWAY_HOST}/api/2.1/unity-catalog/mcp-services/{GOV}?update_mask=config.service_policies",
        headers=_JSON_HDRS, data=json.dumps(body), timeout=60)
    return r.ok, f"HTTP {r.status_code}: {r.text[:200]}"


# Ensure the connection exists (idempotent) so the scanner policy functions compile.
spark.sql(f"""
CREATE CONNECTION IF NOT EXISTS {_conn} TYPE HTTP
OPTIONS (host '{EXT_SECURITY_HOST}', port '443', base_path '/', bearer_token 'none')
""")

# (1) http_request DIRECTLY in the policy body.
_p_direct = f"{CATALOG}.{SCHEMA}.policy_ext_scan_direct"
spark.sql(f"""
CREATE OR REPLACE FUNCTION {_p_direct}(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND (SELECT http_request(conn => '{_conn}', method => 'GET', path => '/').status_code) = 200
  THEN to_variant_object(named_struct('result','DENY','reason','External scanner flagged content.'))
  ELSE to_variant_object(named_struct('result','ALLOW','reason','')) END""")

# (2) http_request hidden behind a wrapper UC function the policy calls.
_p_wrap_fn = f"{CATALOG}.{SCHEMA}.ext_scan_status"
spark.sql(f"""
CREATE OR REPLACE FUNCTION {_p_wrap_fn}(payload STRING)
RETURNS INT
RETURN (SELECT http_request(conn => '{_conn}', method => 'GET', path => '/').status_code)""")
_p_wrap = f"{CATALOG}.{SCHEMA}.policy_ext_scan_wrap"
spark.sql(f"""
CREATE OR REPLACE FUNCTION {_p_wrap}(event VARIANT, config VARIANT)
RETURNS VARIANT LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND {_p_wrap_fn}(event:context.tool.arguments.text::string) = 200
  THEN to_variant_object(named_struct('result','DENY','reason','External scanner flagged content.'))
  ELSE to_variant_object(named_struct('result','ALLOW','reason','')) END""")

for _fqn, _label in ((_p_direct, "http_request directly in policy"),
                     (_p_wrap, "http_request behind a wrapper UC function")):
    ok, msg = _try_attach_raw(_fqn, "p_ext_scan_attempt")
    print(f"  attach [{_label}] -> {'ATTACHED?!' if ok else 'REJECTED'}: {msg}")
    assert not ok, f"expected attach to be rejected for {_fqn}, but it succeeded"
    assert "transpiled to CEL" in msg, f"expected transpile-to-CEL rejection, got: {msg}"

print("proven: an http_request policy cannot be the enforcement gate "
      "(both direct and wrapper forms rejected at attach) — external scan is an "
      "app/function-layer control alongside the deterministic policy.")

# Clean up the illustration objects (idempotent). A rejected attach never changes the
# service's policy set, so the next scenario's attach() still starts from a clean slate.
spark.sql(f"DROP FUNCTION IF EXISTS {_p_direct}")
spark.sql(f"DROP FUNCTION IF EXISTS {_p_wrap}")
spark.sql(f"DROP FUNCTION IF EXISTS {_p_wrap_fn}")
spark.sql(f"DROP FUNCTION IF EXISTS {_scan_fn}")
spark.sql(f"DROP CONNECTION IF EXISTS {_conn}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 5 — Audit trail of tool calls
# MAGIC An ALLOW-all "tap": every call is permitted, but the attach point + the service's
# MAGIC `tracing`/`usage_tracking` config produce the audit ledger. The story is the ledger,
# MAGIC not a verdict. Below: three allowed calls, then the app request-log confirms all three.
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *AI Hub — Activity Visibility &
# MAGIC > Audit Logs* / *Prompt & Response Logging for Audit*
# MAGIC > "Centrally logs and audits all AI interactions… with timestamps for compliance and
# MAGIC > forensic investigation."
# MAGIC > **Sheet gap:** "Logs exist in different platforms — **needs centralized**; Copilot logs
# MAGIC > are auto-rolled-over and need to be preserved."
# MAGIC > **How this closes it:** the gateway's `tracing` + `usage_tracking` emit one uniform
# MAGIC > per-call ledger (system tables) across every governed MCP service — a single, retained
# MAGIC > source of truth rather than per-platform silos.

# COMMAND ----------

print_policy("policy_05_audit")

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
# MAGIC ### Scenario 5b — the durable audit ledger: `system.ai_gateway.usage`
# MAGIC The app `/requests` log above is an in-memory demo aid. The **authoritative, retained**
# MAGIC audit trail is the Databricks system table `system.ai_gateway.usage`: one row per
# MAGIC gateway call across *every* governed MCP service, with the tool name, JSON-RPC method,
# MAGIC status code, and requester — the single centralized source of truth the requirement
# MAGIC asks for (vs. per-platform silos).
# MAGIC
# MAGIC > **Read this table for the verdict story:** `status_code` **200 = ALLOW**, **403 = a
# MAGIC > policy BLOCK** (both DENY and ASK land as 403 on this Beta), 307 = proxy redirect
# MAGIC > noise. `service_name` is the **fully-qualified** mcp-service name. The table lags a
# MAGIC > few minutes, so the newest calls from this run may not have landed yet.

# COMMAND ----------

audit_sql = f"""
SELECT event_time,
       mcp_metadata.tool_name        AS tool,
       mcp_metadata.json_rpc_method  AS method,
       status_code,
       CASE status_code WHEN 200 THEN 'ALLOW'
                        WHEN 403 THEN 'BLOCK (deny/ask)'
                        ELSE CAST(status_code AS STRING) END AS verdict,
       requester
FROM system.ai_gateway.usage
WHERE service_name = '{GOV}'
  AND mcp_metadata.tool_name IS NOT NULL
ORDER BY event_time DESC
LIMIT 20
"""
try:
    df = spark.sql(audit_sql)
    n = df.count()
    if n == 0:
        print(f"no rows yet for {GOV} — the usage table lags a few minutes; "
              "re-run this cell shortly to see this session's calls.")
    else:
        print(f"most recent {n} governed calls from system.ai_gateway.usage:")
        df.show(truncate=False)
except Exception as e:  # noqa: BLE001
    print(f"system.ai_gateway.usage not queryable in this workspace ({e}); "
          "the app /requests ledger above still demonstrates per-call audit.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scenario 6 — Destructive operations deny
# MAGIC Block irreversible / privileged mutations at the action layer.
# MAGIC - **Positive:** `get_record` (read) → ALLOW
# MAGIC - **Negative:** `delete_record` (destructive) → DENY
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *Quarantine / Terminate Risky Agents*
# MAGIC > "Enables real-time containment and termination of AI agents exhibiting risky behavior
# MAGIC > patterns or policy violations."
# MAGIC > **Sheet gap:** "Containment exists but **no AI-specific triggers configured — manual
# MAGIC > intervention required**."
# MAGIC > **How this closes it:** destructive tools are auto-denied at request time by policy —
# MAGIC > the automated, deterministic trigger they lack today, applied before any mutation runs.

# COMMAND ----------

print_policy("policy_06_no_destructive")

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
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *Insider Risk Detection for AI
# MAGIC > Usage Anomalies*
# MAGIC > "Monitors for suspicious AI usage patterns (e.g., bulk data exfiltration via prompts,
# MAGIC > unauthorized model access)."
# MAGIC > **Sheet gap:** "Purview IRM has limited visibility — **depends on sensitivity labels and
# MAGIC > data classification**; without consistent adoption these controls are weak."
# MAGIC > **How this closes it:** export/external-send tools are denied deterministically at the
# MAGIC > gateway regardless of labeling accuracy — a hard egress boundary, not a
# MAGIC > classification-dependent heuristic.

# COMMAND ----------

print_policy("policy_07_no_egress")

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
# MAGIC
# MAGIC > **Customer requirement — originally scoped _Partial_** · *Quarantine / Terminate Risky
# MAGIC > Agents* (human-in-the-loop angle)
# MAGIC > "Real-time containment of agents exhibiting risky behavior / policy violations."
# MAGIC > **Sheet gap:** "**Manual intervention required** — no automated trigger."
# MAGIC > **How this closes it:** the ASK verdict is the governed handoff — sensitive-but-not-
# MAGIC > forbidden actions are automatically routed to a human approver instead of relying on
# MAGIC > ad-hoc manual review, giving a repeatable HITL gate. *(Weakest sheet mapping — the
# MAGIC > requirements list has no dedicated per-action approval row; confirm framing with the account team.)*

# COMMAND ----------

print_policy("policy_08_ask_human")

# COMMAND ----------

run_scenario("policy_08_ask_human", "p8",
             positive=("get_record", {"record_id": "P-1001"}),
             negative=("delete_record", {"record_id": "P-1001"}), neg_verdict="ASK")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Scenario 8 — what's proven today vs. TBD
# MAGIC **Proven here:** the policy returns `ASK` and the gateway stops the call and surfaces the
# MAGIC reason (`-32003`, "routed to a human approver"). The *classification and gating* — "this
# MAGIC action needs a human" — is enforced at the gateway today.
# MAGIC
# MAGIC **TBD (not yet fleshed out):** the operational human-approval loop — *notify an approver →
# MAGIC approver reviews → approve/deny → the original call resumes*. On this Beta there is **no
# MAGIC native approval console, notification, or MCP elicitation** wired to the ASK verdict; ASK
# MAGIC is wire-identical to DENY and only the reason differs. Standing up the approver workflow
# MAGIC (who is alerted, where they approve, how the call resumes) is a **caller-side / app-side
# MAGIC pattern** to design with the account team — native elicitation is on the MCP roadmap but
# MAGIC not available to lean on yet. Keep S8 scoped to "show the return code" until that lands.
# MAGIC
# MAGIC > **Related — attribute-based access (ABAC), in some capacity.** Beyond per-call policies,
# MAGIC > Databricks also offers **ABAC GRANT policies**: grant `EXECUTE` on models/functions by
# MAGIC > *tag* and catalog/schema scope (DBR 18.3+), so entitlement follows attributes rather than
# MAGIC > per-object grants. Note these are **model/UC-object-scoped**, not attachable to an MCP
# MAGIC > *service* policy — a complementary governance layer, not a substitute for the gateway
# MAGIC > policies shown here. Worth flagging to the customer as part of the broader story.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Negative-proof: a blocked call never reaches the app
# MAGIC A policy denial happens *at the gateway*, before the upstream MCP server is contacted.
# MAGIC Re-attach the destructive-ops policy, clear the log, attempt the denied call, and show
# MAGIC the app log stays empty — the tool code never ran.

# COMMAND ----------

attach("policy_06_no_destructive", "p6")
# Poll until enforced (deterministic), then clear the log so the count below is clean.
wait_until_enforced(("get_record", {"record_id": "P-1001"}),
                    ("delete_record", {"record_id": "P-1001"}))
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
