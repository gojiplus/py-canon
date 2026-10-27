"""Tests for fleet triage.

Nothing here touches the network: every function under test is either pure or
takes the `gh` output as an argument.
"""

import json
from pathlib import Path

import pytest

from py_canon import fleet_triage
from py_canon.fleet_triage import (
    RepoStatus,
    _error_lines,
    _normalize,
    classify,
    detect_same_sha_flip,
    is_external_resource,
    is_mechanical,
    parse_first_seen,
    read_fleet,
    render_issue,
    shared_step_names,
)

# The decision table from the module docstring, one row per case. A rule that
# is not exercised here is a rule that will be rediscovered in production.
CLASSIFY_CASES = [
    # (canon ci, same-sha flip, step overlaps, expected)
    (True, True, True, "ecosystem"),
    (True, True, False, "ecosystem"),
    (True, False, True, "repo"),
    (True, False, False, "repo"),
    (False, True, True, "ecosystem"),
    (False, True, False, "ecosystem"),
    (False, False, True, "repo"),
    (False, False, False, "repo"),
]


@pytest.mark.parametrize(("canon", "flip", "overlap", "expected"), CLASSIFY_CASES)
def test_classify_matrix(canon: bool, flip: bool, overlap: bool, expected: str) -> None:
    status = RepoStatus(
        repo="o/r",
        failed_steps=[("test", "Pyright")],
        uses_canon_ci=canon,
        same_sha_flip=flip,
    )
    shared = {"Pyright"} if overlap else set()
    assert classify(status, shared) == expected


def test_a_repo_with_its_own_ci_is_never_blamed_on_canon() -> None:
    """The structural fact settles it before any log is read."""
    status = RepoStatus(
        repo="appeler/pranaam",
        failed_steps=[("test", "Run integration tests")],
        uses_canon_ci=False,
        same_sha_flip=False,
    )
    assert classify(status, {"Run integration tests"}) != "canon"


def test_shared_step_names_needs_two_repos() -> None:
    a = RepoStatus(repo="o/a", failed_steps=[("lint", "Pyright"), ("test", "Sync")])
    b = RepoStatus(repo="o/b", failed_steps=[("lint", "Pyright")])
    c = RepoStatus(repo="o/c", failed_steps=[("test", "Unit tests")])
    assert shared_step_names([a, b, c]) == {"Pyright"}


def test_detect_same_sha_flip() -> None:
    runs = [
        {"conclusion": "failure", "head_sha": "abc"},
        {"conclusion": "success", "head_sha": "abc"},
    ]
    assert detect_same_sha_flip(runs) is True


def test_same_sha_flip_is_false_when_the_code_changed() -> None:
    runs = [
        {"conclusion": "failure", "head_sha": "def"},
        {"conclusion": "success", "head_sha": "abc"},
    ]
    assert detect_same_sha_flip(runs) is False


# A real `gh run view --log-failed` slice from appeler/pranaam run 31671317657,
# kept verbatim on disk — tabs, timestamps and all — so the prefix stripping is
# tested against the format GitHub actually emits rather than a tidied guess.
PRANAAM_LOG = (Path(__file__).parent / "data" / "pranaam-log-failed.txt").read_text()


def test_error_lines_strips_prefixes_and_keeps_the_cause() -> None:
    lines = _error_lines(PRANAAM_LOG)
    assert not any("\t" in line for line in lines)
    assert any("empty file" in line for line in lines)
    assert any("Cannot download model data file" in line for line in lines)


def test_error_lines_drops_passing_noise() -> None:
    lines = _error_lines(PRANAAM_LOG)
    assert not any("PASSED" in line for line in lines)


def _log(*messages: str) -> str:
    return "\n".join(f"job\tstep\t2026-01-01T00:00:00Z {m}" for m in messages)


def test_error_lines_collapses_pytests_repeated_summary() -> None:
    """The defect the first live dry run exposed.

    pytest restates every failure once per test at the end of a run. Keeping
    the tail therefore kept twelve restatements of one root cause and dropped
    the cause itself, which had been logged first.
    """
    log = _log(
        "ERROR    pkg:utils.py:74 File extraction error: empty file",
        *[
            f"FAILED tests/test_e2e.py::TestX::test_{i} - RuntimeError: no model at "
            f"/runner/work/pkg/model/m{i}.keras"
            for i in range(12)
        ],
        "##[error]Process completed with exit code 1.",
    )
    lines = _error_lines(log, keep=5)
    assert any("empty file" in line for line in lines)
    assert sum("FAILED" in line for line in lines) == 1
    assert "##[error]Process completed with exit code 1." in lines


def test_error_lines_keeps_both_ends_when_over_budget() -> None:
    words = [
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
        "foxtrot",
        "golf",
        "hotel",
        "india",
        "juliet",
    ]
    log = _log(*[f"error: {w} went wrong" for w in words])
    lines = _error_lines(log, keep=5)
    assert lines[0] == "error: alpha went wrong"
    assert lines[-1] == "error: juliet went wrong"
    assert len(lines) == 5


def test_normalize_does_not_merge_genuinely_different_errors() -> None:
    """Dedup must collapse repetition, not distinct causes."""
    assert _normalize("error: connection refused") != _normalize("error: bad token")


def test_mechanical_only_when_every_failed_step_is_a_conformance_check() -> None:
    assert is_mechanical(RepoStatus(repo="o/r", failed_steps=[("lint", "Ruff lint")]))
    assert is_mechanical(RepoStatus(repo="o/r", failed_steps=[("lint", "Pyright")]))


def test_not_mechanical_when_a_real_test_failed() -> None:
    """The pranaam case: preen fix would find drift and PR an unrelated diff."""
    status = RepoStatus(
        repo="appeler/pranaam",
        failed_steps=[("test", "Run integration tests (main branch only)")],
    )
    assert is_mechanical(status) is False


def test_not_mechanical_when_only_some_steps_are_conformance() -> None:
    status = RepoStatus(
        repo="o/r", failed_steps=[("lint", "Ruff lint"), ("test", "Unit tests")]
    )
    assert is_mechanical(status) is False


def test_not_mechanical_without_step_data() -> None:
    """No evidence is not evidence of a safe fix."""
    assert is_mechanical(RepoStatus(repo="o/r")) is False


def test_external_resource_tag_fires_on_the_pranaam_signature() -> None:
    status = RepoStatus(repo="appeler/pranaam", excerpt=_error_lines(PRANAAM_LOG))
    assert is_external_resource(status) is True


def test_external_resource_tag_does_not_fire_on_an_ordinary_assertion() -> None:
    status = RepoStatus(
        repo="o/r",
        excerpt=["E   AssertionError: assert 3 == 4", "FAILED tests/test_x.py"],
    )
    assert is_external_resource(status) is False


def test_first_seen_survives_a_round_trip() -> None:
    """The issue body is the state store, so it has to parse back."""
    status = RepoStatus(repo="appeler/pranaam", branch="main", excerpt=["error: boom"])
    body = render_issue([status], "2026-08-14")
    assert parse_first_seen(body) == {"appeler/pranaam": "2026-08-14"}


def test_first_seen_is_carried_not_reset() -> None:
    old = render_issue([RepoStatus(repo="o/r", branch="main")], "2026-08-01")
    carried = parse_first_seen(old)["o/r"]
    new = render_issue(
        [RepoStatus(repo="o/r", branch="main", first_seen=carried)], "2026-08-14"
    )
    assert "first seen 2026-08-01" in new


def test_render_groups_by_class_and_correlates() -> None:
    a = RepoStatus(
        repo="o/a",
        branch="main",
        failed_steps=[("lint", "Pyright")],
        uses_canon_ci=True,
    )
    b = RepoStatus(
        repo="o/b",
        branch="main",
        failed_steps=[("lint", "Pyright")],
        uses_canon_ci=True,
    )
    body = render_issue([a, b], "2026-08-14")
    assert "## Repo-specific" in body
    assert "## Canon-caused" not in body
    assert "step `Pyright` is failing on 2 repos" in body


def test_render_correlates_a_shared_host() -> None:
    excerpt = [
        "error: failed to fetch https://dataverse.harvard.edu/api/access/datafile/1"
    ]
    a = RepoStatus(repo="o/a", branch="main", excerpt=excerpt)
    b = RepoStatus(repo="o/b", branch="main", excerpt=excerpt)
    body = render_issue([a, b], "2026-08-14")
    assert "`dataverse.harvard.edu` appears in 2 repos' errors" in body


def test_read_fleet_skips_blanks_and_comments(tmp_path) -> None:
    fleet = tmp_path / "FLEET"
    fleet.write_text("# a comment\nowner/one\n\n  owner/two  \n")
    assert read_fleet(fleet) == ["owner/one", "owner/two"]


def test_the_echoed_run_block_is_not_quoted_as_an_error() -> None:
    """GitHub echoes a step's whole `run:` block before executing it.

    Those lines are the script, never its output. Quoting one reports a message
    the step may never have printed: onlinerake and statqa were both filed as
    "pydoclint found no package to check" — a string from the echoed
    `echo "::error::..."` in reusable-ci.yml — when pydoclint had passed on both.
    """
    log = (
        "ci / lint\tUNKNOWN STEP\t2026-08-24T07:31:19.3Z ^[[36;1m  "
        'echo "::error::pydoclint found no package to check: there is no src/"^[[0m\n'
        "ci / lint\tUNKNOWN STEP\t2026-08-24T07:31:32.7Z "
        "[warning] links: Broken link: https://example.com/x (HTTP 404)\n"
        "ci / lint\tUNKNOWN STEP\t2026-08-24T07:31:32.8Z "
        "##[error]Process completed with exit code 1.\n"
    )

    lines = _error_lines(log)

    assert not any("no package to check" in line for line in lines)
    assert any("Broken link" in line for line in lines)


def test_a_real_escape_byte_is_recognised_too() -> None:
    """`gh run view --log` writes a literal "^[" off a terminal, ESC on one."""
    log = (
        "ci / lint\tUNKNOWN STEP\t2026-08-24T07:31:19.3Z \x1b[36;1m  "
        'echo "::error::this is the script"\x1b[0m\n'
    )

    assert _error_lines(log) == []


def test_preen_findings_reach_the_excerpt() -> None:
    """Without them a failed conformance step quotes only the exit status.

    True, and no help to whoever opens the issue.
    """
    log = (
        "ci / lint\tUNKNOWN STEP\t2026-08-24T07:31:32.7Z "
        "[warning] template: .copier-answers.yml records the moving tag _commit='v1'\n"
        "ci / lint\tUNKNOWN STEP\t2026-08-24T07:31:32.8Z "
        "##[error]Process completed with exit code 1.\n"
    )

    lines = _error_lines(log)

    assert any("moving tag" in line for line in lines)


def test_gate_is_not_a_root_cause():
    status = RepoStatus(
        repo="o/a", failed_steps=[("gate", "Assert no upstream job failed")]
    )
    assert status.step_names == set()
    assert not is_mechanical(status)


def test_one_repo_with_two_failed_runs_is_not_cross_repo_evidence():
    status = RepoStatus(repo="o/a", failed_steps=[("lint", "Pyright")])
    assert shared_step_names([status, status]) == set()


def test_shared_tool_failure_requires_log_evidence():
    status = RepoStatus(
        repo="o/a",
        uses_canon_ci=True,
        failed_steps=[("lint", "Preen")],
        excerpt=_error_lines(
            "cause: Because the current Python version (3.11.14) does not satisfy "
            "Python>=3.12 and preen==0.7.0 depends on Python>=3.12, "
            "preen==0.7.0 cannot be used."
        ),
    )
    assert classify(status, {"Preen"}) == "canon"


def test_repository_backend_error_is_not_a_shared_tool_defect():
    status = RepoStatus(
        repo="o/a",
        uses_canon_ci=True,
        failed_steps=[("lint", "Preen")],
        excerpt=[
            "[warning] metadata: build-system must use a compatible requirement; "
            "found hatchling"
        ],
    )
    assert classify(status, {"Preen"}) == "repo"


def test_scan_preserves_distinct_workflows_and_approvals(monkeypatch):
    def run(number, workflow, conclusion, branch="main", event="schedule"):
        return {
            "id": number,
            "workflow_id": workflow,
            "name": str(workflow),
            "head_branch": branch,
            "head_sha": str(number),
            "event": event,
            "conclusion": conclusion,
            "created_at": f"2026-10-19T0{number}:00:00Z",
        }

    runs = [
        run(1, 10, "failure"),
        run(2, 10, "success", event="push"),
        run(3, 20, "failure"),
        run(4, 30, "action_required", "update"),
        run(5, 10, "failure", "other-branch"),
    ]
    pages = [{"workflow_runs": runs[:2]}, {"workflow_runs": runs[2:]}]

    def gh(*args, **kwargs):
        if args[-1] == "repos/o/a":
            return json.dumps({"id": 1, "default_branch": "main"})
        if "/pulls?" in args[-1]:
            return json.dumps(
                [
                    [
                        {"head": {"ref": branch, "sha": sha, "repo": {"id": None}}}
                        for branch, sha in [("update", "4"), ("other-branch", "5")]
                    ]
                ]
            )
        return json.dumps(pages)

    monkeypatch.setattr(fleet_triage, "_gh", gh)
    monkeypatch.setattr(fleet_triage, "failed_steps", lambda *a: [])
    monkeypatch.setattr(fleet_triage, "error_excerpt", lambda *a: [])
    statuses = fleet_triage.scan(["o/a"])
    assert len(statuses) == 4
    assert next(s for s in statuses if s.run_id == 2).recovered
    assert not next(s for s in statuses if s.run_id == 5).recovered
    assert {classify(s, set()) for s in statuses} == {"recovered", "repo", "approval"}
    body = render_issue(statuses, "2026-10-19")
    assert "Recovered" in body
    assert "Approval required" in body


def test_unreadable_history_is_not_green(monkeypatch):
    def unavailable(*args, **kwargs):
        raise fleet_triage.GhError("unavailable")

    monkeypatch.setattr(fleet_triage, "_gh", unavailable)
    statuses = fleet_triage.scan(["o/a"])
    assert len(statuses) == 1
    assert classify(statuses[0], set()) == "inaccessible"


def test_diagnostic_correlations_preserve_shared_revision():
    statuses = [
        RepoStatus(repo="o/a", workflow_revision="abc", excerpt=["error: tool failed"]),
        RepoStatus(repo="o/b", workflow_revision="abc", excerpt=["error: tool failed"]),
        RepoStatus(repo="o/c", workflow_revision="def", excerpt=["error: tool failed"]),
    ]
    body = render_issue(statuses, "2026-10-19")
    assert (
        "diagnostic `error: tool failed` recurs at shared revision `abc`: `o/a`, `o/b`"
        in body
    )
    assert "recurs at shared revision `def`" not in body


@pytest.fixture
def scan_history(monkeypatch):
    def setup(runs, pulls=(), unavailable=False):
        def gh(*args, **kwargs):
            endpoint = args[-1]
            if endpoint == "repos/o/a":
                return json.dumps({"id": 1, "default_branch": "main"})
            if "/pulls?" in endpoint:
                if unavailable:
                    raise fleet_triage.GhError("cannot read PR state")
                return json.dumps([list(pulls)])
            return json.dumps([{"workflow_runs": runs}])

        monkeypatch.setattr(fleet_triage, "_gh", gh)
        monkeypatch.setattr(
            fleet_triage, "failed_steps", lambda *a: [("test", "pytest")]
        )
        monkeypatch.setattr(fleet_triage, "error_excerpt", lambda *a: ["error: broken"])
        return fleet_triage.scan(["o/a"])

    return setup


def workflow_run(number=1, branch="feature", sha="old", conclusion="failure"):
    return {
        "id": number,
        "workflow_id": 10,
        "name": "CI",
        "head_branch": branch,
        "head_repository": {"id": 1},
        "head_sha": sha,
        "conclusion": conclusion,
        "created_at": f"2026-10-27T0{number}:00:00Z",
    }


def open_pull(sha="old", repo_id=1):
    return {"head": {"ref": "feature", "sha": sha, "repo": {"id": repo_id}}}


def test_closed_branch_failure_is_historical_not_recovered(scan_history):
    (status,) = scan_history([workflow_run()])
    assert classify(status, set()) == "historical"
    assert status.conclusion == "failure"
    assert not status.recovered


def test_open_pr_failure_stays_actionable(scan_history):
    (status,) = scan_history([workflow_run()], [open_pull()])
    assert classify(status, set()) == "repo"


def test_fork_branch_cannot_match_another_repository(scan_history):
    (status,) = scan_history([workflow_run()], [open_pull(repo_id=2)])
    assert classify(status, set()) == "historical"


def test_new_pr_head_is_pending_instead_of_old_failure(scan_history):
    (status,) = scan_history([workflow_run()], [open_pull(sha="new")])
    assert classify(status, set()) == "pending"
    assert status.failed_steps == []
    assert status.run_id == 0


@pytest.mark.parametrize("conclusion", [None, "cancelled", "skipped"])
def test_current_run_does_not_resurrect_old_failure(scan_history, conclusion):
    statuses = scan_history(
        [
            workflow_run(branch="main", conclusion="failure"),
            workflow_run(2, "main", "new", conclusion),
        ]
    )
    assert classify(statuses[0], set()) == "pending"


def test_unreadable_pr_state_does_not_hide_failures(scan_history):
    (status,) = scan_history([workflow_run()], unavailable=True)
    assert classify(status, set()) == "inaccessible"


def test_default_branch_failure_is_active_without_pr(scan_history):
    (status,) = scan_history([workflow_run(branch="main")])
    assert classify(status, set()) == "repo"


@pytest.mark.parametrize("historical", [True, False])
def test_only_history_or_recoveries_closes_issue(monkeypatch, tmp_path, historical):
    fleet = tmp_path / "FLEET"
    fleet.write_text("o/a\n")
    status = RepoStatus(repo="o/a", recovered=not historical)
    status.historical = historical
    monkeypatch.setattr(fleet_triage, "scan", lambda *a: [status])
    monkeypatch.setattr(fleet_triage, "_existing_issue", lambda *a: (108, ""))
    calls = []
    monkeypatch.setattr(fleet_triage, "_gh", lambda *a, **k: calls.append(a) or "")
    fleet_triage.main(["--fleet", str(fleet), "--issue-repo", "o/canon"])
    assert any(call[:2] == ("issue", "close") for call in calls)


@pytest.mark.parametrize("state", ["pending", "inaccessible", "action_required"])
def test_unknown_or_unvalidated_state_never_closes_issue(monkeypatch, tmp_path, state):
    fleet = tmp_path / "FLEET"
    fleet.write_text("o/a\n")
    status = RepoStatus(repo="o/a", conclusion=state, pending=state == "pending")
    monkeypatch.setattr(fleet_triage, "scan", lambda *a: [status])
    monkeypatch.setattr(fleet_triage, "_existing_issue", lambda *a: (108, ""))
    calls = []
    monkeypatch.setattr(fleet_triage, "_gh", lambda *a, **k: calls.append(a) or "")
    fleet_triage.main(["--fleet", str(fleet), "--issue-repo", "o/canon"])
    assert not any(call[:2] == ("issue", "close") for call in calls)


def test_historical_failures_do_not_create_active_correlations():
    statuses = [
        RepoStatus(repo="o/a", failed_steps=[("lint", "Preen")]),
        RepoStatus(repo="o/b", historical=True, failed_steps=[("lint", "Preen")]),
    ]
    assert "Across repos" not in render_issue(statuses, "2026-10-27")


def test_intentionally_skipped_automation_is_not_pending(scan_history):
    assert scan_history([workflow_run(branch="main", conclusion="skipped")]) == []


def test_new_pr_head_does_not_invalidate_previously_healthy_optional_workflow(
    scan_history,
):
    assert (
        scan_history([workflow_run(conclusion="success")], [open_pull(sha="new")]) == []
    )


def test_skipped_rerun_keeps_success_on_same_head(scan_history):
    (status,) = scan_history(
        [
            workflow_run(branch="main"),
            workflow_run(2, "main", "new", "success"),
            workflow_run(3, "main", "new", "skipped"),
        ]
    )
    assert status.recovered
    assert not status.pending


def test_dependabot_ecosystems_cannot_recover_each_other(scan_history):
    uv = workflow_run(branch="main")
    uv["name"] = "uv in /. - Update #123"
    actions = workflow_run(2, "main", "old", "success")
    actions["name"] = "github_actions in /. - Update #124"
    (status,) = scan_history([uv, actions])
    assert status.workflow == uv["name"]
    assert status.conclusion == "failure"
    assert not status.recovered


def test_later_update_of_same_ecosystem_can_recover(scan_history):
    old = workflow_run(branch="main")
    old["name"] = "uv in /. - Update #123"
    new = workflow_run(2, "main", "new", "success")
    new["name"] = "uv in /. - Update #124"
    (status,) = scan_history([old, new])
    assert status.recovered
    assert status.run_id == 2
