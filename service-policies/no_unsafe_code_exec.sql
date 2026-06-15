-- Service policy: block arbitrary code-execution tools (e.g. system.ai.python_exec).
-- CEL-transpilable MCP service-policy (see no_destructive_ops.sql for the contract).
CREATE OR REPLACE FUNCTION <catalog>.<schema>.no_unsafe_code_exec(event VARIANT, config VARIANT)
RETURNS VARIANT
LANGUAGE SQL
RETURN CASE
  WHEN event:type::string = 'request'
   AND event:context.tool.name::string IN (
       'python_exec', 'system.ai.python_exec', 'exec', 'run_code', 'shell')
  THEN to_variant_object(named_struct('result', 'DENY',
       'reason', 'Code-execution tools are blocked in a PHI environment without governance approval.'))
  ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
END;
