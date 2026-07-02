-- Scenario #1 — Deterministic SQL rule (allowlist / deny-by-default).
-- MARKETING: "deterministic rules". A hardcoded allowlist of known-safe tools;
-- everything else is denied by default (no model, no judgment -- pure rule).
-- CEL-transpilable MCP service-policy (see no_destructive_ops.sql for the contract).
--   positive case: echo        -> ALLOW (on the allowlist)
--   negative case: admin_reset -> DENY  (not on the allowlist)
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_01_allowlist(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN (
       'echo', 'get_record', 'search_notes', 'get_untrusted_doc',
       'health', 'get_current_user')
  THEN to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
  WHEN event:type::string = 'request'
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Tool is not on the deterministic allowlist (deny-by-default).'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
