-- Scenario #5 — Audit trail of tool calls.
-- An ALLOW-all "tap": every tool call is permitted, but its evaluation is the
-- attach point that, combined with the mcp-service's `tracing` +
-- `usage_tracking` config, produces the audit ledger. Allowed calls land in
-- `system.ai_gateway` usage (central per-call status ledger) and in the demo
-- app's /requests log; the policy itself is version-controlled in git.
-- The audit story is told by the ledger, not by the verdict -- hence ALLOW-all.
--   every case: ALLOW (and recorded)
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_05_audit(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN to_variant_object(named_struct('result', 'ALLOW', 'reason', ''));
