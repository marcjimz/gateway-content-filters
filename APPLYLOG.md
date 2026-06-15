# Apply Log

**Superseded on dogfood (2026-06-15):** the gateway endpoint is a UC securable with full
CRUD via the public model-services API, so policy is now applied **from the repo via
`tools/apply.py`** (git → API), not by hand in the UI. Git history + the model-service
`update_time`/`updated_by` are the audit trail.

This log remains only for workspaces **without** the public model-services API (where the
manual UI apply is still required — see git history for that flow). Record manual applies
there, traced to the source commit:

| Date | Endpoint | Workspace | Commit | Applied by | Notes |
|------|----------|-----------|--------|------------|-------|
| _example_ | clinical-chat | (no-API workspace) | `abc1234` | brennan.beal | manual UI apply |
