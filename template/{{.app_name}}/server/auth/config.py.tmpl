"""Dual-mode auth: detect deployed vs local, provide SP and OBO clients."""

import os
from databricks.sdk import WorkspaceClient

IS_DEPLOYED = bool(os.environ.get("DATABRICKS_APP_NAME"))
APP_NAME = os.environ.get("APP_NAME", "{{.app_name}}")


def get_workspace_host() -> str:
    if IS_DEPLOYED:
        host = os.environ.get("DATABRICKS_HOST", "")
        if host and not host.startswith("http"):
            host = f"https://{host}"
        return host
    return get_sp_client().config.host


def get_sp_client() -> WorkspaceClient:
    if IS_DEPLOYED:
        return WorkspaceClient()
    profile = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    return WorkspaceClient(profile=profile)


def get_oauth_token() -> str:
    client = get_sp_client()
    auth = client.config.authenticate()
    if auth and "Authorization" in auth:
        return auth["Authorization"].replace("Bearer ", "")
    raise ValueError("Failed to get OAuth token")
