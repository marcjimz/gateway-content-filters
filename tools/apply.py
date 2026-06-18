#!/usr/bin/env python3
"""Apply gateway policy to live model-services via the Unity Catalog API (the target workspace).

This is the real config-as-code applier — git spec -> API, no UI. CRUD confirmed:
  CREATE  POST   /api/2.1/unity-catalog/model-services?parent=schemas/<cat>.<schema>&model_service_id=<id>
  READ    GET    /api/2.1/unity-catalog/model-services/<cat>.<schema>.<id>
  DELETE  DELETE /api/2.1/unity-catalog/model-services/<cat>.<schema>.<id>

Auth: shells out to `databricks api ... -p <profile>` (CLI auth works where curl-bearer doesn't).

Usage:
  python tools/apply.py get    ih-guardrail-demo
  python tools/apply.py diff   ih-guardrail-demo
  python tools/apply.py create ih-guardrail-demo --name ih-guardrail-iac   # deploy under a new id
  python tools/apply.py delete ih-guardrail-iac
  python tools/apply.py apply  ih-guardrail-demo --name ih-guardrail-iac   # create-or-replace
"""
from __future__ import annotations

import json
import subprocess
import sys

from render import render


def _api(method, path, body=None, profile="DEFAULT"):
    cmd = ["databricks", "api", method, path, "-p", profile]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    txt = (out.stdout or out.stderr).strip()
    try:
        return json.loads(txt)
    except Exception:
        return {"_raw": txt}


def full_name(ep, name=None):
    return f"{ep['parent']}.{name or ep['name']}"


def get(fn, profile):
    return _api("get", f"/api/2.1/unity-catalog/model-services/{fn}", profile=profile)


def create(ep, body, name, profile):
    cat_schema = ep["parent"]
    mid = name or ep["name"]
    path = f"/api/2.1/unity-catalog/model-services?parent=schemas/{cat_schema}&model_service_id={mid}"
    return _api("post", path, body=body, profile=profile)


def delete(fn, profile):
    return _api("delete", f"/api/2.1/unity-catalog/model-services/{fn}", profile=profile)


def _norm(cfg):
    """Normalize a config for comparison (sort policies by name, drop server defaults we don't set)."""
    cfg = json.loads(json.dumps(cfg))
    sp = cfg.get("service_policies", [])
    cfg["service_policies"] = sorted(sp, key=lambda p: (p["name"], p.get("options", {}).get("phases", "")))
    return cfg


def main(argv=None):
    import argparse

    from _policy import load

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["get", "create", "delete", "apply", "diff"])
    ap.add_argument("endpoint")
    ap.add_argument("--name", help="override model_service_id (deploy under a different id)")
    ap.add_argument("--profile", default="DEFAULT")
    ap.add_argument("--parent", help="override the spec's catalog.schema (keeps your catalog out of the repo)")
    args = ap.parse_args(argv)

    policy = load()
    if args.endpoint not in policy["endpoints"]:
        print(f"unknown endpoint: {args.endpoint}", file=sys.stderr)
        return 2
    ep = policy["endpoints"][args.endpoint]
    if args.parent:
        ep["parent"] = args.parent
    fn = full_name(ep, args.name)
    body = render(ep, policy["guardrails"], policy["prompts"])

    if args.action == "get":
        print(json.dumps(get(fn, args.profile), indent=2))
    elif args.action == "delete":
        print(json.dumps(delete(fn, args.profile), indent=2))
    elif args.action == "create":
        print(json.dumps(create(ep, body, args.name, args.profile), indent=2))
    elif args.action == "apply":  # create-or-replace (PATCH update TBD)
        live = get(fn, args.profile)
        if "config" in live:
            print(f"exists -> replacing {fn}")
            delete(fn, args.profile)
        print(json.dumps(create(ep, body, args.name, args.profile), indent=2))
    elif args.action == "diff":
        live = get(fn, args.profile)
        if "config" not in live:
            print(f"{fn}: not deployed (would CREATE)")
            return 0
        a, b = _norm(body["config"]), _norm(live["config"])
        if a == b:
            print(f"{fn}: in sync ✓")
        else:
            print(f"{fn}: DIFFERS")
            print("  rendered policies:", [p["name"] + ":" + p["options"]["phases"] for p in a["service_policies"]])
            print("  live policies:    ", [p["name"] + ":" + p["options"].get("phases", "?") for p in b["service_policies"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
