"""Shared Sphinx configuration for the fleet.

A repo's ``docs/conf.py`` reduces to::

    from py_canon.sphinx import configure

    configure(globals())

Everything — theme, extensions, project metadata, version — is derived from
the repo's ``pyproject.toml`` and this module. Changing the fleet's docs
look-and-feel is an edit here, released as a py-canon tag; repos pick it up
on their next docs build after a lock refresh.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any


def _find_pyproject(start: Path) -> Path:
    """Walk upward from ``start`` to locate the repo's pyproject.toml."""
    for candidate in (start, *start.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            return pyproject
    raise FileNotFoundError(f"No pyproject.toml found above {start}")


def configure(
    namespace: dict[str, Any],
    *,
    conf_file: str | None = None,
    **overrides: Any,
) -> None:
    """Populate a Sphinx ``conf.py`` namespace with the fleet standard.

    Args:
        namespace: The ``globals()`` of the calling ``conf.py``.
        conf_file: Path to the calling ``conf.py``; defaults to the
            ``__file__`` in ``namespace``, falling back to the current
            working directory.
        **overrides: Any Sphinx setting to set or replace after the
            standard configuration is applied (e.g. ``nitpicky=True``,
            ``extensions=[...]`` additions must be done by mutating the
            namespace afterwards instead).
    """
    start = (
        Path(conf_file or namespace.get("__file__") or Path.cwd() / "conf.py")
        .resolve()
        .parent
    )
    pyproject = _find_pyproject(start)
    meta = tomllib.loads(pyproject.read_text())["project"]

    project = meta["name"]
    authors = ", ".join(a.get("name", "") for a in meta.get("authors", []))
    version = meta.get("version", "")
    if not version:
        # Dynamic versioning: read the installed package's metadata instead.
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as pkg_version

        try:
            version = pkg_version(project)
        except PackageNotFoundError:
            version = "dev"

    namespace.update(
        project=project,
        author=authors,
        copyright=f"{date.today().year}, {authors}",
        version=version,
        release=version,
        extensions=[
            "sphinx.ext.autodoc",
            "sphinx.ext.autosummary",
            "sphinx.ext.napoleon",
            "sphinx.ext.intersphinx",
            "sphinx.ext.viewcode",
            "sphinx.ext.doctest",
            "myst_parser",
            "sphinx_copybutton",
        ],
        # Theme
        html_theme="furo",
        html_title=project,
        # Autodoc / autosummary
        autosummary_generate=True,
        autodoc_typehints="description",
        autodoc_member_order="bysource",
        # Napoleon: google style only
        napoleon_google_docstring=True,
        napoleon_numpy_docstring=False,
        # MyST
        myst_enable_extensions=["colon_fence", "deflist"],
        source_suffix={".rst": "restructuredtext", ".md": "markdown"},
        # Intersphinx
        intersphinx_mapping={
            "python": ("https://docs.python.org/3", None),
        },
        exclude_patterns=["_build", "Thumbs.db", ".DS_Store"],
    )
    namespace.update(overrides)
