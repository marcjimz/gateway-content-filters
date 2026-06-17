#!/usr/bin/env python3
"""Query an AI Gateway endpoint with a prompt; print the response payload as pretty JSON.

  bin/query.py <endpoint> "<prompt>"             # short name -> bbeal.default.<endpoint>
  bin/query.py <cat>.<schema>.<name> "<prompt>"  # or a full 3-part name
  bin/query.py ih-guardrail-demo "..." --profile dogfood --max-tokens 300

Allowed requests print the chat-completion JSON; blocked requests print the
guardrail decision (which names the policy that fired).
"""
from __future__ import annotations

import argparse
import json
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("endpoint", help="short name (-> bbeal.default.<name>) or full cat.schema.name")
    ap.add_argument("prompt")
    ap.add_argument("--profile", default="dogfood")
    ap.add_argument("--parent", default="bbeal.default", help="catalog.schema for short names")
    ap.add_argument("--max-tokens", type=int, default=2048)  # gpt-5.x reasoning tokens count against this; keep headroom
    a = ap.parse_args()

    model = a.endpoint if a.endpoint.count(".") >= 2 else f"{a.parent}.{a.endpoint}"
    body = {"model": model, "messages": [{"role": "user", "content": a.prompt}], "max_tokens": a.max_tokens}
    p = subprocess.run(
        ["databricks", "api", "post", "/ai-gateway/mlflow/v1/chat/completions", "-p", a.profile, "--json", json.dumps(body)],
        capture_output=True, text=True,
    )
    raw = (p.stdout.strip() or p.stderr.strip())

    # databricks api prints raw JSON on 2xx and "Error: <message>" on 4xx/5xx
    s = raw[len("Error:"):].strip() if raw.startswith("Error:") else raw
    try:
        print(json.dumps(json.loads(s), indent=2))
    except json.JSONDecodeError:
        print(json.dumps({"blocked": "blocked by" in raw.lower(), "message": s}, indent=2))


if __name__ == "__main__":
    main()
