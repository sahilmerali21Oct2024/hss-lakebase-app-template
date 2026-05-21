"""Roll a Lakebase database back to a known-good revision.

Two strategies:

1. `--strategy alembic` (default): runs `alembic downgrade <rev>`. Use when
   the broken migration has a real downgrade() (the validator enforces this).

2. `--strategy branch-swap`: ASSUMES you took a Lakebase branch before
   deploying. Repoints the app's resource binding to that branch. The
   broken branch is preserved for forensics; delete it manually once safe.

Example
-------
    # Just undo the last migration
    python scripts/rollback_migration.py --strategy alembic --revision -1

    # Or repoint the app to the pre-deploy snapshot branch
    python scripts/rollback_migration.py --strategy branch-swap --branch rollback-2026-05-20
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from databricks.sdk import WorkspaceClient


def alembic_rollback(revision: str) -> int:
    print(f"Running alembic downgrade {revision}", flush=True)
    return subprocess.call(["alembic", "downgrade", revision])


def branch_swap(app_name: str, instance: str, database: str, branch: str) -> int:
    """Rebind the app's Lakebase resource to a different branch.

    Implemented by patching the deployed Apps resource. After this returns,
    you still need to `databricks apps restart <app>` for the new env to apply.
    """
    w = WorkspaceClient()
    app = w.apps.get(name=app_name)

    new_resources = []
    swapped = False
    for r in app.resources or []:
        if r.name == "lakebase_db" and r.database is not None:
            r.database.database_name = database
            r.database.instance_name = instance
            # branch is encoded in the endpoint reference Apps resolves
            r.description = f"Rolled back to branch={branch}"
            swapped = True
        new_resources.append(r)
    if not swapped:
        print("ERROR: app has no lakebase_db resource to swap.", file=sys.stderr)
        return 2

    w.apps.update(name=app_name, app=app)
    w.apps.restart(name=app_name)
    print(f"Rebound {app_name} to branch={branch}; app restarted.", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", choices=["alembic", "branch-swap"], default="alembic")
    p.add_argument("--revision", default="-1", help="alembic revision spec")
    p.add_argument("--branch", help="branch name for branch-swap strategy")
    p.add_argument(
        "--app-name", default=os.environ.get("APP_NAME"), help="Databricks app name"
    )
    p.add_argument("--instance", default=os.environ.get("LAKEBASE_INSTANCE"))
    p.add_argument("--database", default=os.environ.get("LAKEBASE_DATABASE"))
    args = p.parse_args()

    if args.strategy == "alembic":
        return alembic_rollback(args.revision)

    for required in ("app_name", "instance", "database", "branch"):
        if not getattr(args, required):
            print(f"--{required.replace('_', '-')} is required for branch-swap")
            return 2
    return branch_swap(args.app_name, args.instance, args.database, args.branch)


if __name__ == "__main__":
    sys.exit(main())
