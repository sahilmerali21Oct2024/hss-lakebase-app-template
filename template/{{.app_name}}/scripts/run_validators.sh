#!/usr/bin/env bash
# Shared validator. Run by BOTH validate.yml (on PR) and deploy.yml (on push to main).
# A push that skips PR review still cannot reach `bundle deploy` without passing here.
#
# Required env vars:
#   DEV_CATALOG, PROD_CATALOG  — set from bundle template at init time
#
# Exits non-zero on any violation; the deploy job will abort.

set -euo pipefail

fail() {
    echo "::error::$1"
    exit 1
}

note() {
    echo "[validator] $1"
}

: "${DEV_CATALOG:?DEV_CATALOG env var is required}"
: "${PROD_CATALOG:?PROD_CATALOG env var is required}"

note "Checking for hardcoded catalog references..."
PATTERN="${DEV_CATALOG}|${PROD_CATALOG}"
VIOLATIONS=$(grep -rnE "$PATTERN" \
    --include="*.py" --include="*.yaml" --include="*.yml" \
    --exclude="databricks.yml" \
    --exclude="permissions.yaml" \
    --exclude-dir=".github" \
    --exclude-dir=".git" \
    --exclude-dir="node_modules" \
    --exclude-dir=".venv" \
    . || true)
if [ -n "$VIOLATIONS" ]; then
    echo "$VIOLATIONS"
    fail "Hardcoded catalog references. Move to databricks.yml variables."
fi

note "Checking for hardcoded workspace URLs..."
VIOLATIONS=$(grep -rnE "https://[a-zA-Z0-9._-]+\.cloud\.databricks\.com" \
    --include="*.py" --include="*.yaml" --include="*.yml" \
    --exclude="databricks.yml" \
    --exclude-dir=".github" \
    --exclude-dir=".git" \
    --exclude-dir=".venv" \
    . || true)
if [ -n "$VIOLATIONS" ]; then
    echo "$VIOLATIONS"
    fail "Hardcoded workspace URLs. Use DATABRICKS_HOST."
fi

note "Checking for hardcoded credentials..."
VIOLATIONS=$(grep -rnE "dapi[a-f0-9]{32}|Bearer [a-zA-Z0-9]{20,}|password\s*=\s*['\"]" \
    --include="*.py" --include="*.yaml" --include="*.yml" \
    --exclude-dir=".git" \
    --exclude-dir=".venv" \
    . || true)
if [ -n "$VIOLATIONS" ]; then
    echo "$VIOLATIONS"
    fail "Possible hardcoded credentials."
fi

note "Verifying app.yaml host/port..."
grep -q "0.0.0.0" app.yaml && grep -q "8000" app.yaml || \
    fail "app.yaml must bind 0.0.0.0:8000"

PY_BIN="$(command -v python3 || command -v python)"
[ -z "$PY_BIN" ] && fail "python3 not found on PATH"

note "Verifying permissions.yaml is parseable..."
"$PY_BIN" -c "import yaml; yaml.safe_load(open('permissions.yaml'))" || \
    fail "permissions.yaml does not parse"

note "Verifying every migration has a real downgrade()..."
BAD=$("$PY_BIN" - <<'PY'
import ast, pathlib
bad = []
for p in pathlib.Path("migrations/versions").glob("*.py"):
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                bad.append(str(p))
print("\n".join(bad))
PY
)
if [ -n "$BAD" ]; then
    echo "$BAD"
    fail "Migrations with empty downgrade() — rollback impossible."
fi

note "All validators passed."
