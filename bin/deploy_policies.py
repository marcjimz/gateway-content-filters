#!/usr/bin/env python3
"""(Re)create the 8 MCP service-policy functions in any workspace, from the canonical
``service-policies/policy_0*.sql`` files. Idempotent (CREATE OR REPLACE).

Each file uses ``<catalog>.<schema>`` placeholders; this script substitutes the target
catalog/schema, runs each DDL via the SQL Statements API, and (optionally) validates that
every function transpiles to CEL by attaching it alone to a governed mcp-service (the
attach call FAILS if the SQL is not CEL-transpilable).

Usage:
  bin/deploy_policies.py --profile <cli-profile> --catalog <cat> --schema <schema> \
      --warehouse <warehouse_id> [--governed <mcp_service_id> --validate]

Env fallbacks: DATABRICKS_CONFIG_PROFILE, POLICY_CATALOG, POLICY_SCHEMA, POLICY_WAREHOUSE.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_GLOB = os.path.join(REPO, "service-policies", "policy_0*.sql")


def api(method, path, body, profile):
    p = subprocess.run(
        ["databricks", "api", method, path, "-p", profile, "--json", json.dumps(body)],
        capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def run_sql(stmt, warehouse, profile):
    rc, out, err = api("post", "/api/2.0/sql/statements/",
                       {"statement": stmt, "warehouse_id": warehouse, "wait_timeout": "50s"},
                       profile)
    try:
        st = json.loads(out).get("status", {})
        return st.get("state"), json.dumps(st.get("error", {}))
    except Exception:
        return "PARSE_ERR", (out or err)[:400]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT"))
    ap.add_argument("--catalog", default=os.environ.get("POLICY_CATALOG"))
    ap.add_argument("--schema", default=os.environ.get("POLICY_SCHEMA", "default"))
    ap.add_argument("--warehouse", default=os.environ.get("POLICY_WAREHOUSE"))
    ap.add_argument("--governed", help="governed mcp-service id (short name) for --validate")
    ap.add_argument("--validate", action="store_true",
                    help="attach each policy alone to confirm CEL transpilation")
    a = ap.parse_args()
    if not a.catalog or not a.warehouse:
        ap.error("--catalog and --warehouse are required (or set POLICY_CATALOG / POLICY_WAREHOUSE)")

    gov_fqn = f"{a.catalog}.{a.schema}.{a.governed}" if a.governed else None
    files = sorted(glob.glob(POLICY_GLOB))
    if not files:
        sys.exit(f"no policy files matched {POLICY_GLOB}")

    print(f"{'function':28} {'create':8} {'transpile' if a.validate else '':10} note")
    print("-" * 80)
    ok = True
    for f in files:
        name = os.path.basename(f)[:-4]
        ddl = open(f).read().replace("<catalog>", a.catalog).replace("<schema>", a.schema)
        state, err = run_sql(ddl, a.warehouse, a.profile)
        create_ok = state == "SUCCEEDED"
        ok &= create_ok

        transpile = ""
        note = "" if create_ok else err
        if a.validate and create_ok and gov_fqn:
            body = {"config": {"service_policies": [{
                "name": name.replace("policy_0", "p"),
                "policy_type": "POLICY_TYPE_CUSTOM",
                "handler": f"functions/{a.catalog}.{a.schema}.{name}", "rank": 1}]}}
            rc, _, aerr = api(
                "patch",
                f"/api/2.1/unity-catalog/mcp-services/{gov_fqn}?update_mask=config.service_policies",
                body, a.profile)
            transpile = "OK" if rc == 0 else "FAIL"
            if rc != 0:
                ok = False
                note = aerr.strip().splitlines()[-1] if aerr.strip() else "attach failed"
        print(f"{name:28} {'OK' if create_ok else 'FAIL':8} {transpile:10} {note}")

    # leave governed detached if we validated
    if a.validate and gov_fqn:
        api("patch",
            f"/api/2.1/unity-catalog/mcp-services/{gov_fqn}?update_mask=config.service_policies",
            {"config": {"service_policies": []}}, a.profile)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
