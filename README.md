# HSS Lakebase App Template

An opinionated `databricks bundle init` template for Databricks Apps backed
by Lakebase. It scaffolds the repo, deployment pipeline, schema-migration
tooling, and governance docs so every new app starts from the same
production-grade foundation.

It is **not** an app generator. It standardizes the *platform* layer so
teams can build feature code without re-debating ops every time.

## What it gives you

| Pain point (May 20 session) | What the template ships |
|---|---|
| 1. Lakebase project/database organization | One shared instance, one DB per app — documented in `docs/ARCHITECTURE.md` and encoded in `databricks.yml` variables. |
| 2. Schema changes + rollback in prod | Full Alembic scaffold (`alembic.ini`, `env.py` with OAuth, example migration). `downgrade()` mandatory — enforced by validator. `scripts/rollback_migration.py` supports alembic-downgrade and branch-swap. |
| 3. Branching model unclear | `branch-per-pr.yml` workflow + `scripts/lakebase_branch.py` create/teardown disposable PR branches with idle-timeout. Cost model documented. |
| 4. Test → prod promotion broken | `deploy.yml` does `validate (gate) → migrate → deploy → grant → restart → smoke-test → record SHA`. `validate.yml` runs the same validator on PRs. Direct-to-main pushes still get validated by the gate job in `deploy.yml`. CODEOWNERS + `setup_branch_protection.py` lock down `main`; in-app `EXPECTED_GIT_SHA` self-check refuses to start the app after manual deploys; `drift-detection.yml` runs hourly and pages on bypasses. Workspace-side lockdown documented in `docs/PROD_LOCKDOWN.md`. |
| 5. App SP permissioning manual | `permissions.yaml` is **actually consumed** by `scripts/apply_grants.py`, which runs as the last step of every deploy. No more "ask Eric." |
| 6. No standardized template / logging | Common FastAPI structure, OBO + SP auth helpers, `AuditLoggerMiddleware`. Workspace-wide `logs-sink/` SDP pipeline lands all app audit events into one Delta table. |
| 7. UC ↔ Lakebase governance gap | `docs/DATA_CLASSIFICATION.md` is the binding rule: sensitive data stays in UC, read via OBO. `.claude/CLAUDE.md` enforces in-loop. |
| 8. Cross-env Lakebase testing limitation | Explicitly documented (`docs/ARCHITECTURE.md §2`) along with the supported alternative (snapshot/PR branches inside the same workspace). |

## Usage

```bash
databricks bundle init https://github.com/sahilmerali21Oct2024/hss-lakebase-app-template
```

You'll be prompted for app name, database name, Lakebase instance name,
dev + prod workspace URLs, dev + prod catalog names, audit catalog, and
which optional features to enable (Lakebase, OBO, branch-per-PR).

Then:

```bash
cd <app_name>
cat docs/RUNBOOK.md          # first-time GitHub setup
databricks bundle validate -t dev
databricks bundle deploy -t dev
python scripts/apply_grants.py --app-name <app_name>
```

After the first deploy, **all subsequent deploys go through GitHub
Actions**. The architecture document explains why.

## Architecture (one diagram)

```
                ┌──────────────────────────────┐
   PR open ──►  │  branch-per-pr.yml           │  pr-N branch + alembic upgrade
                └──────────────────────────────┘
                              │
                              ▼ (merge)
                ┌──────────────────────────────┐
                │  deploy.yml                  │
                │   validate                   │
                │   alembic upgrade head       │
                │   bundle deploy              │
                │   apply_grants.py            │  ← reads permissions.yaml
                │   apps restart               │
                │   smoke-test /api/health     │
                └──────────────────────────────┘
                              │
                              ▼
                  ┌────────────────────────┐
                  │  Databricks App        │
                  │  ─ AuditLogger MW ─────┼──►  /logz  ──►  logs-sink pipeline
                  │  ─ OBO -> UC reads     │                    │
                  │  ─ SP  -> Lakebase r/w │                    ▼
                  └────────────────────────┘          audit.app_events (Delta)
                              │
                              ▼
                  ┌────────────────────────┐
                  │  Lakebase instance     │
                  │  (one per workspace)   │
                  │   ├─ app_a_db          │
                  │   ├─ app_b_db          │
                  │   └─ shared_ref        │
                  └────────────────────────┘
```

## What it does NOT do

- Does not generate your business logic.
- Does not replace good security and compute design.
- Does not auto-provision the Lakebase instance itself, or the SQL
  warehouse used by the grants script. Those are platform-team
  one-time setup (documented in `docs/RUNBOOK.md`).

## Architecture one-liner

A factory template for new Databricks apps. The platform is opinionated,
your feature code is free.
