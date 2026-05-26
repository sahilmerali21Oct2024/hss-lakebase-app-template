"""Apply Databricks AD/account-group grants to this app's Lakebase database.

Reads the `groups:` section of permissions.yaml. For each group:

  1) Updates the Lakebase project ACL to give the group `project_permission`
     (CAN_USE / CAN_MANAGE). This is required at the Lakebase layer; Postgres
     GRANTs alone are not enough — Lakebase enforces project-level access
     separately from Postgres role grants.
  2) Registers the group as a Postgres OAuth role via the databricks_auth
     extension: `SELECT databricks_create_role('<group>', 'GROUP');`. This
     is the supported primitive for Autoscaling projects, same pattern used
     by the Lakebase Data API setup flow.
  3) Issues standard SQL GRANT statements on the schemas/tables listed.

Idempotent. Safe to re-run after editing permissions.yaml — it will only
apply new grants and skip already-granted privileges.

Run by .github/workflows/deploy.yml right after apply_grants.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import psycopg
import yaml
from databricks.sdk import WorkspaceClient


def log(msg: str) -> None:
    print(f"[group-grants] {msg}", flush=True)


def load_spec(path: str) -> list[dict[str, Any]]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("groups", []) or []


def resolve_endpoint(w: WorkspaceClient, project: str, branch: str) -> tuple[str, str]:
    """Return (endpoint_name, host) for the read-write endpoint on the branch."""
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


def set_project_permission(
    w: WorkspaceClient, project: str, group: str, permission: str
) -> None:
    """Grant the group CAN_USE / CAN_MANAGE on the Lakebase project.

    Lakebase ACL is a separate layer from Postgres GRANTs — both are required
    for the group to actually reach the database via Data API or psql.
    """
    if permission not in ("CAN_USE", "CAN_MANAGE"):
        raise ValueError(f"Invalid project_permission: {permission}")
    try:
        # The SDK surface for project ACLs evolved across versions; try the
        # modern shape first, then fall back to a permissions API call.
        w.postgres.update_project_permissions(
            project=f"projects/{project}",
            access_control_list=[
                {"group_name": group, "permission_level": permission}
            ],
        )
        log(f"Project ACL: granted {permission} to group '{group}' on project '{project}'")
    except AttributeError:
        # Fallback: use the generic Permissions API.
        try:
            api = w.permissions.update_permissions(
                request_object_type="postgres-project",
                request_object_id=project,
                access_control_list=[
                    {"group_name": group, "permission_level": permission}
                ],
            )
            log(f"Project ACL (via permissions API): {permission} -> '{group}'")
        except Exception as e:
            log(f"WARN: could not update project ACL ({e}); please grant manually")
    except Exception as e:
        log(f"WARN: project ACL update failed ({e}); please verify manually")


def apply_group_grants(
    w: WorkspaceClient,
    project: str,
    branch: str,
    database: str,
    groups: list[dict[str, Any]],
) -> None:
    if not groups:
        log("No groups declared in permissions.yaml. Nothing to do.")
        return

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

        for g in groups:
            name = g["name"]
            project_permission = g.get("project_permission", "CAN_USE")

            set_project_permission(w, project, name, project_permission)

            try:
                cur.execute(
                    "SELECT databricks_create_role(%s, 'GROUP');", (name,)
                )
                log(f"databricks_create_role('{name}', 'GROUP') OK")
            except Exception as e:
                # Most likely already exists — log and continue (the GRANTs below are idempotent).
                log(f"databricks_create_role('{name}', 'GROUP') skipped: {e}")

            role = f'"{name}"'
            cur.execute(f"GRANT CONNECT ON DATABASE {database} TO {role};")
            log(f"GRANT CONNECT ON DATABASE {database} -> {name}")

            for schema in g.get("schemas", []) or []:
                sname = schema["name"]
                schema_grants = ", ".join(schema.get("grants", []))
                tbl_grants = ", ".join(schema.get("table_grants", []))
                seq_grants = ", ".join(schema.get("sequence_grants", []))

                if schema_grants:
                    cur.execute(f"GRANT {schema_grants} ON SCHEMA {sname} TO {role};")
                    log(f"  GRANT {schema_grants} ON SCHEMA {sname} -> {name}")
                if tbl_grants:
                    cur.execute(
                        f"GRANT {tbl_grants} ON ALL TABLES IN SCHEMA {sname} TO {role};"
                    )
                    cur.execute(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {sname} "
                        f"GRANT {tbl_grants} ON TABLES TO {role};"
                    )
                    log(f"  GRANT {tbl_grants} ON TABLES IN {sname} (+default) -> {name}")
                if seq_grants:
                    cur.execute(
                        f"GRANT {seq_grants} ON ALL SEQUENCES IN SCHEMA {sname} TO {role};"
                    )
                    cur.execute(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {sname} "
                        f"GRANT {seq_grants} ON SEQUENCES TO {role};"
                    )
                    log(f"  GRANT {seq_grants} ON SEQUENCES IN {sname} (+default) -> {name}")

    conn.close()


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

    groups = load_spec(args.permissions)
    if not groups:
        log("No groups declared. Skipping.")
        return 0

    w = WorkspaceClient()
    apply_group_grants(w, project, branch, database, groups)
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
