"""On-Behalf-Of user auth."""

from fastapi import Request, HTTPException
from databricks.sdk import WorkspaceClient
from .config import get_workspace_host


def get_user_token(request: Request) -> str:
    token = request.headers.get("x-forwarded-access-token")
    if not token:
        raise HTTPException(status_code=401, detail="No user token found")
    return token


def get_obo_client(request: Request) -> WorkspaceClient:
    token = get_user_token(request)
    host = get_workspace_host()
    return WorkspaceClient(token=token, host=host)
