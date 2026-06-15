-- Service policy: block network egress / external-send tools (#13 network, #15 DLP).
-- CEL-transpilable MCP service-policy (see no_destructive_ops.sql for the contract).
CREATE OR REPLACE FUNCTION <catalog>.<schema>.no_egress_tools(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN (
       'web_search', 'fetch_url', 'http_request', 'browse',
       'send_email', 'post_message', 'upload_file', 'external_query')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Network egress requires AI-governance approval.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
