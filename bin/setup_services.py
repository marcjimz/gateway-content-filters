#!/usr/bin/env python3
"""Recreate the demo's gateway plumbing in any workspace: the OAuth (M2M) UC HTTP
connection to the MCP app + the two mcp-services (open + governed).

This is the "stand it up from scratch in a new workspace" artifact. It reproduces
exactly what Phase 2 built, derived from the live objects' shapes:

  1. Look up the target Databricks App's service principal (client id + numeric id).
  2. Mint an OAuth client secret on that SP (unless --client-secret is supplied).
  3. Create a UC HTTP connection (credential_type OAUTH_M2M, is_mcp_connection=true)
     pointing at the app's /mcp base path.
  4. Grant the app's own SP CAN_USE on the app (the gateway calls the app AS this SP).
  5. Create two mcp-services under <catalog>.<schema>: one ungoverned (baseline) and
     one governed (policies get attached here by the notebook / deploy_policies.py).

After this, run bin/deploy_policies.py to create the policy functions, then run
notebooks/mcp_service_policies_demo.py.

Usage:
  bin/setup_services.py --profile <cli-profile> --app-name <app> \
      --catalog <cat> --schema <schema> --workspace-host https://<ws>.cloud.databricks.com \
      [--connection-name policy_demo_mcp_conn --open-id demo_mcp_open \
       --governed-id demo_mcp_governed --client-secret <existing-secret>]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def cli(args, profile, check=True):
    cmd = ["databricks"] + args + ["-p", profile]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        # surface only the last meaningful stderr line (conda noise filtered)
        errs = [l for l in p.stderr.splitlines() if "conda" not in l and l.strip()]
        print(f"  ! command failed: databricks {' '.join(args)}\n    {errs[-1] if errs else p.stderr[:200]}")
    return p.returncode, p.stdout, p.stderr


def api(method, path, body, profile, check=True):
    return cli(["api", method, path, "--json", json.dumps(body)], profile, check)


def jload(out, default=None):
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return default if default is not None else {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT"))
    ap.add_argument("--app-name", required=True, help="Databricks App name hosting the MCP server")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", default="default")
    ap.add_argument("--workspace-host", required=True,
                    help="e.g. https://<workspace>.cloud.databricks.com (for the OIDC token endpoint)")
    ap.add_argument("--connection-name", default="policy_demo_mcp_conn")
    ap.add_argument("--open-id", default="demo_mcp_open")
    ap.add_argument("--governed-id", default="demo_mcp_governed")
    ap.add_argument("--client-secret", help="reuse an existing OAuth secret instead of minting one")
    a = ap.parse_args()
    host = a.workspace_host.rstrip("/")

    # 1. Look up the app's service principal.
    print(f"[1/5] looking up app '{a.app_name}' ...")
    rc, out, _ = cli(["apps", "get", a.app_name, "-o", "json"], a.profile)
    if rc != 0:
        sys.exit("could not read the app; check --app-name and --profile")
    app = jload(out)
    client_id = app.get("service_principal_client_id")
    sp_id = app.get("service_principal_id")
    app_url = app.get("url", "").rstrip("/")
    print(f"      SP client_id={client_id}  sp_id={sp_id}\n      app_url={app_url}")
    if not (client_id and sp_id and app_url):
        sys.exit("app is missing service_principal_client_id / service_principal_id / url")

    # 2. Mint an OAuth client secret on the app SP (unless supplied).
    secret = a.client_secret
    if not secret:
        print(f"[2/5] minting OAuth secret on SP {sp_id} ...")
        rc, out, _ = cli(["service-principal-secrets-proxy", "create", str(sp_id), "-o", "json"], a.profile)
        secret = jload(out).get("secret")
        if not secret:
            sys.exit("failed to mint SP secret; pass --client-secret to reuse an existing one")
    else:
        print("[2/5] using supplied --client-secret")

    # 3. Create the UC HTTP connection (OAUTH_M2M, MCP).
    print(f"[3/5] creating connection '{a.connection_name}' ...")
    conn_body = {
        "name": a.connection_name,
        "connection_type": "HTTP",
        "options": {
            "host": app_url,
            "base_path": "/mcp",
            "port": "443",
            "client_id": client_id,
            "client_secret": secret,
            "oauth_scope": "all-apis",
            "token_endpoint": f"{host}/oidc/v1/token",
            "is_mcp_connection": "true",
        },
    }
    rc, out, err = api("post", "/api/2.1/unity-catalog/connections", conn_body, a.profile, check=False)
    if rc != 0 and "already exists" in (out + err).lower():
        print("      connection already exists — leaving it as-is")
    elif rc != 0:
        print(f"      ! connection create failed: {(err or out)[:200]}")

    # 4. Grant the app SP CAN_USE on the app (gateway calls the app AS this SP).
    print("[4/5] granting app SP CAN_USE on the app ...")
    api("patch", f"/api/2.0/permissions/apps/{a.app_name}",
        {"access_control_list": [{"service_principal_name": client_id, "permission_level": "CAN_USE"}]},
        a.profile, check=False)

    # 5. Create the two mcp-services.
    print("[5/5] creating mcp-services (open + governed) ...")
    parent = f"schemas/{a.catalog}.{a.schema}"
    svc_body = {"config": {
        "connection": {"name": f"connections/{a.connection_name}"},
        "tracing": {"enabled": True},
        "usage_tracking": {"enabled": True},
        "include_tool_selectors": [],
    }}
    for svc_id in (a.open_id, a.governed_id):
        rc, out, err = api(
            "post",
            f"/api/2.1/unity-catalog/mcp-services?parent={parent}&mcp_service_id={svc_id}",
            svc_body, a.profile, check=False)
        status = "created" if rc == 0 else ("exists" if "already exists" in (out + err).lower() else "FAILED")
        print(f"      {a.catalog}.{a.schema}.{svc_id}: {status}")

    print("\nDone. Next:")
    print(f"  bin/deploy_policies.py --profile {a.profile} --catalog {a.catalog} "
          f"--schema {a.schema} --warehouse <wh> --governed {a.governed_id} --validate")


if __name__ == "__main__":
    main()
