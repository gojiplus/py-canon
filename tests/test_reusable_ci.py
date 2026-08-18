"""Regression tests for the shared CI workflow."""

import subprocess
import sys
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/reusable-ci.yml"
RELEASE_WORKFLOW = Path(__file__).parents[1] / ".github/workflows/reusable-release.yml"


def test_ci_syncs_only_the_canonical_dev_group() -> None:
    """Operational dependency groups must not be installed in ordinary CI."""
    workflow = WORKFLOW.read_text()
    release_workflow = RELEASE_WORKFLOW.read_text()
    frozen = 'uv sync --no-default-groups --frozen --group dev "${test_group[@]}"'
    unlocked = 'uv sync --no-default-groups --group dev "${test_group[@]}"'
    assert frozen in workflow
    assert unlocked in workflow
    assert frozen in release_workflow
    assert unlocked in release_workflow
    assert workflow.count("test_group=(--group test)") == 2
    assert release_workflow.count("test_group=(--group test)") == 1
    assert "--all-groups" not in workflow
    assert "--all-groups" not in release_workflow


def test_repository_bound_projects_can_skip_wheel_validation() -> None:
    """A declared non-distributable project can disable only the wheel job."""
    workflow = WORKFLOW.read_text()
    assert "run-wheel:" in workflow
    assert "if: inputs.run-wheel" in workflow


def test_wheel_test_does_not_replace_the_built_wheel() -> None:
    """Resolve the wheel and test dependencies together without source install."""
    workflow = WORKFLOW.read_text()
    assert "uv pip install dist/*.whl --group test\n" in workflow
    assert "uv pip install --group test ." not in workflow


def test_wheel_import_runs_after_conftest_and_checks_its_location() -> None:
    """Check the import after conftest initialization but before collection."""
    workflow = WORKFLOW.read_text()
    assert "def pytest_configure(config):" in workflow
    assert "plugins=[WheelImport()]" in workflow
    assert "imported from checkout instead of the wheel" in workflow
    assert '"$import_name" "$GITHUB_WORKSPACE" "$GITHUB_WORKSPACE/tests/"' in workflow
    assert 'importlib.import_module(sys.argv[1])\' "$import_name"' in workflow


def _run_wheel_check(
    wheel_site: Path, checkout: Path
) -> subprocess.CompletedProcess[str]:
    code = """
import importlib
import pathlib
import pytest
import sys

class WheelImport:
    @staticmethod
    def pytest_configure(config):
        module = importlib.import_module(sys.argv[1])
        checkout = pathlib.Path(sys.argv[2]).resolve()
        locations = [pathlib.Path(module.__file__).resolve()]
        if any(
            location == checkout or checkout in location.parents
            for location in locations
        ):
            raise pytest.UsageError("package imported from checkout")

raise SystemExit(pytest.main([sys.argv[3], "-q"], plugins=[WheelImport()]))
"""
    return subprocess.run(  # noqa: S603 - every argument is test-generated.
        [
            sys.executable,
            "-P",
            "-c",
            code,
            "example_package",
            str(checkout),
            str(checkout / "tests"),
        ],
        cwd=checkout,
        env={"PYTHONPATH": str(wheel_site)},
        check=False,
        capture_output=True,
        text=True,
    )


def test_wheel_check_preserves_conftest_initialization(tmp_path: Path) -> None:
    """Conftest settings must take effect before the wheel is imported."""
    wheel_site = tmp_path / "wheel-site"
    checkout = tmp_path / "checkout"
    tests = checkout / "tests"
    package = wheel_site / "example_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        'import os\nMARKER = os.getenv("SETTING", "unset")\n'
    )
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(
        'import os\nos.environ["SETTING"] = "configured"\n'
    )
    (tests / "test_marker.py").write_text(
        "import example_package\n\n"
        "def test_marker():\n"
        '    assert example_package.MARKER == "configured"\n'
    )
    result = _run_wheel_check(wheel_site, checkout)
    assert result.returncode == 0, result.stdout + result.stderr


def test_wheel_check_rejects_source_injecting_conftest(tmp_path: Path) -> None:
    """A conftest cannot substitute checkout source for the built wheel."""
    wheel_site = tmp_path / "wheel-site"
    checkout = tmp_path / "checkout"
    tests = checkout / "tests"
    for root, marker in ((wheel_site, "wheel"), (checkout, "source")):
        package = root / "example_package"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f'MARKER = "{marker}"\n')
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(checkout)!r})\nimport example_package\n"
    )
    (tests / "test_marker.py").write_text("def test_marker(): pass\n")
    result = _run_wheel_check(wheel_site, checkout)
    assert result.returncode != 0
    assert "imported from checkout" in result.stdout + result.stderr
