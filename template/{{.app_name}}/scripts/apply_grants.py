"""Apply permissions.yaml to Lakebase + Unity Catalog after deploy.

Run by .github/workflows/deploy.yml as the last step. Idempotent.

Resolves:
  - App SP client_id     -> `databricks apps get <name>`
  - Lakebase host/db     -> SDK postgres.get_endpoint (via shared instance)
  - UC catalog           -> $UC_CATALOG (injected by CI from var.catalog)

Requires the CI SP to be either:
  - owner of the Lakebase database instance, OR
  - role with WITH GRANT OPTION on the schemas listed in permissions.yaml.
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
        return yaml.safe_load(f)


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


def apply_lakebase_grants(
    w: WorkspaceClient,
    instance_name: str,
    database: str,
    spec: dict[str, Any],
    app_sp: str,
) -> None:
    if not spec:
        log("No lakebase block in permissions.yaml — skipping Lakebase grants.")
        return

    endpoint = f"projects/{instance_name}/branches/main/endpoints/primary"
    ep = w.postgres.get_endpoint(name=endpoint)
    cred = w.postgres.generate_database_credential(endpoint=endpoint)

    conn = psycopg.connect(
        host=ep.status.hosts.host,
        port=5432,
        dbname=database,
        user=w.current_user.me().user_name,
        password=cred.token,
        sslmode="require",
        autocommit=True,
    )

    role = f'"{app_sp}"'

    with conn.cursor() as cur:
        cur.execute(f"GRANT CONNECT ON DATABASE {database} TO {role};")
        log(f"GRANT CONNECT ON DATABASE {database} -> {app_sp}")

        for schema in spec.get("schemas", []):
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

    for schema in spec.get("schemas", []):
        sname = schema["name"]
        schema_grants = schema.get("grants", [])
        if schema_grants:
            grants = ", ".join(schema_grants)
            statements.append(
                f"GRANT {grants} ON SCHEMA `{catalog}`.`{sname}` TO `{app_sp}`;"
            )
        for tbl in schema.get("tables", []) or []:
            if tbl == "*":
                # Workspace-wide ALL TABLES grant
                tbl_grants = ", ".join(schema.get("grants", ["SELECT"]))
                statements.append(
                    f"GRANT {tbl_grants} ON SCHEMA `{catalog}`.`{sname}` TO `{app_sp}`;"
                )
            else:
                tbl_grants = ", ".join(schema.get("grants", ["SELECT"]))
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
            statements.append(
                f"GRANT `{g}` ON WAREHOUSE `{wh_id}` TO `{app_sp}`;"
            )

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
    instance = os.environ.get("LAKEBASE_INSTANCE")
    if not catalog:
        log("WARN: UC_CATALOG not set; UC grants will be skipped.")
    if not instance:
        log("WARN: LAKEBASE_INSTANCE not set; Lakebase grants will be skipped.")

    spec = load_spec(args.permissions)
    w = WorkspaceClient()

    sp = app_sp_client_id(w, args.app_name)
    log(f"Resolved app SP client_id: {sp}")

    if instance and spec.get("lakebase"):
        apply_lakebase_grants(
            w,
            instance_name=instance,
            database=spec["lakebase"]["database"],
            spec=spec["lakebase"],
            app_sp=sp,
        )

    if catalog and spec.get("unity_catalog"):
        apply_uc_grants(w, catalog=catalog, spec=spec["unity_catalog"], app_sp=sp)

    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
