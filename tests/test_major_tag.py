"""Exercise the promotion gate without contacting GitHub or moving tags."""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/major-tag.yml"


def _gate_script() -> str:
    workflow = WORKFLOW.read_text()
    section = workflow.split(
        "      - name: Require successful CI for the target commit\n"
    )[1]
    return textwrap.dedent(
        section.split("        run: |\n")[1].split("      - name:")[0]
    )


@pytest.mark.parametrize("conclusion", ["failure", "", "cancelled", "null", "skipped"])
def test_promotion_rejects_missing_or_unsuccessful_ci(tmp_path, conclusion) -> None:
    result = _run_gate(tmp_path, conclusion)
    assert result.returncode != 0
    assert "has not succeeded" in result.stdout


def test_promotion_accepts_success_for_the_requested_commit(tmp_path) -> None:
    result = _run_gate(tmp_path, "success")
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "arguments").read_text().splitlines()
    assert args[args.index("--commit") + 1] == "a" * 40
    assert args[args.index("--branch") + 1] == "main"
    assert args[args.index("--workflow") + 1] == "ci.yml"
    assert "--status" not in args  # A prior success must not hide a later failure.


def test_promotion_rejects_api_failure(tmp_path) -> None:
    result = _run_gate(tmp_path, "success", exit_code=1)
    assert result.returncode != 0


def _run_gate(tmp_path, conclusion, *, exit_code=0):
    gh = tmp_path / "gh"
    gh.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENTS"\n'
        'printf "%s\\n" "$CI_CONCLUSION"\nexit "$GH_EXIT"\n'
    )
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ARGUMENTS": str(tmp_path / "arguments"),
        "CI_CONCLUSION": conclusion,
        "GH_EXIT": str(exit_code),
        "TARGET": "a" * 40,
        "DEFAULT_BRANCH": "main",
    }
    return subprocess.run(  # noqa: S603 - fixed shell, isolated command fixture
        ["/bin/bash", "-c", _gate_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
