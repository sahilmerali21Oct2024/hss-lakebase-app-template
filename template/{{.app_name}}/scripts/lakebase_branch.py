"""Create / update / delete a Lakebase branch for an instance.

Used by .github/workflows/branch-per-pr.yml to give every PR a disposable
branch for safely testing migrations.

Notes
-----
* Branches inherit from a parent branch ('main' by default).
* Idle-timeout keeps cost bounded — a branch with no connections will scale
  to zero compute automatically.
* Deletion is idempotent.
"""

from __future__ import annotations

import argparse
import sys

from databricks.sdk import WorkspaceClient


def _branch_path(instance: str, branch: str) -> str:
    return f"projects/{instance}/branches/{branch}"


def ensure(args: argparse.Namespace) -> int:
    w = WorkspaceClient()
    path = _branch_path(args.instance, args.branch)
    try:
        b = w.postgres.get_branch(name=path)
        print(f"Branch already exists: {b.name}", flush=True)
    except Exception:
        print(f"Creating branch {path} from parent={args.parent}", flush=True)
        w.postgres.create_branch(
            project=args.instance,
            branch_name=args.branch,
            parent_branch=args.parent,
        )

    print(f"Configuring idle timeout = {args.idle_timeout_min} min", flush=True)
    try:
        w.postgres.update_endpoint(
            name=f"{path}/endpoints/primary",
            update_mask="idle_timeout_minutes",
            idle_timeout_minutes=args.idle_timeout_min,
        )
    except Exception as e:
        print(f"WARN: could not set idle timeout: {e}", flush=True)
    return 0


def delete(args: argparse.Namespace) -> int:
    w = WorkspaceClient()
    path = _branch_path(args.instance, args.branch)
    try:
        w.postgres.delete_branch(name=path)
        print(f"Deleted branch {path}", flush=True)
    except Exception as e:
        print(f"Branch not present or already deleted: {e}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("ensure")
    e.add_argument("--instance", required=True)
    e.add_argument("--branch", required=True)
    e.add_argument("--parent", default="main")
    e.add_argument("--idle-timeout-min", type=int, default=30)
    e.set_defaults(func=ensure)

    d = sub.add_parser("delete")
    d.add_argument("--instance", required=True)
    d.add_argument("--branch", required=True)
    d.set_defaults(func=delete)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
