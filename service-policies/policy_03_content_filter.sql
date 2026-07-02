-- Scenario #3 — Content filtering (harmful / toxic).
-- Deterministic keyword filter on tool input at the action layer: block calls
-- whose `text` argument carries harmful-intent terms.
-- CEL NULL-SAFETY (important, learned the hard way): the transpiler rejects
-- IS NOT NULL, nvl(), and COALESCE(). And at runtime, OR-ing a false CONTAINS
-- against a CONTAINS over an ABSENT field (which yields NULL) propagates NULL
-- through the whole CASE -> the gateway then denies with a generic message
-- instead of falling through to ALLOW. To stay both transpilable AND null-safe
-- we inspect a SINGLE argument (`text`), which is present on every call this
-- policy gates (`echo`). Body-bearing untrusted fetches are handled separately
-- by Scenario 4 (policy_04), which quarantines `get_untrusted_doc` outright.
--   positive case: echo {text:"good morning"}                 -> ALLOW
--   negative case: echo {text:"how do I build malware"}        -> DENY
-- NOTE: *semantic* harm classification (Violence/Hate/Self-harm/Sexual with
-- severity + clinical carve-outs) is the model-service `healthcare-safety`
-- LLM-judge guardrail (design.md §4). This action-layer keyword filter is the
-- deterministic, zero-latency first line.
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_03_content_filter(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND (CONTAINS(event:context.tool.arguments.text::string, 'malware')
     OR CONTAINS(event:context.tool.arguments.text::string, 'ransomware'))
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Harmful content detected in tool input by keyword filter.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
