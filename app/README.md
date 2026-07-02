# Policy Demo MCP Server

A minimal [FastMCP](https://github.com/jlowin/fastmcp) server, deployed as a
Databricks App, that exposes a handful of deliberately risky demo tools so we
can prove **AI Gateway v2 MCP Service Policies** allow / deny / ask before a
tool ever runs.

## Tools

| Tool | Scenario it exercises |
|------|-----------------------|
| `echo` | #1 deterministic allowlist (positive case) |
| `admin_reset` | #1 deterministic allowlist (deny-by-default, unlisted) |
| `get_record`, `search_notes` | #2 PII/PHI DLP on tool I/O |
| `get_untrusted_doc` | #4 indirect prompt injection in tool results |
| `delete_record` | #6 destructive-ops deny, #8 escalate-to-human |
| `export_dataset` | #7 exfiltration, #8 escalate-to-human |
| `send_external` | #7 network egress deny |
| `health`, `get_current_user` | connectivity + OBO identity check |

All payloads are synthetic. No real customer data.

## Request log (negative-proof)

Every tool call that executes appends to an in-memory log:

- `GET /requests` &mdash; the calls that reached the server
- `DELETE /requests` &mdash; clear it between scenarios

A call blocked by a service policy is rejected at the gateway and never reaches
this process, so it never appears in the log. That absence is the proof that
enforcement happens upstream of the tool.

## Auth

OAuth OBO: the app forwards the caller's token in `x-forwarded-access-token`;
`get_current_user` builds a user-scoped `WorkspaceClient` from it.

## Run locally

```bash
cd app
uv run policy-demo-mcp-server --port 8000
```

## Deploy

```bash
databricks sync app ./app <workspace-source-path> -p <profile>
databricks apps deploy <app-name> --source-code-path <workspace-source-path> -p <profile>
```
