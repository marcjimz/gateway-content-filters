# Service Policies (WS3 — agent action governance)

MCP **service policies** are Unity Catalog SQL functions, attached to a registered
MCP in Unity AI Gateway, evaluated **before every tool call** (allow / deny / consent).
Unlike the v2 guardrails, these are **config-as-code today** — plain `CREATE FUNCTION`
SQL, deployable via SQL/CLI/DABs and CI-testable.

## Verified contract

```sql
CREATE OR REPLACE FUNCTION <catalog>.<schema>.<policy>(actor VARIANT, context VARIANT)
RETURNS STRUCT<result STRING, reason STRING>
RETURN CASE WHEN <condition> THEN NAMED_STRUCT('result','deny','reason','...')
            ELSE NAMED_STRUCT('result','allow','reason','') END;
```

- `context:tool.name::STRING` — the tool being called
- `context:tool.arguments` — its arguments (VARIANT)
- `actor:…` — caller identity
- `result` ∈ `allow` | `deny` | `consent`

## Policies here → IH line items

| Function | Effect | Line item |
|---|---|---|
| `no_destructive_ops` | deny delete/drop/truncate/merge | defense-in-depth |
| `no_unsafe_code_exec` | deny code-interpreter tools (e.g. `system.ai.python_exec`) | defense-in-depth |
| `no_egress_tools` | deny web/internet/external-send tools | #13 network, #15 DLP |

## Open items to confirm in BUILDER before relying on these

1. **Arg convention** — public blogs use `(actor, context)` with `context:tool.name`;
   some internal tickets show `(event, config)` with `event:context.tool.name`.
   Confirm which the current BUILDER backend accepts.
2. **`actor` schema** — exact field for caller identity (user / principal / groups).
3. **Attach mechanism** — how a policy is bound to an MCP (UI / SQL / API).
4. **Table lookups** — whether a service-policy UDF may query an approvals table
   (for principal-gated allow). If yes, upgrade the blanket denies to approval-gated
   (reusing the approver+expiry pattern from `gateway-policy/exceptions/`).

Verify-then-author, same as WS1.
