-- Scenario #6 — Destructive operations deny.
-- Deny irreversible / privileged mutations at the action layer.
--   positive case: get_record {record_id:"P-1001"}  -> ALLOW (read)
--   negative case: delete_record {record_id:"P-1001"} -> DENY (destructive)
CREATE OR REPLACE FUNCTION <catalog>.<schema>.policy_06_no_destructive(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN ('delete_record', 'admin_reset')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Destructive operations are denied in a PHI environment.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
