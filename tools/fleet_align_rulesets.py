#!/usr/bin/env python3
"""Bring every fleet repo's default-branch ruleset and merge settings to the standard.

STANDARD.md asks for one shape: a ruleset on the default branch that requires
CI, a repo-admin bypass for direct pushes, and auto-merge on. A survey on
2026-09-08 found 26 of 47 repos there and the rest drifted three ways:

  17  ruleset with no bypass actors, auto-merge and delete-on-merge off
      (the finite-sample repos adopted in August, plus lost-years,
      paper_voice, rowvoi). A fleet sweep that writes a file straight to
      main is refused there, and the Dependabot sweep cannot arm auto-merge,
      so it has to merge outright.
   1  calibre: bypass present, merge settings off.
   1  slosizer: merge settings on, no bypass.
   4  no ruleset at all. Three of them run the canon CI and get the
      standard ruleset; uijudge-bench does not and is left alone, because a
      repo with nothing required must not have auto-merge (STANDARD.md).

Required checks are not touched here; that is set-required-checks.sh.
GET-mutate-PUT on the ruleset, for the reason that script gives.

Usage:  DRY_RUN=0 ./fleet_align_rulesets.py [owner/repo ...]   (default: FLEET)
"""

import json
import os
import pathlib
import subprocess
import sys

# GitHub's fixed id for the Admin repository role.
ADMIN_BYPASS = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
GATE = "ci / gate"
STANDARD_RULESET = {
    "name": "default branch: CI required for PR merges",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "bypass_actors": [ADMIN_BYPASS],
    "rules": [
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": False,
                "do_not_enforce_on_create": False,
                "required_status_checks": [{"context": GATE}],
            },
        }
    ],
}


def gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, input=stdin
    ).stdout


def gh_json(*args: str, stdin: str | None = None) -> dict:
    return json.loads(gh(*args, stdin=stdin))


def has_admin_bypass(ruleset: dict) -> bool:
    return any(
        a.get("actor_type") == "RepositoryRole"
        and a.get("actor_id") == ADMIN_BYPASS["actor_id"]
        and a.get("bypass_mode") == "always"
        for a in ruleset.get("bypass_actors", [])
    )


def emits_gate(repo: str, branch: str) -> bool:
    """Whether the default branch's latest commit reported the canon gate."""
    runs = gh_json("api", f"repos/{repo}/commits/{branch}/check-runs")
    return any(run["name"] == GATE for run in runs.get("check_runs", []))


def align(repo: str, dry: bool) -> None:
    meta = gh_json("api", f"repos/{repo}")
    branch = meta["default_branch"]
    rulesets = json.loads(gh("api", f"repos/{repo}/rulesets"))
    branch_rulesets = [r for r in rulesets if r["target"] == "branch"]
    actions: list[str] = []

    if not branch_rulesets:
        if not emits_gate(repo, branch):
            print(f"== {repo:45} no ruleset and no {GATE!r} on {branch}; left alone")
            return
        actions.append("create standard ruleset")
        if not dry:
            gh(
                "api",
                "-X",
                "POST",
                f"repos/{repo}/rulesets",
                "--input",
                "-",
                stdin=json.dumps(STANDARD_RULESET),
            )
    else:
        ruleset = gh_json("api", f"repos/{repo}/rulesets/{branch_rulesets[0]['id']}")
        if not has_admin_bypass(ruleset):
            actions.append("add admin bypass")
            if not dry:
                payload = {
                    key: ruleset[key]
                    for key in ("name", "target", "enforcement", "conditions", "rules")
                }
                payload["bypass_actors"] = [
                    *ruleset.get("bypass_actors", []),
                    ADMIN_BYPASS,
                ]
                gh(
                    "api",
                    "-X",
                    "PUT",
                    f"repos/{repo}/rulesets/{ruleset['id']}",
                    "--input",
                    "-",
                    stdin=json.dumps(payload),
                )

    settings = {
        key: True
        for key in ("allow_auto_merge", "delete_branch_on_merge")
        if not meta.get(key)
    }
    if settings:
        actions.append("enable " + ", ".join(settings))
        if not dry:
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{repo}",
                "--input",
                "-",
                stdin=json.dumps(settings),
            )

    verdict = "; ".join(actions) if actions else "already aligned"
    print(f"== {repo:45} {'would ' if dry and actions else ''}{verdict}")


def main(repos: list[str], dry: bool) -> None:
    for repo in repos:
        try:
            align(repo, dry)
        except subprocess.CalledProcessError as exc:
            last = (exc.stderr or "").strip().splitlines()
            print(f"== {repo:45} !! {last[-1] if last else exc}")


if __name__ == "__main__":
    fleet = pathlib.Path(__file__).parents[1] / "FLEET"
    targets = sys.argv[1:] or [r for r in fleet.read_text().split() if r]
    main(targets, dry=os.environ.get("DRY_RUN", "1") == "1")
