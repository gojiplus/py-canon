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


BASE_SHA = "b" * 40
BASE_DATE = "2026-11-14T03:50:00Z"


def _plan(tmp_path, pr, required, base_runs=None):
    """Run the sweep's plan program the way the Collect step leaves its files."""
    if base_runs is None:
        base_runs = [_ci_run("success")]
    (tmp_path / "prs.json").write_text(json.dumps([pr]))
    (tmp_path / "required.txt").write_text("\n".join(required))
    (tmp_path / "base-branch.txt").write_text("main\n")
    (tmp_path / "base-head.json").write_text(
        json.dumps({"sha": BASE_SHA, "commit": {"committer": {"date": BASE_DATE}}})
    )
    (tmp_path / "base-runs.json").write_text(json.dumps({"workflow_runs": base_runs}))
    return subprocess.run(  # noqa: S603 - executes the workflow under test.
        [sys.executable, "-c", _script()],
        cwd=tmp_path,
        env=dict(
            os.environ,
            STALE_AFTER_HOURS="12",
            GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
        ),
        capture_output=True,
        text=True,
        check=True,
    )


CI_PATH = ".github/workflows/ci.yml"


def _ci_run(conclusion, created="2026-11-14T03:50:03Z", path=CI_PATH):
    return {
        "path": path,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": created,
    }


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
        "url": "https://example.com/repo/pull/1",
        "createdAt": "2020-01-01T00:00:00Z",
        "autoMergeRequest": {} if not armed else {"enabled": True},
        "mergeStateStatus": merge,
        "statusCheckRollup": [
            {"name": name, "status": "COMPLETED", "conclusion": state}
            for name, state in checks
        ],
    }
    summary = tmp_path / "summary.md"
    result = _plan(tmp_path, pr, required)
    assert result.stdout.split("\t")[1] == action
    assert reason in result.stdout
    assert reason in summary.read_text()
    assert pr["url"] in summary.read_text()


def _failing_pr(completed, labels=(), merge="BLOCKED", extra=()):
    return {
        "number": 7,
        "url": "https://example.com/repo/pull/7",
        "createdAt": "2026-11-01T00:00:00Z",
        "autoMergeRequest": {"enabled": True},
        "mergeStateStatus": merge,
        "labels": [{"name": name} for name in labels],
        "statusCheckRollup": [
            {
                "name": "ci / lint",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "completedAt": completed,
            },
            {
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "completedAt": "2026-11-20T00:00:00Z",
            },
            *extra,
        ],
    }


def _row(result):
    _n, act, verdict, _age, _stale, labeled = result.stdout.rstrip("\n").split("\t")
    return act, verdict, labeled


@pytest.mark.parametrize(
    ("completed", "base_runs", "merge", "extra", "action"),
    [
        # The measured case: lint failed on Nov 2, main went green on Nov 14.
        ("2026-11-02T23:37:09Z", None, "BLOCKED", (), "rebase"),
        # Failed after main's head existed: it was tested against the fix.
        ("2026-11-14T04:00:00Z", None, "BLOCKED", (), "none"),
        # Main itself is red, so a rebase would test nothing new.
        ("2026-11-02T23:37:09Z", [_ci_run("failure")], "BLOCKED", (), "none"),
        # The latest CI run on main decides, not any earlier green one.
        (
            "2026-11-02T23:37:09Z",
            [_ci_run("success"), _ci_run("failure", created="2026-11-14T05:00:00Z")],
            "BLOCKED",
            (),
            "none",
        ),
        # Only ci.yml speaks for main; a green docs run is not evidence.
        (
            "2026-11-02T23:37:09Z",
            [_ci_run("success", path=".github/workflows/docs.yml")],
            "BLOCKED",
            (),
            "none",
        ),
        # No runs listed (actions: read withheld): behave as before.
        ("2026-11-02T23:37:09Z", [], "BLOCKED", (), "none"),
        # Conflicts are Dependabot's own to rebase.
        ("2026-11-02T23:37:09Z", None, "DIRTY", (), "none"),
        # One fresh failure among stale ones means the red is live.
        (
            "2026-11-02T23:37:09Z",
            None,
            "BLOCKED",
            (
                {
                    "name": "ci / wheel",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "completedAt": "2026-11-14T04:00:00Z",
                },
            ),
            "none",
        ),
        # A failure with no timestamp cannot be shown to predate anything.
        (None, None, "BLOCKED", (), "none"),
    ],
)
def test_stale_failure_detection(tmp_path, completed, base_runs, merge, extra, action):
    pr = _failing_pr(completed, merge=merge, extra=extra)
    act, verdict, _ = _row(_plan(tmp_path, pr, ["ci / gate"], base_runs))
    assert act == action
    assert "checks failing: ci / lint" in verdict
    assert ("predates green main bbbbbbb" in verdict) is (action == "rebase")


@pytest.mark.parametrize("labels", [(), ("needs-rebase",)])
def test_label_state_is_reported(tmp_path, labels):
    pr = _failing_pr("2026-11-02T23:37:09Z", labels=labels)
    _act, _verdict, labeled = _row(_plan(tmp_path, pr, ["ci / gate"]))
    assert labeled == str(len(labels))


def _arm_or_land(tmp_path, rows, rebase_token=""):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(
        s for s in workflow["jobs"]["sweep"]["steps"] if s.get("name") == "Arm or land"
    )
    log = tmp_path / "gh.log"
    gh = tmp_path / "gh"
    gh.write_text(f'#!/bin/sh\necho "token=$GH_TOKEN $*" >> {log}\n')
    gh.chmod(0o755)
    (tmp_path / "base-branch.txt").write_text("main\n")
    (tmp_path / "actions.txt").write_text("".join(f"{row}\n" for row in rows))
    result = subprocess.run(  # noqa: S603 - executes the workflow under test.
        ["/bin/bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PATH=f"{tmp_path}:{os.environ['PATH']}",
            GH_TOKEN="actions-token",  # noqa: S106 - a fake token for a stub gh.
            REBASE_TOKEN=rebase_token,
            GH_REPO="example/repo",
            GITHUB_OUTPUT=str(tmp_path / "output"),
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result.stdout, calls


def test_rebase_without_token_labels_and_warns(tmp_path):
    out, calls = _arm_or_land(tmp_path, ["7\trebase\tstale\t300h\t0\t0"])
    assert "token=actions-token pr edit 7 --add-label needs-rebase" in calls
    assert not any("pr comment" in call for call in calls)
    assert "::warning::#7 failed only before main went green" in out


def test_rebase_with_token_asks_dependabot_as_the_user(tmp_path):
    out, calls = _arm_or_land(
        tmp_path,
        ["7\trebase\tstale\t300h\t0\t0"],
        rebase_token="user-token",  # noqa: S106
    )
    assert "token=user-token pr comment 7 --body @dependabot rebase" in calls
    assert "token=actions-token pr edit 7 --add-label needs-rebase" in calls
    assert "asked Dependabot to rebase #7" in out
    assert "::warning::" not in out


def test_already_flagged_pr_is_not_asked_again(tmp_path):
    _out, calls = _arm_or_land(
        tmp_path,
        ["7\trebase\tstale\t300h\t0\t1"],
        rebase_token="user-token",  # noqa: S106
    )
    assert not any("pr comment" in call or "add-label" in call for call in calls)


def test_label_is_cleared_once_the_pr_is_retested(tmp_path):
    _out, calls = _arm_or_land(tmp_path, ["7\tnone\tchecks running\t300h\t0\t1"])
    assert calls == ["token=actions-token pr edit 7 --remove-label needs-rebase"]


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


@pytest.mark.parametrize(
    ("job", "step_name"),
    [
        ("classify", "Check whether anything is actually required"),
        ("sweep", "Collect open Dependabot PRs"),
    ],
)
@pytest.mark.parametrize(
    ("mode", "success", "required"),
    [
        ("supported", True, "ci / gate\n"),
        ("plan-limit", True, ""),
        ("unauthorized", False, None),
        ("forbidden", False, None),
        ("rate-limit", False, None),
        ("detail-failure", False, None),
    ],
)
def test_required_checks_probe(tmp_path, job, step_name, mode, success, required):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in workflow["jobs"][job]["steps"] if s["name"] == step_name)
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/bin/sh
if [ "$1" = pr ]; then printf '[]'; exit 0; fi
case "$*" in
  *'--jq .default_branch'*) printf 'main\\n'; exit 0 ;;
  *'/commits/main'*) printf '{"sha": "abc"}'; exit 0 ;;
  *'/actions/runs'*) printf '{"workflow_runs": []}'; exit 0 ;;
  *'/rulesets/'*)
    if [ "$PROBE_MODE" = detail-failure ]; then
      echo 'HTTP 401: Bad credentials' >&2; exit 1
    fi
    printf 'ci / gate\\n'; exit 0 ;;
esac
case "$PROBE_MODE" in
  supported|detail-failure) printf '42\\n' ;;
  plan-limit)
    echo 'Upgrade to GitHub Pro or make this repository public' >&2
    exit 1 ;;
  unauthorized) echo 'HTTP 401: Bad credentials' >&2; exit 1 ;;
  forbidden) echo 'HTTP 403: Resource not accessible by integration' >&2; exit 1 ;;
  rate-limit) echo 'HTTP 429: Rate limit exceeded' >&2; exit 1 ;;
esac
"""
    )
    gh.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - executes the workflow under test.
        ["/bin/bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PATH=f"{tmp_path}:{os.environ['PATH']}",
            GH_REPO="example/repo",
            GITHUB_OUTPUT=str(tmp_path / "output"),
            PROBE_MODE=mode,
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    if required is not None:
        assert (tmp_path / "required.txt").read_text() == required
    if mode == "plan-limit":
        assert "only the all-checks sweep may merge" in result.stdout
        if job == "classify":
            assert "count=0" in (tmp_path / "output").read_text()


@pytest.mark.parametrize(
    ("merged", "runs", "dispatched"),
    [
        # recite/softverse: an armed auto-merge left main with no CI run.
        ("0", {"workflow_runs": []}, True),
        ("0", {"workflow_runs": [_ci_run("success")]}, False),
        # A red head is tested; dispatching again would only repeat it.
        ("0", {"workflow_runs": [_ci_run("failure")]}, False),
        ("0", {"workflow_runs": [_ci_run("success", path="x/docs.yml")]}, True),
        # Unknown is not untested: without actions: read, dispatch only after
        # a merge, or every sweep would queue another CI run.
        ("0", {"workflow_runs": [], "unavailable": True}, False),
        ("1", {"workflow_runs": [_ci_run("success")]}, True),
        ("", {"workflow_runs": [_ci_run("success")]}, False),
    ],
)
def test_default_branch_ci_dispatch(tmp_path, merged, runs, dispatched):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(
        s
        for s in workflow["jobs"]["sweep"]["steps"]
        if s.get("name") == "Run CI on the default branch if nothing else has"
    )
    log = tmp_path / "gh.log"
    gh = tmp_path / "gh"
    gh.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    gh.chmod(0o755)
    (tmp_path / "base-branch.txt").write_text("main\n")
    (tmp_path / "base-runs.json").write_text(json.dumps(runs))
    subprocess.run(  # noqa: S603 - executes the workflow under test.
        ["/bin/bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", MERGED=merged),
        capture_output=True,
        text=True,
        check=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    assert calls == (["workflow run ci.yml --ref main"] if dispatched else [])
