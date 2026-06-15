# Service Policies (WS3 — agent action governance)

MCP **service policies** are Unity Catalog SQL functions (transpiled to CEL), attached to
a registered **MCP service** in Unity AI Gateway, evaluated **before every tool call**
(ALLOW / DENY / ASK). Config-as-code today: `CREATE FUNCTION` SQL + API attach.

## Verified end-to-end on dogfood (2026-06-15)

```bash
# 1. Schema-level HTTP connection to the MCP server (U2M OAuth via UI, or API)
#    -> connections/<cat>.<schema>.<conn>

# 2. Create the MCP service (a UC securable) pointing at the connection
databricks api post -p dogfood \
  "/api/2.1/unity-catalog/mcp-services?parent=schemas/<cat>.<schema>&mcp_service_id=github_mcp" \
  --json '{"config":{"connection":{"name":"connections/<cat>.<schema>.<conn>"},"include_tool_selectors":[]}}'

# 3. Deploy the policy function (CEL form — see *.sql)

# 4. Attach it (rank-ordered; rank 1 wins ties)
databricks api patch -p dogfood \
  "/api/2.1/unity-catalog/mcp-services/<cat>.<schema>.github_mcp?update_mask=config.service_policies" \
  --json '{"config":{"service_policies":[
    {"name":"no_destructive_ops","policy_type":"POLICY_TYPE_CUSTOM",
     "handler":"functions/<cat>.<schema>.no_destructive_ops","rank":1}]}}'
```

## Policy function contract (CEL-transpilable)

- Signature: `(event VARIANT, config VARIANT) RETURNS VARIANT LANGUAGE SQL`
- Inputs: `event:type::string` = `request`(on-call)/`response`(on-result); `event:context.tool.name::string`; `event:context.tool.arguments`
- Return: `to_variant_object(named_struct('result','DENY|ALLOW|ASK','reason','...'))`
- Allowed: CASE/IF, comparisons, `IN`, `LIKE`, STARTSWITH/ENDSWITH/CONTAINS, COALESCE, CAST
- NOT allowed: subqueries, BETWEEN, aggregates, lambdas, variadic CONCAT → **keep `reason` static**

## Policies here → IH line items

| Function | Effect | Line item |
|---|---|---|
| `no_destructive_ops` | deny push/merge/delete GitHub tools | defense-in-depth |
| `no_unsafe_code_exec` | deny code-interpreter tools (`python_exec`) | defense-in-depth |
| `no_egress_tools` | deny web/internet/external-send tools | #13 network, #15 DLP |

Note: built-in content guardrails (safety/PII/jailbreak) attach to **model-services** as
`POLICY_TYPE_BUILTIN` handlers; these CUSTOM CEL policies attach to **mcp-services**.
