"""Create / update / delete a Lakebase Autoscaling branch in a project.

Used by .github/workflows/branch-per-pr.yml to give every PR a disposable
child branch off `production`, scoped to the use-case-group project the app
belongs to.

Notes
-----
* All branches live INSIDE the use-case-group project. We never create new
  projects from this script.
* Child branches inherit from `production` (or whatever --parent points at)
  via Lakebase copy-on-write — schema + data are snapshotted at creation
  time, then diverge.
* TTL keeps cost + branch-count bounded. Production-tier branches must be
  created with `no_expiry=True`; PR branches default to 7-day TTL.
* Deletion is idempotent.
"""

from __future__ import annotations

import argparse
import sys

from databricks.sdk import WorkspaceClient


def _branch_path(project: str, branch: str) -> str:
    return f"projects/{project}/branches/{branch}"


def _project_path(project: str) -> str:
    return f"projects/{project}"


def ensure(args: argparse.Namespace) -> int:
    w = WorkspaceClient()
    path = _branch_path(args.project, args.branch)
    parent_path = _branch_path(args.project, args.parent)
    try:
        b = w.postgres.get_branch(name=path)
        print(f"[branch] already exists: {b.name}", flush=True)
    except Exception:
        print(
            f"[branch] creating {path} from parent={parent_path} "
            f"(ttl_days={args.ttl_days}, no_expiry={args.no_expiry})",
            flush=True,
        )
        # SDK API surface varies; use the dict form for portability.
        spec: dict = {"source_branch": parent_path}
        if args.no_expiry:
            spec["no_expiry"] = True
        else:
            spec["ttl"] = f"{args.ttl_days * 86400}s"
        try:
            op = w.postgres.create_branch(
                parent=_project_path(args.project),
                branch_id=args.branch,
                branch={"spec": spec},
            )
            if hasattr(op, "wait"):
                op.wait()
        except TypeError:
            # Fallback for older SDKs.
            w.postgres.create_branch(
                project=args.project,
                branch_name=args.branch,
                parent_branch=parent_path,
            )

    print(f"[branch] setting idle_timeout_min={args.idle_timeout_min}", flush=True)
    try:
        endpoints = list(w.postgres.list_endpoints(parent=path))
        for ep in endpoints:
            try:
                w.postgres.update_endpoint(
                    name=ep.name,
                    update_mask="idle_timeout_minutes",
                    idle_timeout_minutes=args.idle_timeout_min,
                )
            except Exception as e:
                print(f"[branch] WARN: could not set idle timeout on {ep.name}: {e}", flush=True)
    except Exception as e:
        print(f"[branch] WARN: could not list endpoints: {e}", flush=True)
    return 0


def delete(args: argparse.Namespace) -> int:
    w = WorkspaceClient()
    path = _branch_path(args.project, args.branch)
    if args.branch == "production":
        print("[branch] refusing to delete the production branch.", file=sys.stderr)
        return 2
    try:
        w.postgres.delete_branch(name=path)
        print(f"[branch] deleted {path}", flush=True)
    except Exception as e:
        print(f"[branch] not present or already deleted: {e}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("ensure")
    e.add_argument("--project", required=True, help="Use-case-group project name")
    e.add_argument("--branch", required=True, help="Branch id to create (e.g., pr-123)")
    e.add_argument("--parent", default="production", help="Parent branch to fork from")
    e.add_argument("--ttl-days", type=int, default=7)
    e.add_argument("--no-expiry", action="store_true", help="Make this a permanent branch (for production-tier only)")
    e.add_argument("--idle-timeout-min", type=int, default=30)
    e.set_defaults(func=ensure)

    d = sub.add_parser("delete")
    d.add_argument("--project", required=True)
    d.add_argument("--branch", required=True)
    d.set_defaults(func=delete)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
