"""Exercise the recovery workflow's actual decision program."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).parents[1] / ".github/workflows/reusable-dependabot-auto-merge.yml"
)


def _script():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in workflow["jobs"]["sweep"]["steps"] if s.get("id") == "plan")
    return step["run"].split("<<'PY' > actions.txt\n", 1)[1].split("\nPY", 1)[0]


@pytest.mark.parametrize(
    ("armed", "merge", "checks", "required", "action", "reason"),
    [
        (
            True,
            "BLOCKED",
            [("lint", "FAILURE")],
            ["lint"],
            "none",
            "checks failing: lint",
        ),
        (
            True,
            "DIRTY",
            [("lint", "SUCCESS")],
            ["lint"],
            "none",
            "not mergeable (DIRTY)",
        ),
        (
            True,
            "BLOCKED",
            [("lint", "SUCCESS")],
            ["gate"],
            "none",
            "required checks missing: gate",
        ),
        (False, "CLEAN", [], [], "none", "no checks reported"),
        (False, "CLEAN", [("lint", "PENDING")], [], "none", "checks running: lint"),
        (
            False,
            "BLOCKED",
            [("lint", "PENDING")],
            ["lint"],
            "arm",
            "checks running: lint",
        ),
        (
            True,
            "BLOCKED",
            [("lint", "PENDING")],
            ["lint"],
            "none",
            "checks running: lint",
        ),
        (
            False,
            "CLEAN",
            [("lint", "SUCCESS")],
            ["lint"],
            "merge",
            "checks terminal and green",
        ),
        (True, "CLEAN", [("lint", "SUCCESS")], ["lint"], "none", "auto-merge enabled"),
        (
            False,
            "BLOCKED",
            [("lint", "CANCELLED"), ("test", "PENDING")],
            [],
            "none",
            "checks failing: lint",
        ),
        (
            False,
            "BLOCKED",
            [("lint", "FAILURE"), ("lint", "SUCCESS")],
            [],
            "none",
            "checks failing: lint",
        ),
    ],
)
def test_sweep_decision(tmp_path, armed, merge, checks, required, action, reason):
    pr = {
        "number": 1,
        "url": "https://github.com/example/repo/pull/1",
        "createdAt": "2020-01-01T00:00:00Z",
        "autoMergeRequest": {} if not armed else {"enabled": True},
        "mergeStateStatus": merge,
        "statusCheckRollup": [
            {"name": name, "status": "COMPLETED", "conclusion": state}
            for name, state in checks
        ],
    }
    (tmp_path / "prs.json").write_text(json.dumps([pr]))
    (tmp_path / "required.txt").write_text("\n".join(required))
    summary = tmp_path / "summary.md"
    result = subprocess.run(  # noqa: S603 - executes the workflow under test.
        [sys.executable, "-c", _script()],
        cwd=tmp_path,
        env=dict(os.environ, STALE_AFTER_HOURS="12", GITHUB_STEP_SUMMARY=str(summary)),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.split("\t")[1] == action
    assert reason in result.stdout
    assert reason in summary.read_text()
    assert pr["url"] in summary.read_text()


@pytest.mark.parametrize("step_name", ["Collect open Dependabot PRs", "Arm or land"])
def test_api_failure_fails_the_workflow_step(tmp_path, step_name):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(
        s for s in workflow["jobs"]["sweep"]["steps"] if s.get("name") == step_name
    )
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho 'HTTP 401: Bad credentials' >&2\nexit 1\n")
    gh.chmod(0o755)
    (tmp_path / "actions.txt").write_text("1\tmerge\tgreen\t1h\t0\n")
    result = subprocess.run(  # noqa: S603 - executes the workflow under test.
        ["/bin/bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PATH=f"{tmp_path}:{os.environ['PATH']}",
            GH_REPO="example/repo",
            GITHUB_OUTPUT=str(tmp_path / "output"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Bad credentials" in result.stdout + result.stderr
