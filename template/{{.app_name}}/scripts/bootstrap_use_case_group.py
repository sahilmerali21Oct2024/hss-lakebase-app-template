"""One-time bootstrap for a use-case-group Lakebase project in a workspace.

Run ONCE per (workspace x use-case-group) — typically by a platform admin
the first time HSS spins up a new use-case group in a new env. Idempotent.

What this creates:

  1) Lakebase Autoscaling project `<use_case_group>` (if missing).
  2) `production` branch on that project, configured with no_expiry=True
     and a sane idle timeout. This is the canonical branch for the env.
  3) Each `--database` you list as a sibling DB on the production branch.
  4) The `databricks_auth` extension on each database (required for both
     SP role provisioning and group grants).

What this does NOT do:

  - Per-app SP grants. Those run during each app's deploy
    (scripts/apply_grants.py).
  - Group grants. Those run during each app's deploy
    (scripts/apply_group_grants.py).
  - Data API enablement. That runs per-app (scripts/enable_data_api.py).

Usage examples
--------------
    # Brand-new use-case group: create project + production branch +
    # 3 starter databases, all in dev workspace.
    python scripts/bootstrap_use_case_group.py \\
        --project clinical-informatics \\
        --database claims_triage_db \\
        --database care_gaps_db \\
        --database shared_reference_db

    # Add a database to an existing use-case group (re-run is safe):
    python scripts/bootstrap_use_case_group.py \\
        --project clinical-informatics \\
        --database new_app_db
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psycopg
from databricks.sdk import WorkspaceClient


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def ensure_project(w: WorkspaceClient, project: str) -> None:
    path = f"projects/{project}"
    try:
        existing = w.postgres.get_project(name=path)
        log(f"Project exists: {existing.name}")
        return
    except Exception:
        log(f"Creating project: {path}")

    # Different SDK versions accept different shapes. Try the modern form first.
    try:
        op = w.postgres.create_project(
            project_id=project,
            project={"display_name": project},
        )
        if hasattr(op, "wait"):
            op.wait()
    except TypeError:
        # Older shape
        w.postgres.create_project(name=project, display_name=project)
    log(f"Project created: {path}")


def ensure_production_branch(w: WorkspaceClient, project: str) -> None:
    branch_path = f"projects/{project}/branches/production"
    try:
        b = w.postgres.get_branch(name=branch_path)
        log(f"Production branch exists: {b.name}")
        return
    except Exception:
        log(f"Creating branch: {branch_path} (no_expiry=True)")

    try:
        op = w.postgres.create_branch(
            parent=f"projects/{project}",
            branch_id="production",
            branch={"spec": {"no_expiry": True}},
        )
        if hasattr(op, "wait"):
            op.wait()
    except TypeError:
        w.postgres.create_branch(
            project=project,
            branch_name="production",
        )
    log("Production branch created.")


def wait_for_endpoint(w: WorkspaceClient, branch_path: str, timeout_s: int = 300) -> str:
    """Wait for a READ_WRITE endpoint to be provisioned on the branch."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        endpoints = list(w.postgres.list_endpoints(parent=branch_path))
        for ep in endpoints:
            if "READ_WRITE" in str(getattr(ep, "endpoint_type", "")):
                detail = w.postgres.get_endpoint(name=ep.name)
                if (
                    detail.status
                    and detail.status.hosts
                    and detail.status.hosts.host
                ):
                    return ep.name
        log("  waiting for endpoint to provision...")
        time.sleep(10)
    raise RuntimeError(f"Timed out waiting for endpoint on {branch_path}")


def ensure_database(
    w: WorkspaceClient, project: str, database: str, endpoint_name: str
) -> None:
    """Create the database if missing, then add databricks_auth extension."""
    log(f"Ensuring database: {database}")
    try:
        op = w.postgres.create_database(
            parent=f"projects/{project}/branches/production",
            database_id=database,
            database={},
        )
        if hasattr(op, "wait"):
            op.wait()
        log(f"  database created: {database}")
    except TypeError:
        # Older SDK
        try:
            w.postgres.create_database(
                project=project,
                branch="production",
                database_name=database,
            )
            log(f"  database created (legacy API): {database}")
        except Exception as e:
            log(f"  database create skipped ({e}); assuming it already exists")
    except Exception as e:
        log(f"  database create skipped ({e}); assuming it already exists")

    # Connect and add the extension. Owner can always connect.
    detail = w.postgres.get_endpoint(name=endpoint_name)
    host = detail.status.hosts.host
    cred = w.postgres.generate_database_credential(endpoint=endpoint_name)
    user = w.current_user.me().user_name
    try:
        conn = psycopg.connect(
            host=host,
            port=5432,
            dbname=database,
            user=user,
            password=cred.token,
            sslmode="require",
            autocommit=True,
        )
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS databricks_auth;")
        conn.close()
        log(f"  databricks_auth extension ready on {database}")
    except Exception as e:
        log(f"  WARN: could not enable databricks_auth on {database}: {e}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True, help="Use-case-group name (e.g. clinical-informatics)")
    p.add_argument("--database", action="append", default=[], help="Database to create on production branch (repeatable)")
    args = p.parse_args()

    w = WorkspaceClient()
    ensure_project(w, args.project)
    ensure_production_branch(w, args.project)

    if not args.database:
        log("No --database args; project + production branch ready. Done.")
        return 0

    branch_path = f"projects/{args.project}/branches/production"
    endpoint_name = wait_for_endpoint(w, branch_path)
    log(f"Endpoint ready: {endpoint_name}")

    for db in args.database:
        ensure_database(w, args.project, db, endpoint_name)

    log("Bootstrap complete.")
    log(f"Project: projects/{args.project}")
    log(f"Branch:  {branch_path}")
    log(f"Databases: {', '.join(args.database)}")
    log("Next: deploy individual apps via their GitHub Actions workflow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
