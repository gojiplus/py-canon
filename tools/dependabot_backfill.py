#!/usr/bin/env python3
"""Arm auto-merge on already-open Dependabot PRs.

`on: pull_request` does not retro-fire, so PRs opened before the auto-merge
workflow was fixed are never re-evaluated and sit open forever. This replays
the workflow's eligibility gate out-of-band.

Update type is derived the way dependabot/fetch-metadata does it: from the
`updated-dependencies` block in the PR's first commit message, falling back to
a semver comparison of the "Bumps X from A to B." line when that block carries
no `update-type:` (security and requirement-range updates do not). Package
ecosystem comes from the branch name, as in fetch-metadata.

Usage:  DRY_RUN=0 ./dependabot_backfill.py owner/repo [owner/repo ...]
"""

import json
import os
import re
import subprocess
import sys

RANK = {
    "version-update:semver-patch": 1,
    "version-update:semver-minor": 2,
    "version-update:semver-major": 3,
}
UNRANK = {v: k for k, v in RANK.items()}

BUMPS = re.compile(r"^Bumps .* from (?P<a>v?\d[^ ]*) to (?P<b>v?\d[^ ]*)\.", re.M)
REQ = re.compile(
    r"[Uu]pdate .* requirement from \S*?(?P<a>v?\d\S*) to \S*?(?P<b>v?\d\S*)"
)
UPDATE_TYPE = re.compile(r"^\s*update-type:\s*(\S+)", re.M)
DEP_GROUP = re.compile(r"^\s*dependency-group:\s*(\S+)", re.M)


def gh(*args, parse=True):
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out) if parse else out


def semver_delta(a: str, b: str) -> str:
    pa = a.lstrip("v").split(".")
    pb = b.lstrip("v").split(".")
    if pa[0] != pb[0]:
        return "version-update:semver-major"
    if len(pa) > 1 and len(pb) > 1 and pa[1] != pb[1]:
        return "version-update:semver-minor"
    return "version-update:semver-patch"


def classify(repo, num, branch):
    """Return (ecosystem, dependency_group, highest_update_type)."""
    ecosystem = branch.split("/")[1] if branch.count("/") >= 2 else ""
    msg = gh(
        "api",
        f"repos/{repo}/pulls/{num}/commits",
        "--jq",
        ".[0].commit.message",
        parse=False,
    )
    group_m = DEP_GROUP.search(msg)
    group = group_m.group(1) if group_m else ""

    types = [t for t in UPDATE_TYPE.findall(msg) if t in RANK]
    if types:
        return ecosystem, group, UNRANK[max(RANK[t] for t in types)]

    m = BUMPS.search(msg) or REQ.search(msg)
    if m:
        return ecosystem, group, semver_delta(m.group("a"), m.group("b"))
    return ecosystem, group, None


def eligible(ecosystem, group, update_type):
    """Mirror of the workflow's gate step. Unknown update-type is NOT eligible."""
    if ecosystem == "github_actions":
        return True
    if "minor-and-patch" in group:
        return True
    return update_type in (
        "version-update:semver-minor",
        "version-update:semver-patch",
    )


def required_contexts(repo):
    out = set()
    ids = gh("api", f"repos/{repo}/rulesets", "--jq", ".[].id", parse=False).split()
    for rid in ids:
        rs = gh("api", f"repos/{repo}/rulesets/{rid}")
        for rule in rs.get("rules", []):
            if rule["type"] == "required_status_checks":
                out |= {
                    c["context"] for c in rule["parameters"]["required_status_checks"]
                }
    return out


def pr_checks(repo, num):
    lines = subprocess.run(
        ["gh", "pr", "checks", str(num), "--repo", repo],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    out = {}
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) > 1:
            out[parts[0]] = parts[1]
    return out


def main():
    dry = os.environ.get("DRY_RUN", "1") == "1"
    for repo in sys.argv[1:]:
        req = required_contexts(repo)
        if not req:
            print(
                f"!! {repo}: no required status checks -- auto-merge would "
                f"rubber-stamp red CI. Add a ruleset first. SKIPPING."
            )
            continue

        prs = gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--author",
            "app/dependabot",
            "--limit",
            "200",
            "--json",
            "number,title,headRefName,autoMergeRequest",
        )
        if not prs:
            continue

        # A context missing from *every* open PR is orphaned (renamed by a CI
        # migration), not merely cancelled -- no retrigger will ever fix it.
        checks = {p["number"]: pr_checks(repo, p["number"]) for p in prs}
        seen = set().union(*(set(c) for c in checks.values()))
        orphaned = req - seen
        if orphaned:
            print(
                f"!! {repo}: required contexts that never report on any open "
                f"PR (fix the ruleset, not the PRs): {sorted(orphaned)}"
            )

        for pr in prs:
            n, branch = pr["number"], pr["headRefName"]
            eco, group, ut = classify(repo, n, branch)
            ok = eligible(eco, group, ut)
            armed = pr["autoMergeRequest"] is not None
            got = checks[n]
            missing = (req - orphaned) - set(got)
            cancelled = {c for c in req if got.get(c) == "cancelled"}

            verdict = "eligible" if ok else "major/unknown -> leave open"
            print(
                f"{repo}#{n:<4} eco={eco:<15} group={group or '-':<22} "
                f"type={(ut or '-'):<28} armed={armed} {verdict}"
            )
            if missing or cancelled:
                print(
                    f"      needs CI: missing={sorted(missing)} "
                    f"cancelled={sorted(cancelled)}"
                )
            if not ok or dry:
                continue

            # Arm auto-merge FIRST. If CI is retriggered first and goes green
            # while auto-merge is still off, nothing merges.
            if not armed:
                r = subprocess.run(
                    ["gh", "pr", "merge", "--auto", "--squash", "--repo", repo, str(n)],
                    capture_output=True,
                    text=True,
                )
                if r.returncode:
                    print(f"      could not arm: {r.stderr.strip()[:160]}")

            if missing or cancelled:
                # Not `gh run rerun`: that replays the workflow file from the
                # original run, i.e. the broken constant-`pages` concurrency.
                # Not close/reopen: Dependabot reads a close as "do not
                # recreate this version". A rebase force-pushes onto the fixed
                # base and retriggers everything cleanly.
                subprocess.run(
                    [
                        "gh",
                        "pr",
                        "comment",
                        str(n),
                        "--repo",
                        repo,
                        "--body",
                        "@dependabot rebase",
                    ],
                    check=True,
                )
                print("      asked dependabot to rebase")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
