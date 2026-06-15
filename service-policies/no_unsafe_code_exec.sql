-- Service policy: block arbitrary code execution tools.
-- The managed UC Functions MCP ships `system.ai.python_exec` (a full code
-- interpreter) by default — a major risk in a PHI environment. Deny it (and
-- siblings) unless a project is explicitly approved (upgrade to approval-gated
-- once table lookups in service-policy UDFs are confirmed — see README).
CREATE OR REPLACE FUNCTION <catalog>.<schema>.no_unsafe_code_exec(actor VARIANT, context VARIANT)
RETURNS STRUCT<result STRING, reason STRING>
RETURN CASE
  WHEN context:tool.name::STRING IN ('python_exec', 'system.ai.python_exec', 'exec', 'run_code', 'shell')
    THEN NAMED_STRUCT(
        'result', 'deny',
        'reason', 'Code-execution tools are blocked: not permitted in a PHI environment without governance approval.')
  ELSE NAMED_STRUCT('result', 'allow', 'reason', '')
END;
