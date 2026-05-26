# Data Classification Policy

This policy fills the governance gap between Unity Catalog and Lakebase.
Lakebase does **not** support row filters, column masks, or tag-based
policies. UC does. Therefore:

## Rules

1. **PHI / PII / regulated data lives in Unity Catalog.**
   It is read by the app **on behalf of the user (OBO)**. UC's row filters
   and column masks then apply automatically.

2. **Lakebase holds app-owned state.** Examples that are appropriate:
   - User preferences, saved views, drafts.
   - Workflow state (which user approved which item, when).
   - Derived / aggregated results that are already de-identified.
   - Reference data (code lists, lookup tables) — in the use-case-group
     project's `shared_reference_db` if other apps in the group also need it.

### The shared reference database pattern

Within a use-case-group Lakebase project, you can optionally provision a
shared database called `shared_reference_db`. It exists alongside each app's
own database on the `production` branch and is used for **synced UC tables
or reference data that multiple apps in the group need to read with their
own per-app indexes**.

```
projects/clinical-informatics/branches/production/
    ├── databases/claims_triage_db        ← app A writes here
    ├── databases/care_gaps_db            ← app B writes here
    └── databases/shared_reference_db     ← READ-ONLY for both A and B
        ├── public.patient_master         (synced from UC)
        ├── public.icd10_codes            (synced from UC)
        └── ...
```

Rules for `shared_reference_db`:

- **All apps in the group get SELECT-only by default.** Write access is
  reserved for the sync pipeline / platform team.
- **PHI is still subject to Rule 1.** A patient master that contains PHI
  must live in UC and be read via OBO — do *not* sync it into
  `shared_reference_db` unless the synced copy is the already-filtered
  view, with the data steward's sign-off.
- **Per-app indexes are still per-app.** Each app declares the indexes it
  needs in `shared_reference_db` via its own migrations. Indexes don't
  affect data classification; they are local optimizations.
- **To opt this app in**, set `needs_shared_reference_data=yes` at
  scaffold time, then uncomment the `shared_reference:` block in
  `permissions.yaml`.

3. **If you must cache sensitive data in Lakebase**, you take on ownership
   of the controls UC would have given you. That means:
   - Write the same row filter as a SQL view in Lakebase.
   - Enforce it server-side in the FastAPI route (filter by
     `x-forwarded-email` or token claim from `get_user_token(request)`).
   - Document the equivalence to the UC policy in `permissions.yaml`
     comments.
   - Get a sign-off from the data steward in the PR.

4. **Synced tables (UC → Lakebase)** are subject to the same rule. If the
   source UC table has a row filter, the synced Lakebase copy must be the
   already-filtered view, not the raw table.

## Triage checklist for PRs that add a new Lakebase table

- [ ] Does this table contain any field listed in the HSS data inventory as
      `PHI` or `PII`?
- [ ] If yes — is the same data already in UC under a row filter? If yes,
      read it from UC via OBO; do not duplicate it into Lakebase.
- [ ] If you still need a Lakebase copy, did you write the matching filter
      as a SQL view, and call it out in the PR description?
- [ ] Did the data steward approve?

## What the template enforces automatically

- Audit logger captures *who* did *what* and *when* on every request.
- App SP gets only the schemas listed in `permissions.yaml` (least
  privilege).
- Validator blocks hardcoded credentials in code.

## What the template cannot enforce (review-time gate)

- Whether the data you are landing is actually safe to land.
- Whether your manual server-side filter matches the UC policy.

These are the rules the architecture session agreed on. If they need to
change, change *this file* in a PR, get sign-off, then propagate.
