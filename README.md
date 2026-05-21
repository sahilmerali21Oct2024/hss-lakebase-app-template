# HSS Lakebase App Template

An opinionated starter template for creating new Databricks app projects. It scaffolds the repo, deployment config, environments, and CI/CD so teams can launch new apps consistently. It is not magic app generation -- it standardizes the app foundation and deployment path.

## What It Creates

When you run `databricks bundle init`, this template generates:

- Starter app repo structure (FastAPI + optional frontend)
- `databricks.yml` with dev and prod targets
- App config and resource definitions (`app.yaml`)
- Lakebase connection pattern with OAuth token refresh
- OBO + SP dual auth modules
- Audit logging middleware (auto-captures every request)
- CI/CD workflow scaffolding (GitHub Actions)
- Pre-deploy validation checks (hardcoded catalogs, credentials)
- Alembic migration scaffold for schema changes
- `permissions.yaml` for automated SP grant management
- Claude instructions (`.claude/CLAUDE.md`) for AI-assisted development

## What It Does Not Do

- Does not generate app business logic -- you build that
- Does not remove the need to build the actual UI/workflows
- Does not replace good security and compute design
- Does not auto-provision Lakebase projects or warehouses

## Usage

```bash
databricks bundle init https://github.com/sahilmerali21Oct2024/hss-lakebase-app-template
```

You'll be prompted for:
- App name
- Database name
- Dev and prod workspace URLs
- Catalog names
- Whether you need Lakebase and OBO auth

Then:

```bash
cd <app_name>
databricks bundle validate -t dev
databricks bundle deploy -t dev
```

## Architecture Pattern

- 1 shared template repo (this one -- org-wide)
- 1 repo per app (generated from this template)
- Shared Lakebase project with branch-per-environment
- Standard CI/CD: PR → validate + deploy to dev, merge → deploy to prod
- Heavy compute offloaded to SQL Warehouses / Jobs (app compute is for UI only)

## One-Line Positioning

It's a factory template for new Databricks apps -- not an app generator.
