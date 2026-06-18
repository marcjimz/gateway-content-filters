#!/usr/bin/env python3
"""Render a git endpoint spec into the model-services API config body (the target workspace).

The output is the exact `{config:{...}}` body accepted by:
  POST /api/2.1/unity-catalog/model-services?parent=schemas/<cat>.<schema>&model_service_id=<name>

Usage:
  python tools/render.py ih-guardrail-demo          # config JSON for one endpoint
  python tools/render.py                            # all endpoints
"""
from __future__ import annotations

import json
import sys

from _policy import HANDLER, PHASE


def render(ep, guardrails, prompts):
    """Pure: endpoint spec -> {config:{...}} API body."""
    from collections import Counter

    # policy names must be unique within a model-service; if a guardrail is bound
    # more than once (e.g. enforce input + annotate output), disambiguate by phase.
    ref_counts = Counter(b["ref"] for b in ep["policies"])
    policies = []
    for i, b in enumerate(ep["policies"], start=1):
        gd = guardrails[b["ref"]]
        name = gd["name"] if ref_counts[b["ref"]] == 1 else f"{gd['name']}-{'-'.join(b['phases'])}"
        opts = {
            "dry_run": "true" if b["mode"] == "annotate" else "false",
            "model_service": f"model-services/{ep['judge']}",
            "phases": ",".join(PHASE[p] for p in b["phases"]),
        }
        if gd["type"] == "custom":
            opts["action"] = gd["action"]
            opts["instruction"] = prompts.get(gd["prompt_file"], "")
        policies.append({
            "name": name,
            "handler": HANDLER[gd["type"]],
            "policy_type": "POLICY_TYPE_BUILTIN",
            "rank": 1,
            "options": opts,
        })
    config = {
        "destinations": [
            {
                "name": d.get("name", "primary"),
                "pay_per_token_config": {"model": f"models/{d['model']}"},
                "traffic_percentage": d.get("traffic_percentage", 100),
                "type": "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL",
            }
            for d in ep["destinations"]
        ],
        "service_policies": policies,
        "tracing": {"enabled": bool(ep.get("tracing", True))},
        "usage_tracking": {"enabled": bool(ep.get("usage_tracking", True))},
    }
    # Per-endpoint inference (payload) table — OFF by default on the platform, so we
    # turn it on as code (full request/response + verdicts -> main.default.<name>_payload).
    if ep.get("inference_table", True):
        config["inference_table"] = {
            "enabled": True,
            "parent": f"schemas/{ep['parent']}",
            "table_name_prefix": ep["name"],
        }
    return {"config": config}


def main(argv=None):
    from _policy import load

    policy = load()
    names = [argv[0]] if (argv := (argv or sys.argv[1:])) else list(policy["endpoints"])
    for n in names:
        if n not in policy["endpoints"]:
            print(f"unknown endpoint: {n}", file=sys.stderr)
            return 2
        print(json.dumps(render(policy["endpoints"][n], policy["guardrails"], policy["prompts"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
