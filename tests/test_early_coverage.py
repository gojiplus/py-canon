"""Run the shared test step against an installed pytest entry point."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    ("early", "floor", "fails", "expected"),
    [
        (True, 40, False, 0),
        (True, 100, False, 2),
        (True, 40, True, 1),
        (False, 40, False, 1),
        (False, 0, False, 0),
    ],
)
def test_coverage_step(tmp_path, early, floor, fails, expected):
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/reusable-ci.yml").read_text()
    )
    step = next(
        s for s in workflow["jobs"]["test"]["steps"] if s.get("name") == "Run tests"
    )
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 42\n")
    (package / "unused.py").write_text("VALUE = 0\n")
    (tmp_path / "plugin.py").write_text("import sample\n")
    metadata = tmp_path / "sample-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: sample\nVersion: 1.0\n")
    (metadata / "entry_points.txt").write_text("[pytest11]\nsample = plugin\n")
    (tmp_path / "test_sample.py").write_text(
        "import sample\ndef test_value():\n"
        f"    assert sample.VALUE == {0 if fails else 42}\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["sample"]\n'
    )
    # Execute the workflow's commands with this test environment's interpreter.
    uv = tmp_path / "uv"
    uv.write_text(f'#!/bin/sh\nshift\nexec "{sys.executable}" -m "$@"\n')
    uv.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(tmp_path),
        "COVERAGE_FLOOR": str(floor),
        "COVERAGE_BEFORE_PYTEST": str(early).lower(),
    }
    env.pop("COVERAGE_RUN", None)
    env.pop("COVERAGE_PROCESS_START", None)
    result = subprocess.run(  # noqa: S603
        [shutil.which("bash"), "-e", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if early and not fails:
        report = subprocess.run(
            [sys.executable, "-m", "coverage", "json"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert report.returncode == 0
        files = json.loads((tmp_path / "coverage.json").read_text())["files"]
        assert files["sample/__init__.py"]["executed_lines"] == [1]
        assert files["sample/unused.py"]["missing_lines"] == [1]
        assert not any("test_sample" in name for name in files)
