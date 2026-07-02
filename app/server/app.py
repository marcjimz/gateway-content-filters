"""FastAPI + FastMCP application for the policy-demo MCP server.

Combines the MCP protocol endpoint with two custom routes used by the demo:

  GET    /requests   -> the in-memory REQUEST_LOG (tool calls that executed)
  DELETE /requests   -> clear the log (call between scenarios for a clean slate)

The /requests endpoint is the negative-proof surface: a tool call blocked by a
service policy is rejected at the gateway and never reaches this process, so it
never lands in the log.

Path handling note: the AI Gateway invokes the upstream MCP endpoint at
``/mcp/`` (with a trailing slash). FastMCP's default single-path route issues a
slash-redirect to the *internal* host (https://localhost:8000/mcp) when hit at
``/mcp/``, which the gateway cannot follow -> upstream 404. To avoid that, the
MCP ASGI app is built at root path ("/") and MOUNTED under "/mcp", so the
streamable-HTTP handler answers both /mcp and /mcp/ directly with no redirect.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastmcp import FastMCP

from .tools import REQUEST_LOG, load_tools
from .utils import header_store

mcp_server = FastMCP(name="policy-demo-mcp-server")

STATIC_DIR = Path(__file__).parent / "../static"

load_tools(mcp_server)

# stateless_http=True: each request is self-contained (no mcp-session-id
# handshake), which the AI Gateway invocation path and horizontally scaled
# Databricks Apps both require. path="/" so that mounting under "/mcp" (below)
# serves the endpoint at /mcp and /mcp/ without a localhost slash-redirect.
# json_response=True: return the JSON-RPC response as a single application/json
# body instead of a text/event-stream SSE frame. The AI Playground's native UC
# MCP client (connection base_path /mcp) fails to extract the response event
# from the stateless SSE stream on forwarded ALLOW calls ("SSE stream closed
# without a JSON-RPC response event"); a plain JSON body sidesteps that framing.
# The mcp SDK client and the gateway proxy both accept the JSON body unchanged.
mcp_app = mcp_server.http_app(path="/", stateless_http=True, json_response=True)

combined_app = FastAPI(
    title="Combined Policy Demo MCP App",
    description="Demo tools for AI Gateway v2 MCP Service Policies",
    version="0.1.0",
    lifespan=mcp_app.lifespan,
)


@combined_app.middleware("http")
async def capture_headers(request: Request, call_next):
    """Capture inbound headers (incl. OBO token) for user-scoped tool auth."""
    header_store.set(dict(request.headers))
    return await call_next(request)


@combined_app.get("/requests")
async def get_requests():
    """Return the in-memory log of tool calls that actually executed."""
    return {"count": len(REQUEST_LOG), "requests": REQUEST_LOG}


@combined_app.delete("/requests")
async def clear_requests():
    """Clear the request log (reset between demo scenarios)."""
    n = len(REQUEST_LOG)
    REQUEST_LOG.clear()
    return {"cleared": n}


@combined_app.get("/", include_in_schema=False)
async def serve_index():
    if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
        return FileResponse(STATIC_DIR / "index.html")
    return {"message": "policy-demo MCP server is running", "status": "healthy"}


# Mount the MCP endpoint LAST so the explicit routes above take precedence.
combined_app.mount("/mcp", mcp_app)


class _McpSlashFix:
    """ASGI shim: rewrite a bare ``/mcp`` request path to ``/mcp/`` in-process.

    Starlette's ``Mount("/mcp", ...)`` issues an HTTP 307 slash-redirect when hit
    at ``/mcp`` (no trailing slash), and the redirect ``Location`` is built from
    the internal ASGI host -> ``https://localhost:8000/mcp/``. ``proxy_headers``
    does not rewrite redirect hosts, so any MCP client that calls the bare
    ``/mcp`` path (e.g. the AI Playground's UC MCP client, whose connection
    ``base_path`` is ``/mcp``) receives a 307 to localhost it cannot follow and
    fails with "status 307". Rewriting the path in the ASGI scope makes the mount
    match ``/mcp/`` directly and answer 200 with no redirect emitted at all.
    All non-matching scopes (other paths, lifespan, websocket) pass through
    untouched so FastAPI startup/shutdown still runs.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
            if scope.get("raw_path") == b"/mcp":
                scope["raw_path"] = b"/mcp/"
        await self.app(scope, receive, send)


# Exported ASGI app uvicorn serves (see server/main.py). Wraps the FastAPI
# instance so the bare-/mcp slash-redirect never reaches the client.
asgi_app = _McpSlashFix(combined_app)
