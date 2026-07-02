"""Entry point for the policy-demo MCP server (uvicorn ASGI)."""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Start the policy-demo MCP server")
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to run the server on (default: 8000)"
    )
    args = parser.parse_args()

    # proxy_headers + forwarded_allow_ips: trust the Databricks Apps proxy so
    # any URL/redirect the app builds uses the external host, not localhost.
    uvicorn.run(
        "server.app:combined_app",
        host="0.0.0.0",
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
