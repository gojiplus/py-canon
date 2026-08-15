# The Standard

Current best practice for Python packages in this fleet. This document is
versioned: changing our collective mind is a PR here, and the machinery
(reusable workflows, the copier template, `py_canon.sphinx`) propagates the
change to every adopted repo.

**DRY hierarchy — reference where possible, materialize only where forced:**

| Layer | Mechanism | Propagation |
|---|---|---|
| CI / docs / release logic | Reusable workflows in this repo, called via `@v1` | Instant — next workflow run |
| Sphinx configuration | `from py_canon.sphinx import configure` (git dependency) | Next docs build after lock refresh |
| pre-commit hooks | Standard config referencing upstream hooks | `pre-commit autoupdate` / template sync |
| pyproject `[tool.*]`, scaffolding | Copier template (`template/`) | `copier update` PRs, landed by guarded auto-merge |

## Toolchain

- **uv** for everything: env, lock (`uv.lock` committed), build, publish.
  CI installs with `--frozen`.
- Build backend: **hatchling + uv-dynamic-versioning** — chosen over
  `uv_build` because tag-derived versions (below) require a plugin-capable
  backend, and no-bump-commits outranks backend minimalism.
- **ruff** is the only linter and formatter. Line length 88. Lint select:
  `E, W, F, I, B, C4, UP, N, D, S, SIM, T20, PT, RUF, PTH, RET, PIE, FURB,
  PERF, DTZ, LOG, G, TC, FLY, RSE, SLOT, FA, A, EXE, ICN, PGH, PLE, ARG,
  SLF` — about half of ruff's stable rules, skewed to the auto-fixable ones;
  pydocstyle convention **google**. `ARG`/`SLF` are off under `tests/**`.
  Ignored: `D203, D213` (the google convention's own exclusions) and
  `W191, D206, D300` (ruff documents these as always incompatible with
  `ruff format`, which this standard also runs). No black, no isort, no
  flake8.
  Framework-specific sets (`PD`, `NPY`, `ASYNC`, `DJ`, …) are deliberately
  left to individual repos; `preen adopt` preserves a repo's own
  `extend-select` rather than overwriting it.
- **pyright** (`standard` mode) is the only type checker. No mypy.
- **pydoclint** (google style) for docstring–signature consistency, which
  ruff's `D` rules don't check.
- **pytest + coverage**; a coverage floor is set per repo (template question).

## Python support

- `requires-python = ">=3.11"`, **no upper bound**.
- CI tests the floor and the ceiling (3.11 and 3.14 today).
- The floor advances roughly yearly (SPEC-0 spirit); advancing it is a change
  to this document + the template.

## Package layout

- **`src/` layout**; `py.typed` shipped; `tests/` and `docs/` at repo root.
- `__version__` via `importlib.metadata` — no version strings in source.
- Dependency layout: **dependency-groups** (`dev`, `test`, `docs`) for
  development concerns; **extras only for user-facing optional features**.
- Every repo has: `LICENSE` (MIT), `CITATION.cff`, `README.md` with the
  standard badge row, `.pre-commit-config.yaml`, `.github/dependabot.yml`
  (guarded auto-merge policy).

## Versioning and release

- **The git tag is the version.** `uv-dynamic-versioning` derives the package
  version from the latest `v*` tag. No bump commits, no version edits.
- Pushing tag `vX.Y.Z` runs the release workflow: build → PEP 740
  attestations → **PyPI trusted publishing** (OIDC, no tokens) → GitHub
  Release with generated notes.
- PyPI's trusted publishing does not support reusable workflows, so the
  publish job lives in each repo's `release.yml` shim (template-managed);
  build and GitHub Release stay in the reusable workflow. Configure the
  PyPI publisher with workflow `release.yml`, environment `pypi`.
- **Legacy publishers**: repos whose PyPI publisher predates adoption and
  is keyed to an old workflow filename (e.g. indicate:
  `python-publish.yml`, environment `pypi`) strip the publish job from
  `release.yml` and keep a standalone publish workflow under the legacy
  filename with exactly the claims the publisher expects — no pypi.org
  change needed. Caveat: a future `copier update` restores the template
  `release.yml` (with its embedded publish job) — drop that job again
  when reconciling, or migrate the publisher config to `release.yml`/
  `pypi` and delete the legacy file. Repos with **no publisher at all**
  (some were only ever uploaded manually — check the project's publish
  run history before assuming) use the standard layout and need one
  publisher entry created: workflow `release.yml`, environment `pypi`.
- CHANGELOG is the generated release notes; curate in the GitHub Release when
  it matters.

## CI (via reusable workflows)

Every repo's workflows are thin shims calling this repo's reusable workflows:

- `ci.yml` → lint (ruff check + format, pyright, pydoclint), test matrix
  (floor + ceiling), **wheel test** (build the wheel, install it in a clean
  env, run the test suite against the installed package, `twine check`),
  `zizmor` on workflow files, dependency review on PRs.
- `docs.yml` → sphinx build with `-W` and doctests, deploy to GitHub Pages
  on default-branch pushes.
- `release.yml` → tag-driven publish as above.

Workflow hygiene baked into the shims/reusables: top-level
`permissions: contents: read`, `concurrency` cancel-in-progress,
`timeout-minutes` on every job.

## Docs

- Sphinx + **furo** + MyST, napoleon (google), autosummary, intersphinx,
  copybutton. Configuration comes from `py_canon.sphinx.configure()` — a repo's
  `docs/conf.py` is ~2 lines.
- Version and metadata are read from `pyproject.toml`; never hardcoded.
- Docstrings: **google style** on all public callables, enforced by ruff `D`
  + pydoclint.

## Repo operations

- Dependabot: weekly grouped patch/minor with 7-day cooldown; guarded
  auto-merge (Python majors are the only human-reviewed updates). A major
  *security* bump is also held for a human — grouped security PRs report the
  group name, not `minor-and-patch`, so they do not clear the gate.
- **Auto-merge never approves.** `gh pr review --approve` fails outright where
  an org disallows Actions approving PRs, and `run:` uses `bash -e`, so the
  merge line never executes. The rulesets require status checks, not reviews,
  so the approval bought nothing. GitHub's documented example does not approve
  either. Do not add it back.
- **Pages concurrency is keyed on the ref.** A constant `group: pages` on a
  workflow that also triggers on `pull_request` is a trap: GitHub cancels the
  pending run in a group when a new one queues, so a burst of Dependabot PRs
  leaves all but the last with no `build` check. If `build` is required, those
  PRs are blocked forever with no visible failure. Use
  `group: docs-${{ github.ref }}`.
- **Required contexts must match the contexts CI actually emits.** Naming
  matrix legs in a ruleset means the next matrix change silently orphans a
  required check and blocks every PR. Require the aggregate `ci / gate`
  context instead. Audit with `tools/dependabot_backfill.py`, which reports
  contexts that never report on any open PR.
- Ruleset on the default branch: CI checks required for PR merges; repo-admin
  bypass for direct pushes.
- A repo with **no** ruleset must not have auto-merge enabled: auto-merge with
  nothing required is a merge button on a timer.
- Org-level `.github` repos carry community health files (SECURITY.md,
  CONTRIBUTING.md, issue templates).
- Repo metadata (description, topics, homepage → docs URL) is set at adoption.

## Conformance

`preen check --strict` runs in CI (part of the lint job) and fails a repo that
drifts from the standard: template drift, stale generated files, structure
violations, docstring gaps, dead links, CI-matrix mismatch.

## CI failure playbook

Prevention: canon changes are gated by canon's own CI (including a
template-consumer smoke test and actionlint); the fleet-facing `v1` tag is
advanced **only by the promote workflow after green CI** — never by hand.
Breaking standard changes go to `v2`, not a mutated `v1`.

Detection: every repo's CI runs weekly on cron (dormant repos surface
ecosystem drift); the daily **fleet-health** workflow here scans every repo in
`FLEET` and maintains a single "Fleet health" issue.

Triage: that issue is not a list of names. `py_canon.fleet_triage` also
records, per red repo, the failed job and step, an excerpt of the error lines,
how long it has been red, and an origin — from two rules, not a guess:

- *Did unchanged code go red?* An earlier green run on the same `head_sha`
  means the cause is outside the repo. This is what the weekly cron is for.
- *Is more than one repo failing the same step?* One repo failing a shared
  check is usually its own code; several at once is canon's.

It also correlates across repos — a step or a hostname appearing in two or
more failures at once. That is the only view in which a shared dependency
breaking looks like one problem instead of five unrelated ones.

The origin is a rule, not a diagnosis. It is right often enough to route the
work and always shows the evidence it used.

Response, by origin:
1. **Canon-caused** (reusable workflow / template bug): fix in canon; the
   promote workflow moves `v1`; the fleet heals with zero per-repo commits.
   **One PR, here — never a pull request per repo.**
2. **Ecosystem-caused** (action major, runner image, tool release): if it
   lives in a shared workflow, same as (1). If per-repo, dependabot config.
3. **Repo-specific** (real test failure): fix in the repo. Where the failing
   step is a conformance check, `tools/fleet-open-fix-pr.sh <repo>` opens the
   `preen fix` PR for you; where it is not, that script refuses and
   `--file-issue` hands the maintainer the triage instead. A patch nobody
   diagnosed costs a reviewer more than the red build it replaces.

Rollback (fleet-wide undo): `git push -f origin <last-good-sha>:refs/tags/v1`.
