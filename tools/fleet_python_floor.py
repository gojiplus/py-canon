#!/usr/bin/env python3
"""Raise a fleet repo's Python floor to the one STANDARD.md declares.

STANDARD.md and the template say ``requires-python = ">=3.12"``; on
2026-09-08, 24 of 47 fleet repos still said ``>=3.11``. The gap bit in
gojiplus/gringotts, whose lower-bounds job could not resolve py-canon 1.3.0
(itself >=3.12) under a 3.11 floor. preen's ``python-floor`` check exists
but ships off until the fleet moves.

Per repo: shallow clone, edit every place the floor is pinned
(``requires-python``, ruff ``target-version``, the 3.11 classifier, pyright
``pythonVersion``, and a ``python-versions`` matrix in ci.yml), ``uv lock``,
commit on a branch, open a PR, arm auto-merge. CI decides whether it lands;
nothing runs locally, because 24 test suites is a day and the gate is the
same either way. An upper bound (``>=3.11,<3.14``) is kept.

Usage:  DRY_RUN=0 ./fleet_python_floor.py [owner/repo ...]   (default: FLEET)
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

OLD, NEW = "3.11", "3.12"
BRANCH = "fleet/python-floor-312"
TITLE = "Require Python 3.12, the fleet floor"
BODY = f"""STANDARD.md and the py-canon template declare
`requires-python = ">={NEW}"`; this repo still said `>={OLD}`. The gap is not
cosmetic: py-canon 1.3.0 itself requires {NEW}, so a lower-bounds resolution
under a {OLD} floor cannot succeed (gojiplus/gringotts hit exactly that).

Mechanical edit from `tools/fleet_python_floor.py` in gojiplus/py-canon:
`requires-python`, ruff `target-version`, the `{OLD}` classifier, pyright
`pythonVersion` where set, the CI `python-versions` matrix where it named
{OLD}, and `uv lock`. Nothing hand-written; CI is the gate. If something here
is wrong for this repo, the fix belongs in the tool.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
MESSAGE = f"""{TITLE}

STANDARD.md and the template declare requires-python >={NEW}; this repo
said >={OLD}. py-canon 1.3.0 itself needs {NEW}, so a lower-bounds
resolution under a {OLD} floor cannot succeed. Mechanical edit from
tools/fleet_python_floor.py in gojiplus/py-canon; CI decides.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
"""

PYPROJECT_EDITS = [
    (re.compile(rf'(requires-python\s*=\s*")>={re.escape(OLD)}\b'), rf"\g<1>>={NEW}"),
    (
        re.compile(rf'(target-version\s*=\s*")py{OLD.replace(".", "")}"'),
        rf'\g<1>py{NEW.replace(".", "")}"',
    ),
    (re.compile(rf'(pythonVersion\s*=\s*"){re.escape(OLD)}"'), rf'\g<1>{NEW}"'),
    (re.compile(rf'(python_version\s*=\s*"){re.escape(OLD)}"'), rf'\g<1>{NEW}"'),
    (
        re.compile(
            rf'^\s*"Programming Language :: Python :: {re.escape(OLD)}",?\n',
            re.MULTILINE,
        ),
        "",
    ),
]
MATRIX_RE = re.compile(r"(python-versions:\s*')(\[[^\]]*\])(')")


def run(*args: str, cwd: pathlib.Path | None = None, check: bool = True) -> str:
    return subprocess.run(
        list(args), cwd=cwd, check=check, capture_output=True, text=True
    ).stdout


def edit_pyproject(path: pathlib.Path) -> bool:
    text = path.read_text()
    if not re.search(rf'requires-python\s*=\s*">={re.escape(OLD)}\b', text):
        return False
    for pattern, replacement in PYPROJECT_EDITS:
        text = pattern.sub(replacement, text)
    path.write_text(text)
    return True


def edit_matrix(path: pathlib.Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()

    def drop_old(match: re.Match[str]) -> str:
        versions = [v for v in json.loads(match.group(2)) if v != OLD]
        return match.group(1) + json.dumps(versions) + match.group(3)

    new = MATRIX_RE.sub(drop_old, text)
    if new == text:
        return False
    path.write_text(new)
    return True


def bump(repo: str, work: pathlib.Path, dry: bool) -> str:
    if json.loads(
        run("gh", "pr", "list", "--repo", repo, "--head", BRANCH, "--json", "url")
    ):
        return "PR already open"
    clone = work / repo.replace("/", "__")
    run("gh", "repo", "clone", repo, str(clone), "--", "--depth", "1", "-q")
    if not edit_pyproject(clone / "pyproject.toml"):
        return f"floor is not >={OLD}; nothing to do"
    edit_matrix(clone / ".github/workflows/ci.yml")
    run("uv", "lock", cwd=clone)
    stat = run("git", "diff", "--stat", cwd=clone).strip().splitlines()[-1]
    if dry:
        return f"would open PR: {stat}"
    run("git", "checkout", "-q", "-b", BRANCH, cwd=clone)
    run("git", "add", "-A", cwd=clone)
    subprocess.run(
        ["git", "commit", "-q", "-F", "-"],
        cwd=clone,
        input=MESSAGE,
        text=True,
        check=True,
        capture_output=True,
    )
    run("git", "push", "-q", "-u", "origin", BRANCH, cwd=clone)
    url = run(
        "gh",
        "pr",
        "create",
        "--repo",
        repo,
        "--head",
        BRANCH,
        "--title",
        TITLE,
        "--body",
        BODY,
    ).strip()
    try:
        run("gh", "pr", "merge", "--repo", repo, "--auto", "--squash", url)
        armed = "armed"
    except subprocess.CalledProcessError:
        armed = "auto-merge off; merge when green"
    return f"{url} ({armed}; {stat})"


def main(repos: list[str], dry: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for repo in repos:
            try:
                verdict = bump(repo, pathlib.Path(tmp), dry)
            except subprocess.CalledProcessError as exc:
                tail = (exc.stderr or exc.stdout or "").strip().splitlines()
                verdict = f"!! {tail[-1] if tail else exc}"
            print(f"== {repo:45} {verdict}", flush=True)


if __name__ == "__main__":
    fleet = pathlib.Path(__file__).parents[1] / "FLEET"
    targets = sys.argv[1:] or [r for r in fleet.read_text().split() if r]
    main(targets, dry=os.environ.get("DRY_RUN", "1") == "1")
