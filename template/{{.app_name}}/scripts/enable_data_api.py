"""Enable the Lakebase Data API (PostgREST) for this app's database.

Idempotent. Run by .github/workflows/deploy.yml when the app was scaffolded
with expose_data_api=yes (or invoke manually for any app whose UI/operator
ergonomics benefit from PostgREST endpoints).

What this does:

  1) Ensures the `databricks_auth` extension exists on the database.
  2) Calls the Lakebase Data API enablement endpoint for the project. This:
       a) Creates the `authenticator` PostgreSQL role
       b) Sets up the `pgrst` schema
       c) Exposes the `public` schema via REST
       d) Returns the API URL
  3) Optionally applies additional advanced settings (max_rows, exposed
     schemas, CORS) from a `data_api:` block in permissions.yaml.
  4) Refreshes the schema cache so any new tables become visible immediately.

Reference:
  https://docs.databricks.com/aws/en/oltp/projects/data-api

Outputs the API URL (the same URL shown in the Lakebase UI's Data API tab).
Apps that called expose_data_api=yes get this URL in their env as
LAKEBASE_DATA_API_URL via the deploy workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg
import yaml
from databricks.sdk import WorkspaceClient


def log(msg: str) -> None:
    print(f"[data-api] {msg}", flush=True)


def load_spec(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def resolve_endpoint(w: WorkspaceClient, project: str, branch: str) -> tuple[str, str]:
    branch_path = f"projects/{project}/branches/{branch}"
    endpoints = list(w.postgres.list_endpoints(parent=branch_path))
    if not endpoints:
        raise RuntimeError(f"No endpoints under {branch_path}.")
    ep = next(
        (e for e in endpoints if "READ_WRITE" in str(getattr(e, "endpoint_type", ""))),
        endpoints[0],
    )
    detail = w.postgres.get_endpoint(name=ep.name)
    host = detail.status.hosts.host if detail.status and detail.status.hosts else None
    if not host:
        raise RuntimeError(f"Endpoint {ep.name} has no host yet.")
    return ep.name, host


def ensure_extension(w: WorkspaceClient, project: str, branch: str, database: str) -> None:
    endpoint_name, host = resolve_endpoint(w, project, branch)
    cred = w.postgres.generate_database_credential(endpoint=endpoint_name)
    conn = psycopg.connect(
        host=host,
        port=5432,
        dbname=database,
        user=w.current_user.me().user_name,
        password=cred.token,
        sslmode="require",
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS databricks_auth;")
    conn.close()
    log("Ensured databricks_auth extension")


def enable_via_api(
    w: WorkspaceClient,
    project: str,
    database: str,
    settings: dict[str, Any],
) -> str:
    """POST to the Data API enable endpoint and return the API URL.

    The exact REST shape changed over the GA timeline; we go through the SDK's
    raw request layer for portability and parse the URL from the response.
    """
    payload: dict[str, Any] = {
        "database": database,
        "exposed_schemas": settings.get("exposed_schemas", ["public"]),
    }
    if "max_rows" in settings:
        payload["max_rows"] = settings["max_rows"]
    if "cors_allowed_origins" in settings:
        payload["cors_allowed_origins"] = settings["cors_allowed_origins"]
    if "openapi_enabled" in settings:
        payload["openapi_enabled"] = settings["openapi_enabled"]
    if "server_timing_headers" in settings:
        payload["server_timing_headers"] = settings["server_timing_headers"]

    path = f"/api/2.0/postgres/projects/{project}/data-api:enable"
    log(f"POST {path} payload={json.dumps(payload)}")
    try:
        resp = w.api_client.do("POST", path, body=payload)
    except Exception as e:
        # Already enabled? Try GET on the data-api config endpoint to fetch URL.
        log(f"enable call failed ({e}); falling back to GET current config")
        resp = w.api_client.do(
            "GET", f"/api/2.0/postgres/projects/{project}/data-api"
        )
    url = (
        resp.get("url")
        or resp.get("api_url")
        or resp.get("endpoint_url")
        or (resp.get("status", {}) or {}).get("url")
    )
    if not url:
        log(f"WARN: could not parse URL from response: {resp}")
    return url or ""


def refresh_schema_cache(w: WorkspaceClient, project: str) -> None:
    path = f"/api/2.0/postgres/projects/{project}/data-api:refreshSchemaCache"
    try:
        w.api_client.do("POST", path, body={})
        log("Refreshed schema cache")
    except Exception as e:
        log(f"WARN: schema cache refresh failed ({e}); changes may take a few minutes to appear")


def write_url_to_env_file(url: str, env_file: str) -> None:
    """When run under GitHub Actions, emit LAKEBASE_DATA_API_URL=... so the
    deploy workflow can patch the app's env config in a subsequent step."""
    if not env_file:
        return
    try:
        with open(env_file, "a") as f:
            f.write(f"LAKEBASE_DATA_API_URL={url}\n")
        log(f"Wrote LAKEBASE_DATA_API_URL to {env_file}")
    except Exception as e:
        log(f"WARN: could not write to {env_file}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--permissions", default="permissions.yaml")
    args = parser.parse_args()

    project = os.environ.get("LAKEBASE_PROJECT")
    branch = os.environ.get("LAKEBASE_BRANCH", "production")
    database = os.environ.get("LAKEBASE_DATABASE")

    if not project or not database:
        log("LAKEBASE_PROJECT or LAKEBASE_DATABASE not set; skipping.")
        return 0

    spec = load_spec(args.permissions)
    settings = spec.get("data_api", {}) or {}

    w = WorkspaceClient()
    ensure_extension(w, project, branch, database)
    url = enable_via_api(w, project, database, settings)
    if url:
        log(f"Data API URL: {url}")
        write_url_to_env_file(url, os.environ.get("GITHUB_ENV", ""))
    refresh_schema_cache(w, project)
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
