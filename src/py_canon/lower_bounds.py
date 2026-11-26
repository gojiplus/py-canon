"""Pin each runtime dependency of a package to its declared minimum.

The reusable CI's ``lower-bounds`` job installs these pins in a fresh
environment and runs the tests, which is the only check that a
package's declared minimums are true. ``tools/fleet_dependency_floors.py``
uses the same function, so the tool proposes what the job will test.

Only ``[project] dependencies`` and ``optional-dependencies`` are pinned:
they are what ``pip install pkg[extra]`` promises. A dependency named more
than once (base and an extra, or two extras) is pinned once, at the highest
of its lower bounds, so the pins never conflict. A requirement is skipped
when no exact pin can be read from it: no lower bound, a strict ``>``, a
wildcard ``==4.*``, a URL, or a minimum its own specifier excludes
(``>=1,!=1``). Markers are evaluated in the current environment, with
``extra`` set to each optional group's name.

Usage: ``python -m py_canon.lower_bounds [--python-version X.Y.Z]
[pyproject.toml]`` prints one pin per line.
"""

import sys
import tomllib
from pathlib import Path

from packaging.dependency_groups import resolve_dependency_groups
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


def _minimum(spec: SpecifierSet) -> Version | None:
    """The exact version the combined lower bounds name, if it is allowed.

    Args:
        spec: Every specifier on one distribution, combined.

    Returns:
        The highest lower bound, or None when none can be read or the
        combined specifier excludes it.
    """
    lows: list[Version] = []
    for item in spec:
        if item.operator in (">=", "~=") or (
            item.operator == "==" and "*" not in item.version
        ):
            try:
                lows.append(Version(item.version))
            except InvalidVersion:
                return None
    if not lows:
        return None
    low = max(lows)
    return low if spec.contains(low, prereleases=True) else None


def minimums(pyproject: dict, python_version: str | None = None) -> list[str]:
    """Exact pins for the runtime dependencies' declared minimums.

    Every requirement on a distribution counts, including ones with no lower
    bound: base ``idna>=2.4`` with an extra's ``idna!=2.4`` has no exact
    minimum, and an extra's ``g[http]`` adds ``http`` to base ``g>=2``.

    Args:
        pyproject: The parsed ``pyproject.toml``.
        python_version: ``X.Y`` of the interpreter the pins are for, so
            ``python_version`` markers are evaluated there and not where
            this helper runs.

    Returns:
        ``name[extras]==version`` lines, one per distribution, sorted.
    """
    project = pyproject.get("project", {})
    groups: list[tuple[str, list[str]]] = [("", project.get("dependencies", []))]
    groups += list(project.get("optional-dependencies", {}).items())
    env: dict[str, str] = {k: str(v) for k, v in default_environment().items()}
    if python_version:
        env["python_version"] = ".".join(python_version.split(".")[:2])
        env["python_full_version"] = python_version
    found: dict[str, tuple[str, set[str], SpecifierSet]] = {}
    for extra, reqs in groups:
        for text in reqs:
            req = Requirement(text)
            if req.url:
                continue
            if req.marker and not req.marker.evaluate({**env, "extra": extra}):
                continue
            key = canonicalize_name(req.name)
            name, extras, spec = found.get(key, (req.name, set(), SpecifierSet()))
            found[key] = (name, extras | set(req.extras), spec & req.specifier)
    lines = []
    for name, extras, spec in found.values():
        low = _minimum(spec)
        if low is None:
            continue
        tag = f"[{','.join(sorted(extras))}]" if extras else ""
        lines.append(f"{name}{tag}=={low}")
    return sorted(lines, key=str.lower)


def group_requirements(
    pyproject: dict, names: tuple[str, ...] = ("dev", "test")
) -> list[str]:
    """The requirement strings of dependency groups, with include-group expanded.

    Args:
        pyproject: The parsed ``pyproject.toml``.
        names: Groups to collect; ones the project does not define are skipped.

    Returns:
        Requirement strings in declaration order, duplicates removed.
    """
    groups = pyproject.get("dependency-groups", {})
    available = {canonicalize_name(name) for name in groups}
    selected = [name for name in names if canonicalize_name(name) in available]
    return list(dict.fromkeys(resolve_dependency_groups(groups, *selected)))


def environment(pyproject: dict, python_version: str | None = None) -> list[str]:
    """A requirements file for a fresh environment at the runtime minimums.

    The runtime dependencies are pinned at their minimums, the dev and test
    groups are listed unpinned so the resolver picks tool versions that work
    with those minimums, and the package itself is installed editable with
    every extra.

    Args:
        pyproject: The parsed ``pyproject.toml``.
        python_version: Passed to :func:`minimums`.

    Returns:
        Requirement lines.
    """
    extras = sorted(pyproject.get("project", {}).get("optional-dependencies", {}))
    tag = f"[{','.join(extras)}]" if extras else ""
    tools = group_requirements(pyproject)
    if not any(
        canonicalize_name(req.name) == "pytest" and req.marker is None
        for req in map(Requirement, tools)
    ):
        tools.append("pytest")
    return [*minimums(pyproject, python_version), *tools, f"-e .{tag}"]


def main(argv: list[str] | None = None) -> None:
    """Print the pins for a ``pyproject.toml``.

    Args:
        argv: ``[--python-version X.Y.Z] [--environment] [path]``. With
            ``--environment``, print :func:`environment` instead of the pins.
            The path defaults to ``pyproject.toml``.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    version = None
    full = False
    while args[:1] and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--python-version":
            version = args.pop(0)
        elif flag == "--environment":
            full = True
    path = Path(args[0] if args else "pyproject.toml")
    data = tomllib.loads(path.read_text())
    lines = environment(data, version) if full else minimums(data, version)
    for line in lines:
        sys.stdout.write(line + "\n")


if __name__ == "__main__":
    main()
