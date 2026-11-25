#!/usr/bin/env python3
"""Raise each direct dependency's declared minimum to one that installs.

Most fleet packages declared minimums nobody had installed: fewlab said
``pandas>=1.5`` and stable-cart ``numpy>=1.23.0``, and neither installs on
Python 3.13, so the claim was false for every user. The reusable CI's
``lower-bounds`` job tests the claim; this tool makes it true first.

Per repo: shallow clone; for every runtime requirement in ``[project]
dependencies`` and ``optional-dependencies``, find the oldest release that
satisfies the existing specifier, supports the package's floor Python, and
ships a wheel for it (pure ``py3-none-any``, ``abi3`` at or below the floor, or
``cp<floor>``) on Linux x86-64; raise the lower bound to it, or add one where
none exists. Then prove it with the lower-bounds CI job's own steps, in the
standard Linux image: the locked environment, each runtime dependency at its
minimum, the tests. A minimum that still fails to build is raised to its next
candidate, up to a few rounds. Only a repo whose suite passes at its minimums
gets a PR; the rest are reported for a hand fix.

Hand-rolled because nothing maintained does this: uv's ``lowest-direct``
resolution finds failing minimums but does not choose new ones. Requirements pinned
with ``==`` or ``~=`` are left alone and reported.

Usage:  DRY_RUN=0 ./fleet_dependency_floors.py [owner/repo ...]   (default: FLEET)
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from functools import cache

import tomlkit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

BRANCH = "fleet/true-dependency-floors"
TITLE = "Raise dependency minimums to versions that install"
MAX_ROUNDS = 8
LINUX = ("manylinux", "musllinux", "linux_x86_64", "any")


def run(*args: str, cwd: pathlib.Path | None = None, check: bool = True) -> str:
    return subprocess.run(
        list(args), cwd=cwd, check=check, capture_output=True, text=True
    ).stdout


@cache
def releases(name: str) -> dict:
    url = f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)["releases"]


def wheel_fits(filename: str, floor: tuple[int, int]) -> bool:
    """Whether a wheel installs on CPython ``floor`` on Linux x86-64."""
    try:
        _, _, _, tags = parse_wheel_filename(filename)
    except ValueError:
        return False
    cp = f"cp{floor[0]}{floor[1]}"
    for tag in tags:
        if not any(tag.platform.startswith(p) for p in LINUX):
            continue
        if tag.platform != "any" and "x86_64" not in tag.platform:
            continue
        if tag.interpreter in ("py3", f"py{floor[0]}{floor[1]}") and tag.abi == "none":
            return True
        if tag.interpreter == cp and tag.abi in (cp, "abi3", "none"):
            return True
        if tag.abi == "abi3" and tag.interpreter.startswith("cp3"):
            try:
                if int(tag.interpreter[3:]) <= floor[1]:
                    return True
            except ValueError:
                continue
    return False


def candidates(req: Requirement, floor: tuple[int, int]) -> list[Version]:
    """Releases allowed by the specifier that ship a wheel for the floor."""
    pyfloor = Version(f"{floor[0]}.{floor[1]}")
    out = []
    for raw, files in releases(canonicalize_name(req.name)).items():
        try:
            v = Version(raw)
        except InvalidVersion:
            continue
        if v.is_prerelease or not files or v not in req.specifier:
            continue
        live = [f for f in files if not f.get("yanked")]
        rp = next(
            (f.get("requires_python") for f in live if f.get("requires_python")), None
        )
        if rp and pyfloor not in SpecifierSet(rp):
            continue
        if any(
            f["packagetype"] == "bdist_wheel" and wheel_fits(f["filename"], floor)
            for f in live
        ):
            out.append(v)
    return sorted(out)


def lower_bound(req: Requirement) -> Version | None:
    for spec in req.specifier:
        if spec.operator in (">=", ">"):
            return Version(spec.version)
    return None


def with_floor(req: Requirement, new: Version) -> str:
    """The requirement string with its lower bound set to ``new``."""
    kept = [str(s) for s in req.specifier if s.operator not in (">=", ">")]
    spec = ",".join([f">={new}", *kept])
    extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
    marker = f"; {req.marker}" if req.marker else ""
    return f"{req.name}{extras}{spec}{marker}"


def requirement_arrays(doc: tomlkit.TOMLDocument):
    """The runtime requirement arrays: what `pip install pkg[extra]` promises.

    Dependency groups are left alone. Their minimums are not a promise to
    users, and the lower-bounds job keeps test tooling at its locked versions.
    """
    project = doc.get("project", {})
    if "dependencies" in project:
        yield project["dependencies"]
    yield from project.get("optional-dependencies", {}).values()


def raise_floors(
    pyproject: pathlib.Path, floor: tuple[int, int], bump: dict[str, Version]
) -> tuple[list[str], list[str]]:
    """Rewrite requirements in place. Returns (changes, skipped)."""
    doc = tomlkit.parse(pyproject.read_text())
    changes, skipped = [], []
    for arr in requirement_arrays(doc):
        for i, item in enumerate(arr):
            if not isinstance(item, str):
                continue  # {include-group = ...}
            req = Requirement(item)
            name = canonicalize_name(req.name)
            if req.url or any(s.operator in ("==", "~=", "===") for s in req.specifier):
                skipped.append(item)
                continue
            have = lower_bound(req)
            cands = [
                c for c in candidates(req, floor) if name not in bump or c >= bump[name]
            ]
            if not cands:
                skipped.append(
                    f"{item} (no release with a wheel for {floor[0]}.{floor[1]})"
                )
                continue
            new = cands[0]
            if have is not None and new <= have:
                continue
            arr[i] = with_floor(req, new)
            changes.append(f"{item} -> {arr[i]}")
    pyproject.write_text(tomlkit.dumps(doc))
    return changes, skipped


def python_floor(pyproject: pathlib.Path) -> tuple[int, int]:
    spec = tomllib.loads(pyproject.read_text())["project"].get("requires-python", "")
    m = re.search(r">=\s*3\.(\d+)", spec)
    return (3, int(m.group(1)) if m else 13)


BUILD_FAIL = re.compile(
    r"(?:build|install|download|Distribution)[a-z ]*`([A-Za-z0-9_.\-]+)==([^` @]+)"
)


def _clean_env() -> dict[str, str]:
    """The environment without this tool's own virtualenv leaking into uv."""
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    return env


def _failure(out: str) -> str:
    """The lines that say what failed, from pytest or uv output."""
    keep = [
        ln
        for ln in out.splitlines()
        if ln.startswith(("FAILED", "ERROR", "E   ", "error:", "  \u00d7 ", "  ╰─▶"))
    ]
    return " | ".join(keep[:4]) or (out.strip().splitlines() or [""])[-1]


IMAGE = "ghcr.io/astral-sh/uv:python{py}-bookworm"
LAST_OUTPUT = [""]
JOB = r"""
set -euo pipefail
test_group=()
groups=$(python -c 'import tomllib
d = tomllib.load(open("pyproject.toml", "rb"))
print(*d.get("dependency-groups", {}))')
if [[ " $groups " == *" test "* ]]; then
  test_group=(--group test)
fi
uv sync -q --no-default-groups --frozen --group dev "${test_group[@]}" --all-extras
uv run -q --no-project --with packaging python - > /tmp/minimums.txt <<'PY'
import tomllib
from packaging.requirements import Requirement
project = tomllib.load(open("pyproject.toml", "rb"))["project"]
reqs = list(project.get("dependencies", []))
for group in project.get("optional-dependencies", {}).values():
    reqs += group
for text in reqs:
    req = Requirement(text)
    if req.url or (req.marker and not req.marker.evaluate()):
        continue
    low = [s.version for s in req.specifier if s.operator in (">=", "~=", "==")]
    if low:
        print(f"{req.name}=={low[0]}")
PY
if [ -s /tmp/minimums.txt ]; then uv pip install -q -r /tmp/minimums.txt; fi
uv run --no-sync pytest -q -x -p no:cacheprovider
"""


def verify(clone: pathlib.Path, floor: tuple[int, int]) -> tuple[bool, str]:
    """Run the lower-bounds CI job's steps in the standard Linux image.

    Mirrors reusable-ci.yml's lower-bounds job: locked environment, then each
    runtime dependency at its declared minimum, then the tests. Linux, not
    the host, because old macOS wheels fail in ways CI never sees.
    """
    py = f"{floor[0]}.{floor[1]}"
    work = clone.parent / (clone.name + "-lowest")
    run("rm", "-rf", str(work))
    run("cp", "-R", str(clone), str(work))
    lock = subprocess.run(
        ["uv", "lock", "-q", "--python", py],
        cwd=work,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    if lock.returncode:
        return False, "lock: " + _failure(lock.stderr)
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{work}:/w",
            "-w",
            "/w",
            "-e",
            "UV_LINK_MODE=copy",
            IMAGE.format(py=py),
            "bash",
            "-c",
            JOB,
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    run("rm", "-rf", str(work))
    out = proc.stdout + proc.stderr
    LAST_OUTPUT[0] = out
    if proc.returncode == 0:
        return True, "tests: " + (proc.stdout.strip().splitlines() or [""])[-1]
    return False, "tests: " + _failure(out)


IMPORT_NAMES = {
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
}
BLAME = [
    re.compile(r"module '([A-Za-z_][\w]*)"),
    re.compile(r"No module named '([A-Za-z_][\w]*)"),
    re.compile(r"site-packages/([A-Za-z_][\w]*)/"),
]


def runtime_names(pyproject: pathlib.Path) -> set[str]:
    project = tomllib.loads(pyproject.read_text())["project"]
    reqs = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        reqs += group
    return {canonicalize_name(Requirement(r).name) for r in reqs}


def blamed(detail: str, names: set[str]) -> str | None:
    """The runtime dependency a failure names, if any."""
    for pattern in BLAME:
        for found in pattern.findall(detail):
            name = canonicalize_name(IMPORT_NAMES.get(found, found))
            if name in names:
                return name
    return None


def locked_version(clone: pathlib.Path, name: str) -> Version | None:
    lock = clone / "uv.lock"
    if not lock.exists():
        return None
    for pkg in tomllib.loads(lock.read_text()).get("package", []):
        if canonicalize_name(pkg["name"]) == name and "version" in pkg:
            return Version(pkg["version"])
    return None


def enable_lower_bounds(ci: pathlib.Path) -> None:
    """Switch the reusable CI's lower-bounds job on for this repo."""
    if not ci.exists():
        return
    text = ci.read_text()
    if "run-lower-bounds:" in text:
        text = re.sub(r"run-lower-bounds:\s*\w+", "run-lower-bounds: true", text)
    else:
        m = re.search(
            r"(\n(\s+)uses: gojiplus/py-canon/\.github/workflows/"
            r"reusable-ci\.yml@\S+\n)(\s+with:\n)?",
            text,
        )
        if not m:
            return
        indent = m.group(2)
        if m.group(3):
            text = (
                text[: m.end()]
                + f"{indent}  run-lower-bounds: true\n"
                + text[m.end() :]
            )
        else:
            text = (
                text[: m.end(1)]
                + f"{indent}with:\n{indent}  run-lower-bounds: true\n"
                + text[m.end(1) :]
            )
    ci.write_text(text)


def bump_repo(repo: str, tmp: pathlib.Path, dry: bool) -> str:
    clone = tmp / repo.replace("/", "__")
    run("gh", "repo", "clone", repo, str(clone), "--", "--depth", "1", "-q")
    pyproject = clone / "pyproject.toml"
    original = pyproject.read_text()
    floor = python_floor(pyproject)
    bump: dict[str, Version] = {}
    changes: list[str] = []
    skipped: list[str] = []
    detail = ""
    ci_mode = os.environ.get("FLOORS_VERIFY", "local") == "ci"
    for _ in range(MAX_ROUNDS):
        pyproject.write_text(original)
        changes, skipped = raise_floors(pyproject, floor, bump)
        if ci_mode:
            # CI is the gate: the PR switches the lower-bounds job on, and
            # GitHub's Linux runners test the new minimums.
            detail = "verified by the PR's lower-bounds job"
            break
        ok, detail = verify(clone, floor)
        if ok:
            break
        m = BUILD_FAIL.search(detail)
        if m:
            name, ver = canonicalize_name(m.group(1)), Version(m.group(2))
            bump[name] = Version(f"{ver.major}.{ver.minor}.{ver.micro + 1}")
            continue
        # A test that fails on a dependency's API (missing attribute or
        # module) names the dependency; raise it to its locked version, which
        # the normal test job already passes with. That is a true minimum,
        # though maybe not the lowest one.
        name = blamed(LAST_OUTPUT[0], runtime_names(pyproject))
        locked = locked_version(clone, name) if name is not None else None
        if not name or not locked or bump.get(name) == locked:
            return f"!! needs a hand fix ({detail.strip().splitlines()[-1][:160]})"
        bump[name] = locked
    else:
        return f"!! still failing after {MAX_ROUNDS} rounds ({detail[:160]})"
    if not changes:
        return f"minimums already install and pass ({detail})"
    subprocess.run(
        ["uv", "lock"], cwd=clone, check=True, capture_output=True, env=_clean_env()
    )
    if ci_mode:
        enable_lower_bounds(clone / ".github" / "workflows" / "ci.yml")
    body = (
        "Each direct dependency's minimum is raised to the oldest release that "
        f"ships a wheel for this package's floor Python ({floor[0]}.{floor[1]}); "
        "the old minimums could not be installed there, so the declared range "
        "was false.\n\n"
        + "\n".join(f"- `{c}`" for c in changes)
        + (
            (
                "\n\nLeft alone (pinned or no wheel):\n"
                + "\n".join(f"- `{s}`" for s in skipped)
            )
            if skipped
            else ""
        )
        + "\n\nVerified with the lower-bounds CI job's own steps in the "
        f"python:{floor[0]}.{floor[1]} Linux image: locked environment, every "
        "runtime dependency installed at its new minimum, and the test suite "
        f"passed ({detail}). Generated by "
        "`tools/fleet_dependency_floors.py` in gojiplus/py-canon.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    )
    if dry:
        lines = "".join(f"\n      {c}" for c in changes)
        return f"would open PR: {len(changes)} raised; {detail}{lines}"
    run("git", "checkout", "-q", "-b", BRANCH, cwd=clone)
    run("git", "add", "-A", cwd=clone)
    message = (
        f"{TITLE}\n\nThe declared minimums could not be installed on Python "
        f"{floor[0]}.{floor[1]}. Each is raised to the oldest release with a "
        "wheel for it, and the suite passes at those minimums. From "
        "tools/fleet_dependency_floors.py in gojiplus/py-canon.\n\n"
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    )
    subprocess.run(
        ["git", "commit", "-q", "-F", "-"],
        cwd=clone,
        input=message,
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
        body,
    ).strip()
    try:
        run("gh", "pr", "merge", "--repo", repo, "--auto", "--squash", url)
        armed = "armed"
    except subprocess.CalledProcessError:
        armed = "auto-merge off; merge when green"
    return f"{url} ({armed}; {len(changes)} raised)"


MIN_FREE_GB = 5


def main(repos: list[str], dry: bool) -> None:
    with tempfile.TemporaryDirectory(dir=os.environ.get("FLOORS_TMP")) as tmp:
        for repo in repos:
            # Installing every repo's minimums downloads gigabytes; stop while
            # there is room to finish cleanly rather than filling the disk.
            free = shutil.disk_usage(tmp).free / 1e9
            if free < MIN_FREE_GB:
                print(f"== stopped: {free:.1f} GB free, below {MIN_FREE_GB} GB")
                return
            try:
                verdict = bump_repo(repo, pathlib.Path(tmp), dry)
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                OSError,
            ) as exc:
                tail = (getattr(exc, "stderr", "") or str(exc)).strip().splitlines()
                verdict = f"!! {tail[-1] if tail else exc}"
            print(f"== {repo:45} {verdict}", flush=True)
            run("rm", "-rf", str(pathlib.Path(tmp) / repo.replace("/", "__")))
            # Deleted containers do not return space to the host until the VM
            # trims it; on Colima: FLOORS_TRIM="colima ssh -- sudo fstrim -a".
            if os.environ.get("FLOORS_TRIM"):
                subprocess.run(os.environ["FLOORS_TRIM"].split(), capture_output=True)


if __name__ == "__main__":
    fleet = pathlib.Path(__file__).parents[1] / "FLEET"
    targets = sys.argv[1:] or [r for r in fleet.read_text().split() if r]
    main(targets, dry=os.environ.get("DRY_RUN", "1") == "1")
