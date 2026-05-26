"""Apply permissions.yaml to Lakebase + Unity Catalog after deploy.

Run by .github/workflows/deploy.yml as the last step. Idempotent.

For Lakebase: uses the `databricks_auth` extension (`databricks_create_role`)
to register the app's service principal as a Postgres OAuth role, then issues
standard GRANT statements. This is the same primitive used by the Lakebase
Data API setup flow (https://docs.databricks.com/aws/en/oltp/projects/data-api)
and is the only correct way to provision identities for Autoscaling projects
that run in OAuth-only mode (which is the HSS default).

Resolves:
  - App SP client_id     -> `databricks apps get <name>`
  - Lakebase endpoint    -> SDK postgres.list_endpoints on the bound branch
  - UC catalog           -> $UC_CATALOG (injected by CI from var.catalog)

Requires the CI principal to be a Lakebase project admin on the use-case-group
project (so it can grant role memberships) and to have WITH GRANT OPTION on
the UC catalog/schema/table objects listed in permissions.yaml.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import psycopg
import yaml
from databricks.sdk import WorkspaceClient


def log(msg: str) -> None:
    print(f"[grants] {msg}", flush=True)


def load_spec(path: str) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def app_sp_client_id(w: WorkspaceClient, app_name: str) -> str:
    for attempt in range(6):
        try:
            app = w.apps.get(name=app_name)
        except Exception as e:
            log(f"apps.get failed ({e}); retrying in 10s")
            time.sleep(10)
            continue
        sp = getattr(app, "service_principal_client_id", None) or getattr(
            app, "oauth2_client_id", None
        )
        if sp:
            return sp
        log(f"SP not yet provisioned (attempt {attempt + 1}/6); sleeping 10s")
        time.sleep(10)
    raise RuntimeError(f"Could not resolve service principal for app {app_name}")


def resolve_endpoint(w: WorkspaceClient, project: str, branch: str) -> tuple[str, str]:
    """Return (endpoint_name, host) for the read-write endpoint on the branch."""
    branch_path = f"projects/{project}/branches/{branch}"
    endpoints = list(w.postgres.list_endpoints(parent=branch_path))
    if not endpoints:
        raise RuntimeError(
            f"No endpoints under {branch_path}. "
            f"Run scripts/bootstrap_use_case_group.py."
        )
    ep = next(
        (e for e in endpoints if "READ_WRITE" in str(getattr(e, "endpoint_type", ""))),
        endpoints[0],
    )
    detail = w.postgres.get_endpoint(name=ep.name)
    host = detail.status.hosts.host if detail.status and detail.status.hosts else None
    if not host:
        raise RuntimeError(f"Endpoint {ep.name} has no host yet.")
    return ep.name, host


def apply_lakebase_grants(
    w: WorkspaceClient,
    project: str,
    branch: str,
    database: str,
    spec: dict[str, Any],
    app_sp: str,
) -> None:
    """Register app SP as a Postgres OAuth role and apply schema/table grants.

    Uses `SELECT databricks_create_role('<sp>', 'SERVICE_PRINCIPAL')` from the
    `databricks_auth` extension — this is the supported primitive for
    Autoscaling projects in OAuth-only mode. We do NOT use raw `CREATE ROLE`.
    """
    if not spec:
        log("No lakebase block in permissions.yaml — skipping Lakebase grants.")
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

    role = f'"{app_sp}"'

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS databricks_auth;")
        log("Ensured databricks_auth extension on database")

        # Idempotent: databricks_create_role no-ops if the role already exists.
        cur.execute(
            "SELECT databricks_create_role(%s, 'SERVICE_PRINCIPAL');",
            (app_sp,),
        )
        log(f"databricks_create_role('{app_sp}', 'SERVICE_PRINCIPAL') OK")

        cur.execute(f"GRANT CONNECT ON DATABASE {database} TO {role};")
        log(f"GRANT CONNECT ON DATABASE {database} -> {app_sp}")

        for schema in spec.get("schemas", []) or []:
            sname = schema["name"]
            schema_grants = ", ".join(schema.get("grants", []))
            tbl_grants = ", ".join(schema.get("table_grants", []))
            seq_grants = ", ".join(schema.get("sequence_grants", []))

            if schema_grants:
                cur.execute(f"GRANT {schema_grants} ON SCHEMA {sname} TO {role};")
                log(f"GRANT {schema_grants} ON SCHEMA {sname}")
            if tbl_grants:
                cur.execute(
                    f"GRANT {tbl_grants} ON ALL TABLES IN SCHEMA {sname} TO {role};"
                )
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {sname} "
                    f"GRANT {tbl_grants} ON TABLES TO {role};"
                )
                log(f"GRANT {tbl_grants} ON TABLES IN {sname} (+ default)")
            if seq_grants:
                cur.execute(
                    f"GRANT {seq_grants} ON ALL SEQUENCES IN SCHEMA {sname} TO {role};"
                )
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {sname} "
                    f"GRANT {seq_grants} ON SEQUENCES TO {role};"
                )
                log(f"GRANT {seq_grants} ON SEQUENCES IN {sname} (+ default)")

    conn.close()


def apply_uc_grants(
    w: WorkspaceClient,
    catalog: str,
    spec: dict[str, Any],
    app_sp: str,
) -> None:
    if not spec:
        log("No unity_catalog block — skipping UC grants.")
        return

    warehouse_id = os.environ.get("UC_WAREHOUSE_ID")
    if not warehouse_id:
        raise RuntimeError(
            "UC_WAREHOUSE_ID is required to issue UC GRANT statements via SQL warehouse."
        )

    statements: list[str] = [f"GRANT USE_CATALOG ON CATALOG `{catalog}` TO `{app_sp}`;"]

    for schema in spec.get("schemas", []) or []:
        sname = schema["name"]
        schema_grants = schema.get("grants", [])
        if schema_grants:
            grants = ", ".join(schema_grants)
            statements.append(
                f"GRANT {grants} ON SCHEMA `{catalog}`.`{sname}` TO `{app_sp}`;"
            )
        for tbl in schema.get("tables", []) or []:
            tbl_grants = ", ".join(schema.get("grants", ["SELECT"]))
            if tbl == "*":
                statements.append(
                    f"GRANT {tbl_grants} ON SCHEMA `{catalog}`.`{sname}` TO `{app_sp}`;"
                )
            else:
                statements.append(
                    f"GRANT {tbl_grants} ON TABLE `{catalog}`.`{sname}`.`{tbl}` "
                    f"TO `{app_sp}`;"
                )

    for vol in spec.get("volumes", []) or []:
        vol_grants = ", ".join(vol.get("grants", ["READ_VOLUME"]))
        statements.append(
            f"GRANT {vol_grants} ON VOLUME `{catalog}`.`{vol['schema']}`.`{vol['name']}` "
            f"TO `{app_sp}`;"
        )

    for wh in spec.get("warehouses", []) or []:
        wh_id = wh["id"]
        for g in wh.get("grants", []):
            statements.append(f"GRANT `{g}` ON WAREHOUSE `{wh_id}` TO `{app_sp}`;")

    for stmt in statements:
        log(stmt)
        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=stmt,
            wait_timeout="30s",
        )
        if resp.status and resp.status.state and resp.status.state.value == "FAILED":
            err = resp.status.error.message if resp.status.error else "unknown"
            raise RuntimeError(f"GRANT failed: {stmt} -> {err}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--permissions", default="permissions.yaml")
    args = parser.parse_args()

    catalog = os.environ.get("UC_CATALOG")
    project = os.environ.get("LAKEBASE_PROJECT")
    branch = os.environ.get("LAKEBASE_BRANCH", "production")
    if not catalog:
        log("WARN: UC_CATALOG not set; UC grants will be skipped.")
    if not project:
        log("WARN: LAKEBASE_PROJECT not set; Lakebase grants will be skipped.")

    spec = load_spec(args.permissions)
    w = WorkspaceClient()

    sp = app_sp_client_id(w, args.app_name)
    log(f"Resolved app SP client_id: {sp}")

    if project and spec.get("lakebase"):
        database = spec["lakebase"].get("database") or os.environ.get("LAKEBASE_DATABASE")
        if not database:
            raise RuntimeError(
                "Could not determine Lakebase database (set in permissions.yaml or LAKEBASE_DATABASE env)."
            )
        apply_lakebase_grants(
            w,
            project=project,
            branch=branch,
            database=database,
            spec=spec["lakebase"],
            app_sp=sp,
        )

    if catalog and spec.get("unity_catalog"):
        apply_uc_grants(w, catalog=catalog, spec=spec["unity_catalog"], app_sp=sp)

    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
