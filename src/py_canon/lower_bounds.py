"""Pin each runtime dependency of a package to its declared minimum.

The reusable CI's ``lower-bounds`` job installs these pins on top of the
locked environment and runs the tests, which is the only check that a
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


def main(argv: list[str] | None = None) -> None:
    """Print the pins for a ``pyproject.toml``.

    Args:
        argv: ``[--python-version X.Y.Z] [path]``; the path defaults to
            ``pyproject.toml`` in the working directory.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    version = None
    if args[:1] == ["--python-version"]:
        version = args[1]
        args = args[2:]
    path = Path(args[0] if args else "pyproject.toml")
    for line in minimums(tomllib.loads(path.read_text()), version):
        sys.stdout.write(line + "\n")


if __name__ == "__main__":
    main()
