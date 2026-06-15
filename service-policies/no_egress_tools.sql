-- Service policy: block network egress / external-send tools.
-- Covers IH line items #13 (internet access / prompt-injection at perimeter) and
-- #15 (DLP) at the *action* layer: even a clean prompt can't make an agent reach
-- the internet or exfiltrate data if the tool call is denied here.
CREATE OR REPLACE FUNCTION <catalog>.<schema>.no_egress_tools(actor VARIANT, context VARIANT)
RETURNS STRUCT<result STRING, reason STRING>
RETURN CASE
  WHEN context:tool.name::STRING IN (
        'web_search', 'fetch_url', 'http_request', 'browse',           -- inbound internet
        'send_email', 'post_message', 'upload_file', 'external_query'  -- outbound exfiltration
      )
    THEN NAMED_STRUCT(
        'result', 'deny',
        'reason', context:tool.name::STRING || ' is blocked: network egress requires AI-governance approval.')
  ELSE NAMED_STRUCT('result', 'allow', 'reason', '')
END;
