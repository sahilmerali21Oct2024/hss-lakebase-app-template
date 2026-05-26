"""Lakebase Autoscaling connection pool with OAuth credential auto-refresh.

Why this file exists and is shaped this way:

  Lakebase Autoscaling does NOT auto-inject PG* env vars when the app is bound
  to a project via the bundle `postgres:` resource (the binding itself is
  brittle — it requires internal database UIDs and the terraform provider
  crashes on missing fields). The proven pattern, used by production HSS apps,
  is to skip the bundle binding and have the app connect at runtime via the
  SDK using the use-case-group project / branch / database it was deployed
  against. Those three values are passed in as plain env vars (set in
  app.yaml from databricks.yml variables).

What this pool does:

  1) On first request, resolve the read-write endpoint of the configured
     branch via `w.postgres.list_endpoints(parent=<branch_path>)`.
  2) Generate a short-lived (~1h) OAuth credential via
     `w.postgres.generate_database_credential(endpoint=<endpoint_name>)`.
  3) Build an asyncpg pool keyed by PROJECT/BRANCH/DATABASE.
  4) Refresh credentials every 45 min in the background.

If LAKEBASE_PROJECT is not set, the pool stays in demo mode and the app boots
without a database connection — useful for local development without a
Databricks workspace.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import asyncpg
from databricks.sdk import WorkspaceClient


def _branch_path() -> str:
    project = os.environ.get("LAKEBASE_PROJECT")
    branch = os.environ.get("LAKEBASE_BRANCH", "production")
    if not project:
        raise RuntimeError("LAKEBASE_PROJECT env var is required.")
    return f"projects/{project}/branches/{branch}"


def _resolve_endpoint(w: WorkspaceClient) -> str:
    """Find the read-write endpoint on the configured branch.

    Honors LAKEBASE_ENDPOINT_NAME if set (explicit override); otherwise picks
    the first endpoint whose type contains READ_WRITE; otherwise the first
    endpoint on the branch.
    """
    explicit = os.environ.get("LAKEBASE_ENDPOINT_NAME")
    if explicit:
        return explicit
    branch_path = _branch_path()
    endpoints = list(w.postgres.list_endpoints(parent=branch_path))
    if not endpoints:
        raise RuntimeError(
            f"No endpoints found under {branch_path}. "
            f"Run scripts/bootstrap_use_case_group.py for this env."
        )
    for ep in endpoints:
        if "READ_WRITE" in str(getattr(ep, "endpoint_type", "")):
            return ep.name
    return endpoints[0].name


class LakebasePool:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._demo_mode = False

    async def get_pool(self) -> Optional[asyncpg.Pool]:
        if not os.environ.get("LAKEBASE_PROJECT"):
            self._demo_mode = True
            return None
        if self._pool is None:
            await self._create_pool()
            self._refresh_task = asyncio.create_task(self._refresh_loop())
        return self._pool

    async def _create_pool(self) -> None:
        try:
            w = WorkspaceClient()
            endpoint_name = _resolve_endpoint(w)
            endpoint = w.postgres.get_endpoint(name=endpoint_name)
            host = (
                endpoint.status.hosts.host
                if endpoint.status and endpoint.status.hosts
                else None
            )
            if not host:
                raise RuntimeError(f"Endpoint {endpoint_name} has no host yet.")
            cred = w.postgres.generate_database_credential(endpoint=endpoint_name)
            user = w.current_user.me().user_name
            self._pool = await asyncpg.create_pool(
                host=host,
                port=5432,
                database=os.environ.get("LAKEBASE_DATABASE", "databricks_postgres"),
                user=user,
                password=cred.token,
                ssl="require",
                min_size=2,
                max_size=10,
            )
            print(f"Lakebase connected: {endpoint_name} db={os.environ.get('LAKEBASE_DATABASE')}")
        except Exception as e:
            print(f"Lakebase connection failed: {e}")
            self._demo_mode = True

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(45 * 60)
            try:
                if self._pool:
                    await self._pool.close()
                    self._pool = None
                await self._create_pool()
            except Exception as e:
                print(f"Lakebase credential refresh failed: {e}")

    @property
    def is_demo_mode(self) -> bool:
        return self._demo_mode

    async def close(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._pool:
            await self._pool.close()


db = LakebasePool()
