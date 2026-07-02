"""Auth + request-context helpers.

OAuth OBO (on-behalf-of user): when deployed as a Databricks App, the platform
forwards the calling user's short-lived token in the ``x-forwarded-access-token``
header. We stash the inbound headers in a ContextVar so tools can build a
user-scoped WorkspaceClient without threading the request through every call.
"""

import contextvars
import os

from databricks.sdk import WorkspaceClient

header_store = contextvars.ContextVar("header_store")


def get_workspace_client():
    """App service-principal client (default app auth)."""
    return WorkspaceClient()


def get_user_authenticated_workspace_client():
    """User-scoped client via OAuth OBO token, falling back to local auth."""
    is_databricks_app = "DATABRICKS_APP_NAME" in os.environ

    if not is_databricks_app:
        return WorkspaceClient()

    headers = header_store.get({})
    token = headers.get("x-forwarded-access-token")

    if not token:
        raise ValueError(
            "OBO token not found in request headers (x-forwarded-access-token)."
        )

    return WorkspaceClient(token=token, auth_type="pat")
