-- Scenario #7 — Data-exfiltration / egress deny.
-- Deny bulk export and external-send tools at the action layer (defense against
-- data exfiltration). Complements the network-egress policy in no_egress_tools.sql.
--   positive case: search_notes {query:"A1c"}                          -> ALLOW
--   negative case: export_dataset {dataset:"patients", destination:...} -> DENY
--   negative case: send_external {to:"x@ext", body:"..."}              -> DENY
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_07_no_egress(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN ('export_dataset', 'send_external')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Data egress / bulk export requires AI-governance approval.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
