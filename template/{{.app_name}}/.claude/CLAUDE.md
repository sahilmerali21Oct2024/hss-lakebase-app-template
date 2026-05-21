# {{.app_name}} -- Development Rules

## Auth
- OBO (x-forwarded-access-token) for all user-facing data reads from Unity Catalog
- SP for Lakebase writes, background tasks, and audit logging
- NEVER hardcode catalog names in Python -- they belong in databricks.yml targets only
- DATABRICKS_HOST may not have https:// -- always add it

## Lakebase
- Connection pool with OAuth token refresh every 45 min (server/db/lakebase.py)
- asyncpg with ssl="require"
- Schema changes through Alembic migrations, never manual ALTER TABLE
- Read PG* env vars, never hardcode connection strings

## Logging
- AuditLoggerMiddleware auto-captures every request -- don't add manual log statements
- Logs go to stdout (/logz) and should eventually write to central Delta table

## Deployment
- Always deploy through GitHub Actions, never manual upload
- bundle deploy + restart -- deploy alone doesn't restart the app
- App must listen on 0.0.0.0:8000
- Environment values in databricks.yml targets, not in Python
