#!/usr/bin/env python3
"""Call a tool on an MCP service through the policy-gated gateway path; print the payload.

  bin/mcp_call.py <mcp-service> --list                         # tools/list
  bin/mcp_call.py <mcp-service> <tool> ['<json-args>']         # tools/call
  bin/mcp_call.py github_mcp merge_pull_request '{}'           # -> POLICY_DENIED (no_destructive_ops)
  bin/mcp_call.py github_mcp get_me                            # -> real GitHub data (after U2M login)

Path:  POST <gateway-host>/ai-gateway/mcp-services/<cat>.<schema>.<id>/{tools/call|tools/list}
Notes: the AI Gateway is on its own host (--host), distinct from the CLI profile host;
       requests need Accept: application/json, text/event-stream (responses are SSE).
       A policy-denied write returns POLICY_DENIED *before* the upstream MCP is contacted;
       allowed/read tools reach upstream and need its auth (e.g. GitHub U2M login).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

DEFAULT_HOST = os.environ.get("GATEWAY_HOST", "")  # AI Gateway host; set GATEWAY_HOST env or pass --host


def token(profile):
    out = subprocess.run(["databricks", "auth", "token", "-p", profile], capture_output=True, text=True).stdout
    return json.loads(out)["access_token"]


def parse_sse(text):
    """Gateway returns SSE ('event: message' / 'data: {json}'); fall back to plain JSON."""
    for line in reversed([l[5:].strip() for l in text.splitlines() if l.startswith("data:")]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text.strip()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("service", help="short name (-> main.default.<name>) or full cat.schema.name")
    ap.add_argument("tool", nargs="?", help="tool name (omit with --list)")
    ap.add_argument("args", nargs="?", default="{}", help="JSON tool arguments")
    ap.add_argument("--list", action="store_true", help="tools/list instead of tools/call")
    ap.add_argument("--profile", default="DEFAULT")
    ap.add_argument("--parent", default="main.default")
    ap.add_argument("--host", default=DEFAULT_HOST, help="AI Gateway host (not the CLI profile host)")
    a = ap.parse_args()
    if not a.host:
        ap.error("set the AI Gateway host via GATEWAY_HOST env or --host (e.g. https://<workspace>.cloud.databricks.com)")

    fqn = a.service if a.service.count(".") >= 2 else f"{a.parent}.{a.service}"
    method = "tools/list" if a.list else "tools/call"
    params = {} if a.list else {"name": a.tool, "arguments": json.loads(a.args)}
    rpc = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    out = subprocess.run(
        ["curl", "-sS", "-X", "POST", f"{a.host}/ai-gateway/mcp-services/{fqn}/{method}",
         "-H", f"Authorization: Bearer {token(a.profile)}",
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         "-d", json.dumps(rpc)],
        capture_output=True, text=True,
    )
    print(json.dumps(parse_sse(out.stdout or out.stderr), indent=2))


if __name__ == "__main__":
    main()
