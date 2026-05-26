# HSS Lakebase App Template

An opinionated `databricks bundle init` template for Databricks Apps backed
by **Lakebase Autoscaling**. It scaffolds the repo, deployment pipeline,
schema-migration tooling, role provisioning, and governance docs so every
new app starts from the same production-grade foundation.

It is **not** an app generator. It standardizes the *platform* layer so
teams can build feature code without re-debating ops every time.

## Core architectural choice — one project per use-case group

```
workspace (dev / test / prod)
    └── Lakebase Autoscaling projects     ← one PROJECT per use-case group
        ├── clinical-informatics
        │    └── branch: production
        │         ├── database: claims_triage_db          (app)
        │         ├── database: care_gaps_db              (app)
        │         ├── database: ...        (5–10 apps total)
        │         └── database: shared_reference_db       (optional)
        ├── rev-cycle-analytics
        │    └── branch: production
        │         └── ... 5–10 databases ...
        └── dicom-imaging
             └── ... same pattern ...
```

- **Project = use-case group.** 5–10 related apps share one project.
  They share Lakebase compute + ACLs but each gets its own database (and
  therefore its own indexes).
- **Same project IDs across envs.** dev/test/prod each have their own
  copy of `clinical-informatics`, `rev-cycle-analytics`, etc. — no
  `-dev`/`-prod` suffix.
- **`production` branch is canonical.** No-expiry, never deleted.
- **PR child branches are throwaway.** Fork off `production`, run
  migrations, auto-delete on PR close (with 7-day TTL safety net).

## What it gives you

| Pain point (May 20 session) | What the template ships |
|---|---|
| 1. Lakebase project/database organization | One Autoscaling project per use-case group; one DB per app on `production` branch. Optional shared reference DB per group. Documented in `docs/ARCHITECTURE.md`. |
| 2. Schema changes + rollback in prod | Full Alembic scaffold. `downgrade()` mandatory — enforced by validator. `scripts/rollback_migration.py` does alembic-downgrade *or* Lakebase PITR for data-destructive cases. |
| 3. Branching model unclear | `branch-per-pr.yml` + `scripts/lakebase_branch.py` create/teardown child branches off `production` with 7d TTL + idle timeout. Cost shape documented. |
| 4. Test → prod promotion broken | `deploy.yml` does `validate → migrate → deploy → app-SP grants → group grants → optional Data API → restart → smoke test → SHA pin`. `validate.yml` runs the same validator on PRs. CODEOWNERS + `setup_branch_protection.py` lock down `main`; in-app `EXPECTED_GIT_SHA` self-check refuses to start after manual deploys; `drift-detection.yml` pages on bypasses. Workspace-side lockdown documented in `docs/PROD_LOCKDOWN.md`. |
| 5. App SP permissioning manual | `permissions.yaml` is **consumed** by `scripts/apply_grants.py` (every deploy) using the `databricks_auth` extension — `databricks_create_role(<sp>, 'SERVICE_PRINCIPAL')`, no raw `CREATE ROLE`, no manual UI clicks. |
| 6. No standardized template / logging | Common FastAPI structure, OBO + SP auth helpers, `AuditLoggerMiddleware`. The `logs-sink/` companion bundle lands all app audit events into one Delta table. `.claude/CLAUDE.md` keeps AI-assisted edits inside the guard rails. |
| 7. UC ↔ Lakebase governance gap | `docs/DATA_CLASSIFICATION.md` is the binding rule: PHI stays in UC, read via OBO. Lakebase RLS supported via standard `CREATE POLICY` in migrations. |
| 8. Cross-env Lakebase testing limitation | Solved by the per-PR child branch model — same workspace, same project, ephemeral branch. Cross-workspace sync is documented as a separate sync pipeline pattern, not point-in-time queries. |
| **Bonus 1. AD group access** | `permissions.yaml` `groups:` section + `scripts/apply_group_grants.py` use `databricks_create_role(<group>, 'GROUP')` to register Databricks AD groups as Postgres roles + set project ACL. |
| **Bonus 2. Data API (opt-in)** | `expose_data_api=yes` at scaffold time wires `scripts/enable_data_api.py` into the deploy. PostgREST-compatible REST endpoints with RLS enforcement. |
| **Bonus 3. Bootstrap script** | `scripts/bootstrap_use_case_group.py` provisions a new project + production branch + databases + `databricks_auth` extension. Replaces ad-hoc UI clicks. |

## Usage

### Scaffolding a new app

```bash
databricks bundle init https://github.com/sahilmerali21Oct2024/hss-lakebase-app-template
```

You'll be prompted for:

- `app_name` — lowercase hyphenated, e.g. `claims-triage`
- `use_case_group` — which group this app belongs to, e.g. `clinical-informatics`
- `database_name` — defaults to `<app_name>_db`
- `needs_shared_reference_data` — yes/no
- `dev_workspace_url` / `prod_workspace_url`
- `dev_catalog` / `prod_catalog` (UC catalogs, not Lakebase)
- `audit_catalog` — where `audit.app_events` lives (shared platform catalog)
- `use_obo` — On-Behalf-Of for UC reads
- `expose_data_api` — Lakebase PostgREST endpoints
- `enable_branch_per_pr` — child branch per PR
- `github_codeowners_team` / `github_org_repo`

### First-time workspace setup (per use-case group)

ONCE per (workspace x use-case-group), a platform admin runs:

```bash
cd <app_name>
python scripts/bootstrap_use_case_group.py \
    --project <use_case_group> \
    --database <app_database>
```

This creates the project, the `production` branch (no-expiry), the
database, and the `databricks_auth` extension. Repeat in each workspace.

### Then per-app deploy goes through GitHub Actions

```bash
cd <app_name>
cat docs/RUNBOOK.md          # GitHub repo + Environment setup
git init && git add . && git commit -m "initial scaffold"
git remote add origin <your repo URL>
git push -u origin main
# deploy.yml runs automatically
```

After the first deploy, **all subsequent deploys go through GitHub
Actions**. The architecture document explains why.

## What it does NOT do

- Does not generate your business logic.
- Does not replace good security and compute design — read `DATA_CLASSIFICATION.md`.
- Does not auto-create AD groups or the CI principal. Those are
  platform-team one-time setup (documented in `docs/RUNBOOK.md`).
- Does not enforce manual deploy bans at the workspace level — that's
  done by the workspace admin per `docs/PROD_LOCKDOWN.md`. The template
  enforces it at the app level via `EXPECTED_GIT_SHA`.

## Architecture one-liner

A factory template for new HSS Databricks apps. Platform is opinionated;
your feature code is free.
