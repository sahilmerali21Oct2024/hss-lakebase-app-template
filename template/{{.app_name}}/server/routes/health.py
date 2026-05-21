"""Health and diagnostic routes -- always included."""

from fastapi import APIRouter
from server.auth.config import APP_NAME, IS_DEPLOYED
import os

router = APIRouter()

{{if eq .use_lakebase "yes"}}from server.db.lakebase import db{{end}}


@router.get("/api/health")
async def health():
    result = {"status": "ok", "app": APP_NAME, "deployed": IS_DEPLOYED}
    {{if eq .use_lakebase "yes"}}pool = await db.get_pool()
    result["lakebase"] = "connected" if pool else "demo_mode"{{end}}
    return result


@router.get("/api/env")
async def env_check():
    check = ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER",
             "DATABRICKS_HOST", "DATABRICKS_APP_NAME", "APP_NAME"]
    return {v: "SET" if os.environ.get(v) else "MISSING" for v in check}


{{if eq .use_lakebase "yes"}}@router.get("/api/lakebase/test")
async def lakebase_test():
    pool = await db.get_pool()
    if not pool:
        return {"status": "demo_mode"}
    try:
        async with pool.acquire() as conn:
            return {"status": "connected", "test": await conn.fetchval("SELECT 1")}
    except Exception as e:
        return {"status": "error", "error": str(e)}{{end}}
