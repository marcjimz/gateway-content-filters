#!/usr/bin/env python3
"""Call a tool on an MCP service through the policy-gated gateway path; print the payload.

  bin/mcp_call.py <mcp-service> --list                         # tools/list
  bin/mcp_call.py <mcp-service> <tool> ['<json-args>']         # tools/call
  bin/mcp_call.py github_mcp merge_pull_request                # -> POLICY_DENIED (no_destructive_ops)
  bin/mcp_call.py github_mcp list_issues '{"owner":"o","repo":"r"}'

Path: POST /ai-gateway/mcp-services/<cat>.<schema>.<id>/{tools/call|tools/list}
A write tool denied by a service policy returns POLICY_DENIED *before* the upstream
MCP server is contacted — so a denied call never reaches e.g. GitHub. Read/allowed
tools DO reach the upstream and need its auth (e.g. GitHub U2M login) to succeed.
"""
from __future__ import annotations

import argparse
import json
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("service", help="short name (-> bbeal.default.<name>) or full cat.schema.name")
    ap.add_argument("tool", nargs="?", help="tool name (omit with --list)")
    ap.add_argument("args", nargs="?", default="{}", help="JSON tool arguments")
    ap.add_argument("--list", action="store_true", help="tools/list instead of tools/call")
    ap.add_argument("--profile", default="dogfood")
    ap.add_argument("--parent", default="bbeal.default")
    a = ap.parse_args()

    fqn = a.service if a.service.count(".") >= 2 else f"{a.parent}.{a.service}"
    method = "tools/list" if a.list else "tools/call"
    params = {} if a.list else {"name": a.tool, "arguments": json.loads(a.args)}
    rpc = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    p = subprocess.run(
        ["databricks", "api", "post", f"/ai-gateway/mcp-services/{fqn}/{method}", "-p", a.profile, "--json", json.dumps(rpc)],
        capture_output=True, text=True,
    )
    raw = (p.stdout.strip() or p.stderr.strip())
    s = raw[len("Error:"):].strip() if raw.startswith("Error:") else raw
    try:
        print(json.dumps(json.loads(s), indent=2))
    except json.JSONDecodeError:
        print(s)


if __name__ == "__main__":
    main()
