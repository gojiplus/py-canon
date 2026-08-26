"""Tests for fleet triage.

Nothing here touches the network: every function under test is either pure or
takes the `gh` output as an argument.
"""

from pathlib import Path

import pytest

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
    (True, True, True, "canon"),
    (True, True, False, "ecosystem"),
    (True, False, True, "canon"),
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
    assert "## Canon-caused" in body
    assert "step `Pyright` is failing on 2 repos" in body
    assert "not with a pull request per repo" in body


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
