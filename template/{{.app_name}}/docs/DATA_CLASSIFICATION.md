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
   - Reference data (code lists, lookup tables) — in `shared_ref` DB.

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
