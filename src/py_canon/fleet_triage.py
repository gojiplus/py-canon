"""Root-cause a red fleet and render it into one issue.

The daily scan used to say only *which* repos were red, which left the
expensive part — opening the run, finding the failed step, reading the log,
deciding who owns the fix — to a human, once per repo, every day.

Everything that step does mechanically is done here instead. What is left for
a person is the judgement the classifier deliberately does not attempt.

The classifier is a pure function (:func:`classify`) over a
:class:`RepoStatus`, so the rules are unit-tested rather than discovered in
production. Two of this repo's own bugs (#8, #26) were untested workflow logic
silently doing the wrong thing; the scan this module replaces was a 40-line
inline shell blob with the same property.

Run it the way the workflow does::

    uv run python -m py_canon.fleet_triage --fleet FLEET --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Workflow names that count as "the repo's CI". Legacy adopters predate the
# standard's naming, so the scan cannot assume `CI`.
CI_WORKFLOW_NAMES = frozenset({"CI", "ci", "test", "Test", "tests", "Tests"})

# Conclusions that mean red. `cancelled` is excluded on purpose: a cancelled
# run is usually a superseded push or a sibling matrix leg being torn down by
# fail-fast, and treating those as failures makes the issue cry wolf.
RED_CONCLUSIONS = frozenset({"failure", "startup_failure", "timed_out"})

CANON_CI_REF = "py-canon/.github/workflows/reusable-ci.yml"

# Failed steps that `preen fix` could plausibly be the answer to. Matched
# against the step name from reusable-ci.yml's lint job, casefolded, so a
# repo's own spelling of the same check still hits.
CONFORMANCE_STEP_MARKERS = ("ruff", "pyright", "pydoclint", "preen", "lint", "format")

# GitHub echoes a step's whole `run:` block before executing it, tinted with
# this SGR sequence. Those lines are the *script*, never its output, and
# quoting one reports a message the step may never have printed: onlinerake and
# statqa were both filed as "pydoclint found no package to check" -- a string
# from the echoed `echo "::error::..."` in reusable-ci.yml -- when pydoclint
# had passed on both and the real failures were a 404 README badge and three
# conformance findings.
# Both renderings: `gh run view --log` writes the escape as the literal two
# characters "^[" when it is not attached to a terminal, and as a real ESC
# byte when it is.
ECHOED_SCRIPT = re.compile(r"(?:\x1b|\^\[)\[36;1m")

# Lines worth quoting from a failed job's log.
ERROR_PATTERNS = (
    re.compile(r"^E\s{2,}"),  # pytest assertion detail
    re.compile(r"##\[error\]"),
    re.compile(r"^\s*Traceback \(most recent call last\)"),
    re.compile(r"\bFAILED\b"),
    re.compile(r"\b(?:error|Error|ERROR):"),
    re.compile(r"^\s*(?:error|Error|ERROR)\b"),
    # preen prints its findings as "[warning] links: Broken link: ...". Without
    # this the excerpt for a failed conformance step is the exit status and
    # nothing else -- true, and no help to whoever opens the issue.
    re.compile(r"^\[(?:error|warning)\]\s\S"),
)

# An orthogonal tag, not a class: a repo-specific failure and an ecosystem
# failure can both be a download that stopped working. The tag is what makes
# the same broken host visible across repos before they go red one at a time.
EXTERNAL_RESOURCE_PATTERNS = (
    re.compile(r"\bempty file\b", re.IGNORECASE),
    re.compile(r"\bcannot download\b", re.IGNORECASE),
    re.compile(
        r"\b(?:connection|read) (?:error|timed out|refused|reset)\b", re.IGNORECASE
    ),
    re.compile(r"\bHTTP(?:Error)?\b.*\b(?:401|403|404|429|5\d\d)\b"),
    re.compile(
        r"\b(?:certificate|SSL|TLS)\b.*\b(?:verify|expired|failed)\b", re.IGNORECASE
    ),
    re.compile(r"\bMax retries exceeded\b", re.IGNORECASE),
    re.compile(r"\b(?:urlopen|requests\.exceptions)\b"),
)

# Hosts named in an excerpt, so the correlation line can say *which* dependency
# broke rather than only that several repos share a symptom.
HOST_PATTERN = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)

FIRST_SEEN_MARKER = "first seen"
FIRST_SEEN_PATTERN = re.compile(
    r"`(?P<repo>[\w.-]+/[\w.-]+)`.*?"
    + FIRST_SEEN_MARKER
    + r" (?P<date>\d{4}-\d{2}-\d{2})",
    re.DOTALL,
)

CLASS_HEADINGS = {
    "canon": (
        "Canon-caused",
        "A shared workflow or the `v1` tag. Fix once **here** and promote — "
        "not with a pull request per repo.",
    ),
    "ecosystem": (
        "Ecosystem-caused",
        "Unchanged code went red on its own: an action major, a runner image, "
        "or a dependency release. Fix in the shared workflow if it lives there, "
        "otherwise per repo.",
    ),
    "repo": (
        "Repo-specific",
        "This repo's own code or its own dependencies. Needs a person who knows "
        "the package.",
    ),
}


class GhError(RuntimeError):
    """A `gh` invocation failed in a way the caller should not swallow."""


@dataclass
class RepoStatus:
    """Everything the classifier is allowed to look at for one red repo.

    Attributes:
        repo: ``owner/name``.
        branch: The default branch that is red.
        conclusion: The failing run's conclusion.
        run_id: The failing run's numeric id.
        run_url: Web URL of the failing run.
        failed_steps: ``(job name, step name)`` for every failed step.
        excerpt: Trimmed error lines from the failed jobs' logs.
        uses_canon_ci: Whether the repo's ``ci.yml`` calls the reusable
            workflow. A repo that does not cannot be broken by canon's CI.
        same_sha_flip: Whether an earlier green run shares the red run's
            ``head_sha`` — unchanged code that went red on its own.
        first_seen: Date this repo first appeared red, carried across scans.
    """

    repo: str
    branch: str = ""
    conclusion: str = "failure"
    run_id: int = 0
    run_url: str = ""
    failed_steps: list[tuple[str, str]] = field(default_factory=list)
    excerpt: list[str] = field(default_factory=list)
    uses_canon_ci: bool = False
    same_sha_flip: bool = False
    first_seen: str = ""

    @property
    def step_names(self) -> set[str]:
        """Distinct failed step names, used for cross-repo correlation."""
        return {step for _, step in self.failed_steps}

    @property
    def hosts(self) -> set[str]:
        """Hostnames appearing in the excerpt."""
        return {
            m.group(1).lower()
            for line in self.excerpt
            for m in HOST_PATTERN.finditer(line)
        }


def _gh(*args: str, check: bool = True) -> str:
    """Run `gh` and return stdout.

    Args:
        *args: Arguments after the `gh` executable.
        check: Raise :class:`GhError` on a non-zero exit instead of returning
            what was written to stdout.

    Returns:
        Captured stdout, stripped.

    Raises:
        GhError: The call failed and ``check`` is set.
    """
    proc = subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 and check:
        raise GhError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def read_fleet(path: Path) -> list[str]:
    """Read `owner/repo` lines from a FLEET file, ignoring blanks and comments.

    Args:
        path: Path to the FLEET file.

    Returns:
        Repository slugs in file order.
    """
    lines = path.read_text().splitlines()
    return [s for line in lines if (s := line.strip()) and not s.startswith("#")]


def latest_ci_runs(repo: str, branch: str, limit: int = 20) -> list[dict]:
    """Fetch recent CI runs on a branch, newest first.

    Args:
        repo: ``owner/name``.
        branch: Branch to filter on.
        limit: How many runs to request.

    Returns:
        Run objects restricted to the workflows that count as CI. Empty if the
        repo has no such workflow or the call failed.
    """
    raw = _gh(
        "api",
        f"repos/{repo}/actions/runs?branch={branch}&per_page={limit}",
        "--jq",
        ".workflow_runs",
        check=False,
    )
    if not raw:
        return []
    try:
        runs = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [r for r in runs if r.get("name") in CI_WORKFLOW_NAMES]


def detect_same_sha_flip(runs: list[dict]) -> bool:
    """Report whether the newest red run shares a SHA with an earlier green one.

    The sharpest signal available, and free: the same commit that passed now
    fails, so the cause is outside the repo's own code. It is why the standard
    puts a weekly cron on quiet repos at all.

    Args:
        runs: CI runs newest-first, as returned by :func:`latest_ci_runs`.

    Returns:
        True when an earlier run on the same ``head_sha`` concluded ``success``.
    """
    red = next((r for r in runs if r.get("conclusion") in RED_CONCLUSIONS), None)
    if red is None:
        return False
    sha = red.get("head_sha")
    return any(
        r.get("head_sha") == sha and r.get("conclusion") == "success" for r in runs
    )


def failed_steps(repo: str, run_id: int) -> list[tuple[str, str]]:
    """List the failed (job, step) pairs of a run.

    Args:
        repo: ``owner/name``.
        run_id: Run to inspect.

    Returns:
        ``(job name, step name)`` for each failed step, in job order.
    """
    raw = _gh(
        "api",
        f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
        "--jq",
        ".jobs",
        check=False,
    )
    if not raw:
        return []
    try:
        jobs = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [
        (job.get("name", "?"), step.get("name", "?"))
        for job in jobs
        if job.get("conclusion") == "failure"
        for step in job.get("steps") or []
        if step.get("conclusion") == "failure"
    ]


def _normalize(line: str) -> str:
    """Reduce a line to what makes it a *distinct* error.

    pytest ends a run by restating every failure once per test, so a single
    broken download can produce a dozen lines that differ only by test name and
    file path. Collapsing on that form is what keeps the cause in the excerpt.

    Args:
        line: A log line with its prefix already stripped.

    Returns:
        A comparison key.
    """
    key = re.sub(r"[\w./-]+\.py::[\w:.\[\]-]+", "<node>", line)
    key = re.sub(r"(/[\w.-]+)+", "<path>", key)
    key = re.sub(r"\d+", "#", key)
    return " ".join(key.lower().split())


def _error_lines(log: str, keep: int = 15) -> list[str]:
    """Trim a failed-job log down to the lines that say what broke.

    Selection is head-and-tail, not tail alone. In a CI log the *cause* comes
    first and the *symptom* last: pranaam's failed download was logged at the
    top and the missing-model error 200 lines below it, so keeping the tail
    kept only the consequence. The final lines still earn their place — that is
    where the exit status and the last assertion live.

    Args:
        log: Raw ``gh run view --log-failed`` output.
        keep: Maximum lines to return.

    Returns:
        Matching lines in log order, deduplicated, prefixes stripped.
    """
    hits: list[str] = []
    seen: set[str] = set()
    for raw_line in log.splitlines():
        # `--log-failed` prefixes every line with "job\tstep\t<ISO ts> ".
        if ECHOED_SCRIPT.search(raw_line):
            continue
        line = re.sub(r"^.*?\t.*?\t\S*\s*", "", raw_line).strip()
        line = line.replace("﻿", "")
        if not line or not any(p.search(line) for p in ERROR_PATTERNS):
            continue
        key = _normalize(line)
        if key in seen:
            continue
        seen.add(key)
        hits.append(line[:200])

    if len(hits) <= keep:
        return hits
    tail = min(3, keep - 1)
    return hits[: keep - tail] + hits[-tail:]


def error_excerpt(
    repo: str, run_id: int, keep: int = 15, max_bytes: int = 4_000_000
) -> list[str]:
    """Pull the error lines out of a run's failed jobs.

    Args:
        repo: ``owner/name``.
        run_id: Run to inspect.
        keep: Maximum lines to return.
        max_bytes: Cap on the log text considered; a matrix failure can emit
            hundreds of megabytes and the interesting lines are at the end.

    Returns:
        Trimmed error lines, newest last. Empty when logs have expired.
    """
    log = _gh("run", "view", str(run_id), "--repo", repo, "--log-failed", check=False)
    if not log:
        return []
    return _error_lines(log[-max_bytes:], keep=keep)


def uses_canon_ci(repo: str) -> bool:
    """Report whether a repo's `ci.yml` calls canon's reusable workflow.

    Args:
        repo: ``owner/name``.

    Returns:
        True when the shim is present. A repo with a hand-written CI cannot be
        broken by a change to canon's, which removes a whole class from
        consideration before any log is read.
    """
    content = _gh(
        "api",
        f"repos/{repo}/contents/.github/workflows/ci.yml",
        "--jq",
        ".content",
        check=False,
    )
    if not content:
        return False
    try:
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
    except ValueError:
        return False
    return CANON_CI_REF in decoded


def classify(status: RepoStatus, shared_steps: set[str]) -> str:
    """Decide who owns a failure.

    The rules, in order:

    1. A repo that does not consume canon's CI cannot have been broken by it.
       If its unchanged code went red on its own, that is the ecosystem;
       otherwise it is the repo's own change.
    2. Among repos that do consume canon's CI, the same step failing on more
       than one repo at once is canon's — a lone repo failing a shared step is
       far more likely to be its own code hitting a shared check.
    3. Everything else is the repo's.

    Args:
        status: The collected facts for one repo.
        shared_steps: Failed step names seen on two or more repos in this scan.

    Returns:
        One of ``canon``, ``ecosystem``, ``repo``.
    """
    overlaps = bool(status.step_names & shared_steps)
    if not status.uses_canon_ci:
        return "ecosystem" if status.same_sha_flip else "repo"
    if overlaps:
        return "canon"
    return "ecosystem" if status.same_sha_flip else "repo"


def is_mechanical(status: RepoStatus) -> bool:
    """Report whether the failing step is one `preen fix` could plausibly own.

    Without this gate the remediation tool would clone a repo that is red for a
    failing integration test, find unrelated conformance drift, and open a
    pull request that does not make CI green — a reviewer's time spent on a
    patch that answers a different question than the one they had.

    Args:
        status: The collected facts for one repo.

    Returns:
        True when every failed step is a conformance or lint step.
    """
    if not status.failed_steps:
        return False
    return all(
        any(marker in step.casefold() for marker in CONFORMANCE_STEP_MARKERS)
        for step in status.step_names
    )


def is_external_resource(status: RepoStatus) -> bool:
    """Report whether the excerpt looks like a fetch that stopped working.

    Args:
        status: The collected facts for one repo.

    Returns:
        True when any excerpt line matches a download/network signature.
    """
    return any(
        p.search(line) for line in status.excerpt for p in EXTERNAL_RESOURCE_PATTERNS
    )


def shared_step_names(statuses: list[RepoStatus]) -> set[str]:
    """Failed step names appearing on two or more repos in one scan.

    Args:
        statuses: Every red repo in this scan.

    Returns:
        The step names that correlate across repos.
    """
    counts = Counter(step for s in statuses for step in s.step_names)
    return {step for step, n in counts.items() if n >= 2}


def parse_first_seen(body: str) -> dict[str, str]:
    """Recover per-repo first-seen dates from the previous issue body.

    The issue is edited in place every scan, which makes it the state store —
    no extra file, and it stays true if the workflow is ever rerun by hand.

    Args:
        body: The previous issue body, or an empty string.

    Returns:
        Mapping of ``owner/repo`` to ISO date.
    """
    return {m.group("repo"): m.group("date") for m in FIRST_SEEN_PATTERN.finditer(body)}


def _correlations(statuses: list[RepoStatus], shared: set[str]) -> list[str]:
    """Build the cross-repo lines — the thing no per-repo view can show.

    Args:
        statuses: Every red repo in this scan.
        shared: Failed step names seen on two or more repos.

    Returns:
        Markdown bullet lines; empty when nothing correlates.
    """
    lines: list[str] = []
    for step in sorted(shared):
        repos = sorted(s.repo for s in statuses if step in s.step_names)
        lines.append(
            f"- step `{step}` is failing on {len(repos)} repos: "
            + ", ".join(f"`{r}`" for r in repos)
        )

    host_counts = Counter(host for s in statuses for host in s.hosts)
    for host, n in sorted(host_counts.items()):
        if n < 2:
            continue
        repos = sorted(s.repo for s in statuses if host in s.hosts)
        lines.append(
            f"- `{host}` appears in {n} repos' errors: "
            + ", ".join(f"`{r}`" for r in repos)
        )
    return lines


def render_issue(statuses: list[RepoStatus], today: str) -> str:
    """Render the fleet-health issue body.

    Args:
        statuses: Every red repo in this scan.
        today: ISO date of the scan.

    Returns:
        Markdown body.
    """
    shared = shared_step_names(statuses)
    parts = [f"Repos with failing default-branch CI as of {today}:", ""]

    correlations = _correlations(statuses, shared)
    if correlations:
        parts += ["## Across repos", "", *correlations, ""]

    by_class: dict[str, list[RepoStatus]] = {"canon": [], "ecosystem": [], "repo": []}
    for status in statuses:
        by_class[classify(status, shared)].append(status)

    for name, group in by_class.items():
        if not group:
            continue
        heading, blurb = CLASS_HEADINGS[name]
        parts += [f"## {heading}", "", blurb, ""]
        for status in sorted(group, key=lambda s: s.repo):
            parts += _render_repo(status, today)

    parts += [
        "---",
        "",
        "Triage is computed by `py_canon.fleet_triage`; the class is a rule, not a "
        "diagnosis. See STANDARD.md → CI failure playbook.",
    ]
    return "\n".join(parts)


def _render_repo(status: RepoStatus, today: str) -> list[str]:
    """Render one repo's entry.

    Args:
        status: The repo's collected facts.
        today: ISO date of the scan, used when the repo is newly red.

    Returns:
        Markdown lines.
    """
    first_seen = status.first_seen or today
    tags = " · external resource" if is_external_resource(status) else ""
    lines = [
        f"- [ ] `{status.repo}` — `{status.branch}` CI **{status.conclusion}**"
        f" ({FIRST_SEEN_MARKER} {first_seen}{tags})"
    ]
    if status.run_url:
        lines.append(f"  - run: {status.run_url}")
    for job, step in status.failed_steps[:4]:
        lines.append(f"  - failed: `{job}` → `{step}`")
    if status.excerpt:
        lines += ["", "  ```", *[f"  {line}" for line in status.excerpt], "  ```", ""]
    else:
        lines.append("  - no log excerpt (logs expired or unreadable)")
    return lines


def scan(fleet: list[str], previous_body: str = "") -> list[RepoStatus]:
    """Collect the facts for every red repo in the fleet.

    Args:
        fleet: Repository slugs.
        previous_body: The previous issue body, for first-seen carry-over.

    Returns:
        One :class:`RepoStatus` per red repo, in FLEET order.
    """
    first_seen = parse_first_seen(previous_body)
    red: list[RepoStatus] = []

    for repo in fleet:
        branch = _gh("api", f"repos/{repo}", "--jq", ".default_branch", check=False)
        if not branch:
            continue
        runs = latest_ci_runs(repo, branch)
        latest = next((r for r in runs if r.get("conclusion")), None)
        if latest is None or latest.get("conclusion") not in RED_CONCLUSIONS:
            continue

        run_id = int(latest["id"])
        status = RepoStatus(
            repo=repo,
            branch=branch,
            conclusion=str(latest["conclusion"]),
            run_id=run_id,
            run_url=str(latest.get("html_url", "")),
            failed_steps=failed_steps(repo, run_id),
            excerpt=error_excerpt(repo, run_id),
            uses_canon_ci=uses_canon_ci(repo),
            same_sha_flip=detect_same_sha_flip(runs),
            first_seen=first_seen.get(repo, ""),
        )
        red.append(status)
        print(f"red: {repo} ({status.conclusion})", file=sys.stderr)  # noqa: T201

    return red


def _existing_issue(issue_repo: str, label: str) -> tuple[int | None, str]:
    """Find the open fleet-health issue and its body.

    Args:
        issue_repo: Repo that carries the issue.
        label: Label identifying it.

    Returns:
        ``(number, body)``; ``(None, "")`` when there is none.
    """
    raw = _gh(
        "issue",
        "list",
        "--repo",
        issue_repo,
        "--label",
        label,
        "--state",
        "open",
        "--json",
        "number,body",
        "--jq",
        ".[0]",
        check=False,
    )
    if not raw:
        return None, ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, ""
    return data.get("number"), data.get("body") or ""


def main(argv: list[str] | None = None) -> int:
    """Scan the fleet and create, update or close the fleet-health issue.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit status.
    """
    parser = argparse.ArgumentParser(description="Root-cause a red fleet.")
    parser.add_argument("--fleet", type=Path, default=Path("FLEET"))
    parser.add_argument("--issue-repo", default="")
    parser.add_argument("--label", default="fleet-health")
    parser.add_argument("--title", default="Fleet health: failing CI")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the rendered body instead of touching the issue.",
    )
    args = parser.parse_args(argv)

    today = datetime.now(tz=UTC).date().isoformat()
    number, previous = (
        _existing_issue(args.issue_repo, args.label) if args.issue_repo else (None, "")
    )

    statuses = scan(read_fleet(args.fleet), previous)

    if not statuses:
        if args.dry_run:
            print("Fleet green.")  # noqa: T201
        elif number is not None:
            _gh(
                "issue",
                "close",
                str(number),
                "--repo",
                args.issue_repo,
                "--comment",
                f"Fleet is green as of {today}.",
            )
        return 0

    body = render_issue(statuses, today)
    if args.dry_run:
        print(body)  # noqa: T201
        return 0

    if number is not None:
        _gh("issue", "edit", str(number), "--repo", args.issue_repo, "--body", body)
    else:
        _gh(
            "label",
            "create",
            args.label,
            "--repo",
            args.issue_repo,
            "--color",
            "D93F0B",
            check=False,
        )
        _gh(
            "issue",
            "create",
            "--repo",
            args.issue_repo,
            "--title",
            args.title,
            "--label",
            args.label,
            "--body",
            body,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
