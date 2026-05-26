"""Roll a Lakebase schema change back to a known-good state.

Two supported strategies — both keep the canonical `production` branch as the
source of truth. We deliberately do NOT support "swap the app onto a snapshot
branch" — per HSS architecture guidance, branches are for validation only,
never as a promotion or rollback mechanism. The production branch must always
be the live, canonical branch for the env.

Strategies:

1. `--strategy alembic` (default, preferred):
       Run `alembic downgrade <revision>`. Use this whenever the broken
       migration has a real downgrade() (the pre-deploy validator enforces
       that every revision has one).

2. `--strategy pitr`:
       Perform a Lakebase point-in-time restore of the production branch.
       Use this when downgrade() can't safely undo a data-destructive change
       (e.g. a DROP COLUMN that lost data). This rewinds the production
       branch's storage to a timestamp before the bad migration ran.

       This is a destructive operation: any data written AFTER the timestamp
       is lost. Coordinate with stakeholders, then run:

           python scripts/rollback_migration.py --strategy pitr \\
               --timestamp 2026-05-20T14:30:00Z

Example
-------
    # Standard case: undo the last migration
    python scripts/rollback_migration.py --strategy alembic --revision -1

    # Emergency case: rewind production to 14:30 UTC today
    python scripts/rollback_migration.py --strategy pitr \\
        --timestamp 2026-05-20T14:30:00Z
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from databricks.sdk import WorkspaceClient


def alembic_rollback(revision: str) -> int:
    print(f"[rollback] alembic downgrade {revision}", flush=True)
    return subprocess.call(["alembic", "downgrade", revision])


def pitr_rollback(project: str, branch: str, timestamp: str) -> int:
    """Reset the branch's storage to a prior point in time.

    Lakebase Autoscaling supports point-in-time recovery within the project's
    retention window (default 7 days). This is the only correct rollback path
    for data-destructive migrations.
    """
    w = WorkspaceClient()
    branch_path = f"projects/{project}/branches/{branch}"
    print(
        f"[rollback] PITR: resetting {branch_path} to {timestamp}. "
        f"This will rewind storage — data written after this timestamp will be lost.",
        flush=True,
    )
    # The exact SDK call here depends on the databricks-sdk version. The CLI
    # equivalent is:
    #   databricks postgres reset-branch <branch_path> \
    #     --json '{"source_timestamp": "<ts>"}'
    # which is what the deploy workflow falls back to for portability.
    try:
        op = w.postgres.reset_branch(
            name=branch_path,
            source_timestamp=timestamp,
        )
        result = op.wait() if hasattr(op, "wait") else op
        print(f"[rollback] PITR complete: {result}", flush=True)
        return 0
    except AttributeError:
        # Older SDKs: shell out to the CLI.
        cmd = [
            "databricks",
            "postgres",
            "reset-branch",
            branch_path,
            "--json",
            f'{{"source_timestamp": "{timestamp}"}}',
        ]
        print(f"[rollback] SDK lacks reset_branch; falling back to CLI: {' '.join(cmd)}", flush=True)
        return subprocess.call(cmd)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--strategy",
        choices=["alembic", "pitr"],
        default="alembic",
        help="alembic = run downgrade() (preferred); pitr = rewind storage to a timestamp",
    )
    p.add_argument("--revision", default="-1", help="alembic revision spec")
    p.add_argument("--timestamp", help="ISO8601 timestamp for PITR (e.g. 2026-05-20T14:30:00Z)")
    p.add_argument("--project", default=os.environ.get("LAKEBASE_PROJECT"))
    p.add_argument("--branch", default=os.environ.get("LAKEBASE_BRANCH", "production"))
    args = p.parse_args()

    if args.strategy == "alembic":
        return alembic_rollback(args.revision)

    if not args.project:
        print("--project is required for PITR (or set LAKEBASE_PROJECT env var)", file=sys.stderr)
        return 2
    if not args.timestamp:
        print("--timestamp is required for PITR", file=sys.stderr)
        return 2
    return pitr_rollback(args.project, args.branch, args.timestamp)


if __name__ == "__main__":
    sys.exit(main())
