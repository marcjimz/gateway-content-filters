-- Service policy: block destructive tool calls on any MCP it's attached to.
-- Defense-in-depth: an agent should never delete/drop/truncate/merge, regardless
-- of what a (possibly injected) prompt asks. Replace <catalog>.<schema>.
CREATE OR REPLACE FUNCTION <catalog>.<schema>.no_destructive_ops(actor VARIANT, context VARIANT)
RETURNS STRUCT<result STRING, reason STRING>
RETURN CASE
  WHEN context:tool.name::STRING IN (
        'delete_file', 'delete_repo', 'drop_table', 'truncate_table',
        'delete_database', 'merge_pull_request', 'force_push'
      )
    THEN NAMED_STRUCT(
        'result', 'deny',
        'reason', context:tool.name::STRING || ' is blocked: destructive operations are not permitted for agents.')
  ELSE NAMED_STRUCT('result', 'allow', 'reason', '')
END;
