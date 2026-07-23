"""Tests for the shared Sphinx configuration."""

from pathlib import Path

from py_canon.sphinx import configure


def test_configure_populates_namespace(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "1.2.3"\n'
        'authors = [{name = "Ada Lovelace"}]\n'
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    namespace: dict = {"__file__": str(docs / "conf.py")}

    configure(namespace)

    assert namespace["project"] == "demo-pkg"
    assert namespace["version"] == "1.2.3"
    assert namespace["author"] == "Ada Lovelace"
    assert namespace["html_theme"] == "furo"
    assert "sphinx.ext.napoleon" in namespace["extensions"]
    assert namespace["napoleon_numpy_docstring"] is False


def test_configure_overrides_win(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1"\n')
    namespace: dict = {"__file__": str(tmp_path / "conf.py")}

    configure(namespace, html_title="Custom Title")

    assert namespace["html_title"] == "Custom Title"


def test_configure_builds_real_sphinx_project(tmp_path: Path) -> None:
    """The 2-line conf.py pattern must produce a buildable Sphinx project."""
    import subprocess
    import sys

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nauthors = [{name = "A"}]\n'
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "conf.py").write_text(
        "from py_canon.sphinx import configure\n\nconfigure(globals())\n"
    )
    (docs / "index.md").write_text("# demo\n\nHello.\n")

    cmd = [sys.executable, "-m", "sphinx", "-W", "-b", "html"]
    result = subprocess.run(  # noqa: S603 - fixed argv, test-controlled paths
        [*cmd, str(docs), str(tmp_path / "_site")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "_site" / "index.html").exists()
