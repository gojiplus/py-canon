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
    # Three: the lint and test jobs' syncs, plus the wheel job's install.
    assert workflow.count("test_group=(--group test)") == 3
    assert release_workflow.count("test_group=(--group test)") == 1
    assert "--all-groups" not in workflow
    assert "--all-groups" not in release_workflow


def test_repository_bound_projects_still_build_the_wheel() -> None:
    """Non-distributable projects skip installation, not package validation."""
    workflow = WORKFLOW.read_text()
    assert "run-wheel:" in workflow
    wheel_job = workflow.split("  wheel:\n", 1)[1].split("  workflow-security:\n", 1)[0]
    job_header = wheel_job.split("    steps:\n", 1)[0]
    assert "if: inputs.run-wheel" not in job_header
    assert "      - name: Build\n        run: uv build\n" in wheel_job
    assert (
        "      - name: Check metadata\n        run: uvx twine check dist/*\n"
        in wheel_job
    )
    assert (
        "      - name: Install wheel in clean env and test\n"
        "        if: inputs.run-wheel\n"
    ) in wheel_job


def test_slow_projects_can_extend_test_and_wheel_timeouts() -> None:
    """A caller should not have to copy the workflow to fit a valid suite."""
    workflow = WORKFLOW.read_text()
    assert "      test-timeout-minutes:\n" in workflow
    assert "        default: 30\n" in workflow
    assert "    timeout-minutes: ${{ inputs.test-timeout-minutes }}\n" in workflow
    assert "      wheel-timeout-minutes:\n" in workflow
    assert "        default: 20\n" in workflow
    assert "    timeout-minutes: ${{ inputs.wheel-timeout-minutes }}\n" in workflow


def test_wheel_test_does_not_replace_the_built_wheel() -> None:
    """Resolve the wheel and test dependencies together without source install."""
    workflow = WORKFLOW.read_text()
    assert 'uv pip install dist/*.whl "${test_group[@]}"\n' in workflow
    assert "uv pip install --group test ." not in workflow


def test_wheel_job_tolerates_a_repo_with_no_test_group() -> None:
    """The install assumed the group exists; the syncs above it never did.

    `uv pip install dist/*.whl --group test` exits 2 with "The dependency group
    'test' was not found" on a repo that has not split one out, turning an
    unrelated packaging job red. Skipping the install alone is not enough: the
    step goes on to run pytest inside that clean environment, so without a
    runner the clear error becomes an ImportError.
    """
    workflow = WORKFLOW.read_text()
    wheel_job = workflow.split("  wheel:\n", 1)[1].split("  workflow-security:\n", 1)[0]
    assert "test_group=(--group test)" in wheel_job
    assert 'uv pip install dist/*.whl "${test_group[@]}"' in wheel_job
    assert (
        "          if [ ${#test_group[@]} -eq 0 ] "
        '&& [ -d "$GITHUB_WORKSPACE/tests" ]; then\n'
        "            uv pip install pytest\n" in wheel_job
    )


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


def test_pydoclint_derives_the_package_from_module_name() -> None:
    """A repo may declare an import name unlike its distribution name.

    finite-sample/optimal-classification-cutoffs ships `optimal_cutoffs/`.
    Guessing from the distribution name looked for
    `optimal_classification_cutoffs/`, found nothing, and hard-errored — so the
    adoption had to pass `run-pydoclint: false` on a package that is clean.
    """
    workflow = WORKFLOW.read_text()
    assert 'backend.get("module-name", "")' in workflow
    assert 'backend.get("module-root", "")' in workflow
    # The distribution-name guess stays, for the repos that have no declaration.
    assert 'name.replace("-", "_")' in workflow
    # And a repo with neither is still an error rather than a silent skip.
    assert "pydoclint found no package to check" in workflow


AUTO_MERGE = (
    Path(__file__).parents[1] / ".github/workflows/reusable-dependabot-auto-merge.yml"
)


def test_auto_merge_dispatches_ci_on_the_branch_it_merged_into() -> None:
    """A GITHUB_TOKEN push does not trigger workflows.

    So an auto-merged PR leaves the default branch at a commit no CI run ever
    covered — on gojiplus/rowvoi, HEAD had zero runs for its SHA while the
    passing run belonged to a since-deleted PR branch. fleet_triage reads
    default-branch runs, so it reported on an older commit than HEAD.
    """
    workflow = AUTO_MERGE.read_text()
    assert "gh workflow run ci.yml --ref" in workflow
    # Only when something actually landed: a declined merge must not dispatch.
    assert "steps.arm.outputs.merged != '0'" in workflow
    # And a repo whose ci.yml predates the trigger warns rather than failing.
    assert "could not dispatch CI" in workflow


def test_ci_accepts_a_dispatch() -> None:
    """The dispatch above needs the shim to declare the trigger."""
    for shim in (
        Path(__file__).parents[1] / ".github/workflows/ci.yml",
        Path(__file__).parents[1] / "template/.github/workflows/ci.yml.jinja",
    ):
        assert "workflow_dispatch:" in shim.read_text(), shim


def _lock_regression_script() -> str:
    """The Python the lock-regression step feeds to `uv run`, verbatim."""
    import yaml

    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(
        step
        for step in workflow["jobs"]["lock-regression"]["steps"]
        if step.get("name", "").startswith("Compare locked versions")
    )
    return step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def _lock(path: Path, packages: dict[str, list[str]]) -> Path:
    entries = "".join(
        f'[[package]]\nname = "{name}"\nversion = "{version}"\n\n'
        for name, versions in packages.items()
        for version in versions
    )
    path.write_text(f"version = 1\nrevision = 3\n\n{entries}")
    return path


def _compare(
    tmp_path: Path, base: dict, head: dict
) -> subprocess.CompletedProcess[str]:
    base_lock = _lock(tmp_path / "base.lock", base)
    head_lock = _lock(tmp_path / "head.lock", head)
    return subprocess.run(  # noqa: S603 - every argument is test-generated.
        [
            sys.executable,
            "-c",
            _lock_regression_script(),
            str(base_lock),
            str(head_lock),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_lock_regression_fails_on_any_downgrade(tmp_path: Path) -> None:
    """A stale lock resurrects every Dependabot bump it undoes; name each one."""
    result = _compare(
        tmp_path,
        base={
            "pandas": ["3.0.5"],
            "pandas-stubs": ["3.0.3.260530"],
            "ruff": ["0.16.4"],
        },
        head={
            "pandas": ["2.3.3"],
            "pandas-stubs": ["2.3.2.250926"],
            "ruff": ["0.16.5"],
        },
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::uv.lock pins pandas 2.3.3, below 3.0.5" in result.stdout
    assert "pandas-stubs 2.3.2.250926, below 3.0.3.260530" in result.stdout
    assert "ruff" not in result.stdout


def test_lock_regression_passes_on_upgrade_add_and_remove(tmp_path: Path) -> None:
    """Only a version going down is a regression."""
    result = _compare(
        tmp_path,
        base={"pandas": ["2.3.3"], "pytz": ["2025.2"]},
        head={"pandas": ["3.0.5"], "formulaic": ["1.2.2"]},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no package pinned below the base branch (2 compared)" in result.stdout


def test_lock_regression_compares_versions_not_strings(tmp_path: Path) -> None:
    """pyright 1.1.411 is newer than 1.1.39 even though it sorts lower as text."""
    result = _compare(
        tmp_path, base={"pyright": ["1.1.39"]}, head={"pyright": ["1.1.411"]}
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lock_regression_reduces_marker_forks_to_the_highest_version(
    tmp_path: Path,
) -> None:
    """A lock can pin one package twice under different markers."""
    result = _compare(
        tmp_path,
        base={"numpy": ["1.26.4", "2.4.6"]},
        head={"numpy": ["2.4.6"]},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lock_regression_tolerates_a_branch_that_introduces_the_lock(
    tmp_path: Path,
) -> None:
    """The fetch step writes an empty base file when the base has no uv.lock."""
    empty = tmp_path / "base.lock"
    empty.write_text("")
    head = _lock(tmp_path / "head.lock", {"pandas": ["3.0.5"]})
    result = subprocess.run(  # noqa: S603 - every argument is test-generated.
        [sys.executable, "-c", _lock_regression_script(), str(empty), str(head)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lock_regression_is_gated_and_overridable() -> None:
    """The gate must see it, and a deliberate downgrade must have a way through."""
    import yaml

    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "lock-regression" in workflow["jobs"]["gate"]["needs"]
    condition = workflow["jobs"]["lock-regression"]["if"]
    assert "github.event_name == 'pull_request'" in condition
    assert "'lock-downgrade-ok'" in condition
