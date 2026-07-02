-- Scenario #8 — Escalate-to-human (ASK).
-- MARKETING: "ask a human". Sensitive-but-not-forbidden actions return ASK,
-- routing the call to a human approver instead of an outright DENY. Demonstrated
-- in isolation (attach this policy alone) so it does not collide with the
-- hard-deny policies (#6 destructive, #7 egress) that cover the same tools.
--   positive case: get_record {record_id:"P-1001"}    -> ALLOW
--   ask case:      delete_record {record_id:"P-1001"}  -> ASK (human approval)
--   ask case:      export_dataset {...}                -> ASK (human approval)
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_08_ask_human(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN ('delete_record', 'export_dataset')
  THEN to_variant_object(named_struct('result', 'ASK',
       'reason', 'Sensitive action routed to a human approver.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
