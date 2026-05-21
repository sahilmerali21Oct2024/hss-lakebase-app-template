"""One-time setup: lock down GitHub branch protection + environment rules.

Run this once per new repo, by an org admin, immediately after the repo is created.

What it does
------------
On branch `main`:
  - Require pull request before merge.
  - Require status checks `validate / validate` and `deploy / validate` to pass.
  - Require approval from CODEOWNERS for sensitive paths.
  - Disallow force-push, disallow direct push.

On environment `prod`:
  - Required reviewer = the CODEOWNERS team.
  - Wait timer = 5 minutes (window to abort if the diff looks wrong).
  - Only `main` branch can deploy.

On environment `dev`:
  - No required reviewers (dev deploys should be fast).
  - Only `main` branch can deploy.

Why
---
Without these rules, `git push origin main --force` or a direct commit to main
will trigger `deploy.yml` and bypass PR review. Together with the workspace-side
lockdown (docs/PROD_LOCKDOWN.md) and the in-app SHA self-check (app.py), this
closes the loop: no human can deploy to prod without explicit approval.

Usage
-----
    export GITHUB_TOKEN=<a personal access token with `repo` + `admin:org`>
    python scripts/setup_branch_protection.py \\
        --repo hss-platform/my_app \\
        --team hss-platform/platform
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


GH_API = "https://api.github.com"


def _request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{GH_API}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        raise SystemExit(f"GitHub API {method} {path} -> {e.code}: {msg}")


def lock_main_branch(repo: str, token: str) -> None:
    print(f"[main] applying branch protection on {repo}@main ...")
    body = {
        "required_status_checks": {
            "strict": True,
            "checks": [
                {"context": "validate"},
                {"context": "deploy / validate"},
            ],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "require_code_owner_reviews": True,
            "dismiss_stale_reviews": True,
        },
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_linear_history": True,
        "required_conversation_resolution": True,
    }
    _request("PUT", f"/repos/{repo}/branches/main/protection", token, body)
    print("[main] done.")


def configure_environment(
    repo: str,
    env_name: str,
    token: str,
    reviewer_team_slug: str | None = None,
    wait_timer_minutes: int = 0,
) -> None:
    print(f"[{env_name}] configuring environment ...")

    reviewers: list[dict] = []
    if reviewer_team_slug:
        owner = repo.split("/")[0]
        team = _request(
            "GET",
            f"/orgs/{owner}/teams/{reviewer_team_slug.split('/')[-1]}",
            token,
        )
        reviewers.append({"type": "Team", "id": team["id"]})

    body = {
        "wait_timer": wait_timer_minutes,
        "reviewers": reviewers,
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }
    _request("PUT", f"/repos/{repo}/environments/{env_name}", token, body)

    print(f"[{env_name}] restricting deploys to branch `main` ...")
    branch_policies = _request(
        "GET",
        f"/repos/{repo}/environments/{env_name}/deployment-branch-policies",
        token,
    )
    has_main = any(
        p.get("name") == "main" for p in branch_policies.get("branch_policies", [])
    )
    if not has_main:
        _request(
            "POST",
            f"/repos/{repo}/environments/{env_name}/deployment-branch-policies",
            token,
            {"name": "main"},
        )
    print(f"[{env_name}] done.")


def main() -> int:
    import os

    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, help="org/repo")
    p.add_argument(
        "--team",
        required=True,
        help="CODEOWNERS team slug, e.g. hss-platform/platform",
    )
    p.add_argument(
        "--prod-wait-minutes",
        type=int,
        default=5,
        help="Cooling-off window before prod deploys execute.",
    )
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN env var is required.", file=sys.stderr)
        return 2

    lock_main_branch(args.repo, token)
    configure_environment(args.repo, "dev", token)
    configure_environment(
        args.repo,
        "prod",
        token,
        reviewer_team_slug=args.team,
        wait_timer_minutes=args.prod_wait_minutes,
    )

    print("\nDone. Verify in the GitHub UI:")
    print(f"  https://github.com/{args.repo}/settings/branches")
    print(f"  https://github.com/{args.repo}/settings/environments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
