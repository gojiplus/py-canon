#!/usr/bin/env python3
"""Add `allow: dependency-type: all` to the uv block of every fleet dependabot.yml.

Not fleet-push-file.sh: that overwrites the whole file, and repos carry their
own groups on top of the template (appeler/pranaam pairs pandas with
pandas-stubs). This inserts one block after the uv ecosystem's `directory:`
line and leaves the rest of the file alone. Repos already carrying an `allow:`
in that block are skipped.

Usage:  DRY_RUN=0 ./fleet_dependabot_allow.py [owner/repo ...]   (default: FLEET)
"""

import base64
import contextlib
import json
import os
import pathlib
import subprocess
import sys

BLOCK = """    # Dependabot's default keeps only the dependencies pyproject.toml names
    # current; everything they pull in sits in uv.lock untouched until an
    # advisory or an upstream floor forces the issue (reporoulette carried
    # grpcio 1.76 for eleven months). The grouped weekly PR absorbs the churn.
    allow:
      - dependency-type: "all"
"""
UV_HEADER = '  - package-ecosystem: "uv"\n'
MESSAGE = pathlib.Path(__file__).with_suffix(".msg").read_text()


def gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def patched(text: str) -> str | None:
    """Return the file with the block inserted, or None if nothing to do."""
    start = text.find(UV_HEADER)
    if start < 0:
        return None
    end = text.find("\n  - package-ecosystem:", start + 1)
    block = text[start : end if end > 0 else len(text)]
    if "allow:" in block:
        return None
    marker = '    directory: "/"\n'
    at = block.find(marker)
    if at < 0:
        return None
    at = start + at + len(marker)
    return text[:at] + BLOCK + text[at:]


def main(repos: list[str], dry: bool) -> None:
    for repo in repos:
        path = ".github/dependabot.yml"
        branch = gh("api", f"repos/{repo}", "--jq", ".default_branch").strip()
        try:
            meta = json.loads(gh("api", f"repos/{repo}/contents/{path}?ref={branch}"))
        except subprocess.CalledProcessError:
            print(f"== {repo:32} no dependabot.yml")
            continue
        current = base64.b64decode(meta["content"]).decode()
        new = patched(current)
        if new is None:
            why = "already allows" if "allow:" in current else "no uv block"
            print(f"== {repo:32} skip: {why}")
            continue
        print(f"== {repo:32} insert on {branch}")
        if dry:
            continue
        try:
            out = gh(
                "api",
                "-X",
                "PUT",
                f"repos/{repo}/contents/{path}",
                "-f",
                f"message={MESSAGE}",
                "-f",
                f"content={base64.b64encode(new.encode()).decode()}",
                "-f",
                f"branch={branch}",
                "-f",
                f"sha={meta['sha']}",
                "--jq",
                ".commit.html_url",
            )
        except subprocess.CalledProcessError:
            # Every fleet ruleset requires `ci / gate` on the default branch,
            # but only the repos that also grant admins a bypass accept a
            # direct write (26 of 43 on the first sweep). The rest need the
            # change to arrive the way the ruleset expects: on a branch, as
            # a PR, with auto-merge armed so gate lands it.
            print("   direct write refused by ruleset; opening a PR instead")
            out = via_pull_request(repo, branch, path, meta["sha"], new)
        print(f"   -> {out.strip()}")


def via_pull_request(repo: str, base: str, path: str, sha: str, new: str) -> str:
    head = "fleet/dependabot-transitive"
    existing = gh(
        "pr", "list", "--repo", repo, "--head", head, "--state", "open", "--json", "url"
    )
    if urls := json.loads(existing):
        return urls[0]["url"] + " (already open)"
    tip = gh("api", f"repos/{repo}/git/ref/heads/{base}", "--jq", ".object.sha").strip()
    # A branch left over from an earlier sweep is reused.
    with contextlib.suppress(subprocess.CalledProcessError):
        gh(
            "api",
            "-X",
            "POST",
            f"repos/{repo}/git/refs",
            "-f",
            f"ref=refs/heads/{head}",
            "-f",
            f"sha={tip}",
        )
    gh(
        "api",
        "-X",
        "PUT",
        f"repos/{repo}/contents/{path}",
        "-f",
        f"message={MESSAGE}",
        "-f",
        f"content={base64.b64encode(new.encode()).decode()}",
        "-f",
        f"branch={head}",
        "-f",
        f"sha={sha}",
    )
    title, _, body = MESSAGE.partition("\n\n")
    url = gh(
        "pr",
        "create",
        "--repo",
        repo,
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
        "--body",
        body,
    ).strip()
    # 16 of the 17 repos that refuse direct writes also have auto-merge
    # switched off, so arming fails there; the PR is left open to merge
    # by hand once gate is green.
    try:
        gh("pr", "merge", "--repo", repo, "--auto", "--squash", url)
    except subprocess.CalledProcessError:
        return url + " (auto-merge off; merge when green)"
    return url + " (auto-merge armed)"


if __name__ == "__main__":
    fleet = pathlib.Path(__file__).parents[1] / "FLEET"
    targets = sys.argv[1:] or [r for r in fleet.read_text().split() if r]
    main(targets, dry=os.environ.get("DRY_RUN", "1") == "1")
