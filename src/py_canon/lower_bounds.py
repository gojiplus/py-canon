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

Usage: ``python -m py_canon.lower_bounds [pyproject.toml]`` prints one pin
per line.
"""

import sys
import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


def _minimum(req: Requirement) -> Version | None:
    """The exact version the requirement's lower bound names, if any.

    Args:
        req: A parsed requirement.

    Returns:
        The minimum, or None when no installable exact minimum can be read.
    """
    lows: list[Version] = []
    for spec in req.specifier:
        if spec.operator in (">=", "~=") or (
            spec.operator == "==" and "*" not in spec.version
        ):
            try:
                lows.append(Version(spec.version))
            except InvalidVersion:
                return None
    if not lows:
        return None
    low = max(lows)
    return low if req.specifier.contains(low, prereleases=True) else None


def minimums(pyproject: dict) -> list[str]:
    """Exact pins for the runtime dependencies' declared minimums.

    Args:
        pyproject: The parsed ``pyproject.toml``.

    Returns:
        ``name[extras]==version`` lines, one per distribution, sorted.
    """
    project = pyproject.get("project", {})
    groups: list[tuple[str, list[str]]] = [("", project.get("dependencies", []))]
    groups += list(project.get("optional-dependencies", {}).items())
    env: dict[str, str] = {k: str(v) for k, v in default_environment().items()}
    best: dict[str, tuple[Version, set[str], str]] = {}
    for extra, reqs in groups:
        for text in reqs:
            req = Requirement(text)
            if req.url:
                continue
            if req.marker and not req.marker.evaluate({**env, "extra": extra}):
                continue
            low = _minimum(req)
            if low is None:
                continue
            key = canonicalize_name(req.name)
            have = best.get(key)
            extras = set(req.extras) | (have[1] if have else set())
            if have is None or low > have[0]:
                best[key] = (low, extras, req.name)
            else:
                best[key] = (have[0], extras, have[2])
    lines = []
    for low, extras, name in best.values():
        tag = f"[{','.join(sorted(extras))}]" if extras else ""
        lines.append(f"{name}{tag}=={low}")
    return sorted(lines, key=str.lower)


def main(argv: list[str] | None = None) -> None:
    """Print the pins for a ``pyproject.toml``.

    Args:
        argv: ``[path]``; defaults to ``pyproject.toml`` in the working
            directory.
    """
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0] if args else "pyproject.toml")
    for line in minimums(tomllib.loads(path.read_text())):
        sys.stdout.write(line + "\n")


if __name__ == "__main__":
    main()
