-- Scenario #2 — PII/PHI DLP on tool input.
-- Deterministic DLP at the action layer: detect an SSN-shaped token in tool
-- arguments and DENY before the call reaches the MCP server. The LIKE pattern
-- '___-__-____' (underscore = exactly one char) matches the 3-2-4 SSN shape
-- without needing regex (which CEL does not support).
--   positive case: search_notes {query:"elevated A1c"}          -> ALLOW
--   negative case: search_notes {query:"lookup 123-45-6789"}     -> DENY
-- NOTE: response-side PHI *redaction* (transform) is not expressible as an
-- ALLOW/DENY action policy -- it lives in the model-service `pii`/`phi`
-- guardrail (design.md §4, native mask_pii). This policy is the input-side
-- deterministic complement.
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_02_phi_dlp(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.arguments.query::string LIKE '%___-__-____%'
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'PHI detected in tool input (SSN-shaped token); redact before sending.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
