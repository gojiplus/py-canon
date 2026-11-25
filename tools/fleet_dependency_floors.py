#!/usr/bin/env python3
"""Raise each runtime dependency's declared minimum to one that installs.

Most fleet packages declared minimums nobody had installed: fewlab said
``pandas>=1.5`` and stable-cart ``numpy>=1.23.0``, and neither installs on
Python 3.13, so the claim was false for every user. The reusable CI's
``lower-bounds`` job tests the claim; this tool makes it true first.

Per repo: shallow clone; for every requirement in ``[project] dependencies``
and ``optional-dependencies``, find the oldest release that satisfies the
existing specifier, supports the package's floor Python, and ships a wheel
for it on glibc Linux x86-64 (``py3-none-any``, ``py3X-none-any`` with X at
or below the floor, ``abi3`` at or below the floor, or ``cp<floor>``); raise
the lower bound to it, or add one where none exists. Then switch the
lower-bounds job on in the repo's ci.yml and open a PR: CI is the gate. A red
PR means a minimum that installs but does not work (an API the package needs
arrived later) and is fixed by hand.

Hand-rolled because nothing maintained does this: uv's ``lowest-direct``
resolution finds failing minimums but does not choose new ones.
Requirements pinned with ``==`` or ``~=`` are left alone and reported.

Usage (from a py-canon checkout):
    DRY_RUN=0 uv run --group dev tools/fleet_dependency_floors.py [owner/repo ...]
"""

import json
import os
import pathlib
import re
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
REUSABLE = "gojiplus/py-canon/.github/workflows/reusable-ci.yml@"


def run(*args: str, cwd: pathlib.Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return subprocess.run(
        list(args), cwd=cwd, check=True, capture_output=True, text=True, env=env
    ).stdout


@cache
def releases(name: str) -> dict:
    url = f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)["releases"]


def wheel_fits(filename: str, floor: tuple[int, int]) -> bool:
    """Whether a wheel installs on CPython ``floor`` on glibc Linux x86-64."""
    try:
        _, _, _, tags = parse_wheel_filename(filename)
    except ValueError:
        return False
    for tag in tags:
        plat = tag.platform
        if plat != "any" and not (
            (plat.startswith("manylinux") or plat == "linux_x86_64")
            and plat.endswith("x86_64")
        ):
            continue
        interp, abi = tag.interpreter, tag.abi
        if interp.startswith("py3") and abi == "none":
            minor = interp[3:]
            if not minor or (minor.isdigit() and int(minor) <= floor[1]):
                return True
        if interp == f"cp3{floor[1]}" and abi in (interp, "abi3", "none"):
            return True
        if (
            abi == "abi3"
            and interp.startswith("cp3")
            and interp[3:].isdigit()
            and int(interp[3:]) <= floor[1]
        ):
            return True
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
        if v.is_prerelease or v not in req.specifier:
            continue
        live = [f for f in files if not f.get("yanked")]
        rp = next(
            (f.get("requires_python") for f in live if f.get("requires_python")),
            None,
        )
        if rp and pyfloor not in SpecifierSet(rp):
            continue
        wheels = [f["filename"] for f in live if f["packagetype"] == "bdist_wheel"]
        if any(wheel_fits(w, floor) for w in wheels):
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
    """The runtime requirement arrays: what `pip install pkg[extra]` promises."""
    project = doc.get("project", {})
    if "dependencies" in project:
        yield project["dependencies"]
    yield from project.get("optional-dependencies", {}).values()


def raise_floors(
    pyproject: pathlib.Path, floor: tuple[int, int]
) -> tuple[list[str], list[str]]:
    """Rewrite requirements in place. Returns (changes, skipped)."""
    doc = tomlkit.parse(pyproject.read_text())
    changes, skipped = [], []
    for arr in requirement_arrays(doc):
        for i, item in enumerate(arr):
            if not isinstance(item, str):
                continue
            req = Requirement(item)
            if req.url or any(s.operator in ("==", "~=", "===") for s in req.specifier):
                skipped.append(f"{item} (pinned)")
                continue
            cands = candidates(req, floor)
            if not cands:
                skipped.append(f"{item} (no wheel for {floor[0]}.{floor[1]})")
                continue
            have = lower_bound(req)
            if have is not None and cands[0] <= have:
                continue
            arr[i] = with_floor(req, cands[0])
            changes.append(f"{item} -> {arr[i]}")
    # The wheel check sees each dependency alone. A minimum can still be
    # unsatisfiable next to the others (fewlab's numpy 2.1 under a simcheck
    # that needs >=2.4.6), so resolve the whole runtime set at its lowest and
    # raise any minimum the resolver had to lift.
    resolved = lowest_consistent(
        [i for arr in requirement_arrays(doc) for i in arr if isinstance(i, str)],
        floor,
    )
    for arr in requirement_arrays(doc):
        for i, item in enumerate(arr):
            if not isinstance(item, str):
                continue
            req = Requirement(item)
            got = resolved.get(canonicalize_name(req.name))
            have = lower_bound(req)
            if got is None or req.url or (have is not None and got <= have):
                continue
            if any(s.operator in ("==", "~=", "===") for s in req.specifier):
                continue
            arr[i] = with_floor(req, got)
            changes.append(f"{item} -> {arr[i]} (needed by another dependency)")
    pyproject.write_text(tomlkit.dumps(doc))
    return changes, skipped


def lowest_consistent(reqs: list[str], floor: tuple[int, int]) -> dict[str, Version]:
    """Resolve requirements at their lowest, as Linux wheels for the floor.

    Returns ``{name: version}``, or ``{}`` when they cannot be resolved,
    which leaves the floors as the wheel pass set them for CI to judge.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".in", delete=False) as fh:
        fh.write("\n".join(reqs) + "\n")
        path = fh.name
    try:
        out = run(
            "uv",
            "pip",
            "compile",
            path,
            "--resolution",
            "lowest-direct",
            "--python-version",
            f"{floor[0]}.{floor[1]}",
            "--python-platform",
            "x86_64-manylinux_2_28",
            "--only-binary",
            ":all:",
            "--no-header",
            "--no-annotate",
            "-q",
        )
    except subprocess.CalledProcessError:
        return {}
    finally:
        pathlib.Path(path).unlink()
    pins = {}
    for line in out.splitlines():
        name, sep, ver = line.partition("==")
        if sep and not line.startswith(("#", "-")):
            try:
                pins[canonicalize_name(name.split("[")[0].strip())] = Version(
                    ver.split(";")[0].strip()
                )
            except InvalidVersion:
                continue
    return pins


def python_floor(pyproject: pathlib.Path) -> tuple[int, int]:
    """The lowest 3.x minor any patch release of which requires-python allows."""
    spec = SpecifierSet(
        tomllib.loads(pyproject.read_text())["project"].get("requires-python", "")
    )
    for minor in range(8, 30):
        if any(Version(f"3.{minor}.{patch}") in spec for patch in range(40)):
            return (3, minor)
    return (3, 13)


def enable_lower_bounds(ci: pathlib.Path) -> bool:
    """Switch the reusable CI's lower-bounds job on. Returns whether it did.

    Line-based rather than a YAML round-trip, so comments and layout survive.
    Finds the job that ``uses:`` the reusable workflow (any ref form, SHA
    with a trailing comment included), then sets the input inside that job's
    own ``with:`` block, adding the block if the job has none.
    """
    if not ci.exists():
        return False
    lines = ci.read_text().splitlines(keepends=True)
    i = next(
        (
            n
            for n, line in enumerate(lines)
            if REUSABLE in line and line.lstrip().startswith("uses:")
        ),
        None,
    )
    if i is None:
        return False
    # Only a caller that follows the moving v1 tag gets the input; a SHA or an
    # older tag predates it, and the job would reject the unknown input.
    ref = lines[i].split("@", 1)[1].split()[0]
    if ref != "v1":
        return False
    indent = len(lines[i]) - len(lines[i].lstrip())
    end = len(lines)
    for j in range(i + 1, len(lines)):
        text = lines[j]
        if text.strip() and len(text) - len(text.lstrip()) < indent:
            end = j
            break
    pad = " " * indent
    for j in range(i - 1, -1, -1):
        text = lines[j]
        if text.strip() and len(text) - len(text.lstrip()) < indent:
            start = j + 1
            break
    else:
        start = 0
    for j in range(start, end):
        if lines[j].startswith(f"{pad}with:") and lines[j].split(":", 1)[1].strip():
            return False  # an inline mapping, `with: {...}`; edit by hand
        if lines[j].startswith(f"{pad}with:"):
            for k in range(j + 1, end):
                if re.match(rf"{pad}  run-lower-bounds:", lines[k]):
                    lines[k] = f"{pad}  run-lower-bounds: true\n"
                    break
            else:
                lines.insert(j + 1, f"{pad}  run-lower-bounds: true\n")
            break
    else:
        lines[end:end] = [f"{pad}with:\n", f"{pad}  run-lower-bounds: true\n"]
    ci.write_text("".join(lines))
    return True


def bump_repo(repo: str, tmp: pathlib.Path, dry: bool) -> str:
    clone = tmp / repo.replace("/", "__")
    run("gh", "repo", "clone", repo, str(clone), "--", "--depth", "1", "-q")
    pyproject = clone / "pyproject.toml"
    floor = python_floor(pyproject)
    changes, skipped = raise_floors(pyproject, floor)
    if not changes:
        return "no minimum needs raising"
    if not enable_lower_bounds(clone / ".github" / "workflows" / "ci.yml"):
        return (
            f"!! {len(changes)} to raise, but ci.yml does not call the reusable CI "
            "at @v1 with a block `with:`; edit by hand"
        )
    lines = "".join(f"\n      {c}" for c in changes)
    if dry:
        return f"would open PR: {len(changes)} raised{lines}"
    run("uv", "lock", cwd=clone)
    left = "".join(f"\n- `{s}`" for s in skipped)
    body = (
        "Each runtime dependency's minimum is raised to the oldest release that "
        f"ships a wheel for this package's floor Python ({floor[0]}.{floor[1]}); "
        "the old minimums could not be installed there, so the declared range "
        "was false.\n\n"
        + "\n".join(f"- `{c}`" for c in changes)
        + (f"\n\nLeft alone:{left}" if skipped else "")
        + "\n\nThis PR also switches on the reusable CI's `lower-bounds` job, "
        "which installs every runtime dependency at its declared minimum and "
        "runs the tests. If it fails, a minimum installs but does not work and "
        "needs raising by hand. Generated by `tools/fleet_dependency_floors.py` "
        "in gojiplus/py-canon.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    )
    existing = json.loads(
        run("gh", "pr", "list", "--repo", repo, "--head", BRANCH, "--json", "url")
    )
    run("git", "checkout", "-q", "-B", BRANCH, cwd=clone)
    run("git", "add", "-A", cwd=clone)
    message = (
        f"{TITLE}\n\nThe declared minimums could not be installed on Python "
        f"{floor[0]}.{floor[1]}; each is raised to the oldest release with a wheel "
        "for it, and the lower-bounds job is switched on to test them. From "
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
    # Rerunning replaces the branch: the PR always shows this tool's current
    # answer against the current default branch, not a stack of patches.
    run("git", "push", "-q", "-f", "-u", "origin", BRANCH, cwd=clone)
    if existing:
        return f"{existing[0]['url']} (updated; {len(changes)} raised)"
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


def main(repos: list[str], dry: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for repo in repos:
            try:
                verdict = bump_repo(repo, pathlib.Path(tmp), dry)
            except (subprocess.CalledProcessError, OSError) as exc:
                tail = (getattr(exc, "stderr", "") or str(exc)).strip().splitlines()
                verdict = f"!! {tail[-1] if tail else exc}"
            print(f"== {repo:45} {verdict}", flush=True)


if __name__ == "__main__":
    fleet = pathlib.Path(__file__).parents[1] / "FLEET"
    targets = sys.argv[1:] or [r for r in fleet.read_text().split() if r]
    main(targets, dry=os.environ.get("DRY_RUN", "1") == "1")
