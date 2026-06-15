-- Service policy: block destructive tool calls on any MCP it's attached to.
-- MCP service-policy contract (must transpile to CEL):
--   (event VARIANT, config VARIANT) RETURNS VARIANT
--   read tool via event:context.tool.name::string; event:type = 'request'|'response'
--   return to_variant_object(named_struct('result','DENY|ALLOW|ASK','reason','...'))
--   allowed: CASE/IF, comparisons, IN, LIKE, STARTSWITH/ENDSWITH/CONTAINS, COALESCE, CAST
--   NOT allowed: subqueries, BETWEEN, aggregates, lambdas, variadic CONCAT (keep reason static)
-- Attach:  PATCH /api/2.1/unity-catalog/mcp-services/<fqn>?update_mask=config.service_policies
--          {"config":{"service_policies":[{"name":...,"policy_type":"POLICY_TYPE_CUSTOM",
--            "handler":"functions/<cat>.<schema>.no_destructive_ops","rank":1}]}}
CREATE OR REPLACE FUNCTION <catalog>.<schema>.no_destructive_ops(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN (
       'push_files', 'merge_pull_request', 'create_or_update_file',
       'delete_file', 'delete_repository', 'force_push')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Destructive GitHub operations are not permitted for agents.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
