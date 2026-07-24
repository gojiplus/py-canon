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
  `E, W, F, I, B, C4, UP, N, D, S, SIM, T20, PT, RUF`; pydocstyle convention
  **google**. No black, no isort, no flake8.
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
  auto-merge (Python majors are the only human-reviewed updates).
- Ruleset on the default branch: CI checks required for PR merges; repo-admin
  bypass for direct pushes.
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
ecosystem drift); the daily **fleet-health** workflow here scans every repo
in `FLEET` and maintains a single "Fleet health" issue listing red repos.

Response, by origin:
1. **Canon-caused** (reusable workflow / template bug): fix in canon; the
   promote workflow moves `v1`; the fleet heals with zero per-repo commits.
2. **Ecosystem-caused** (action major, runner image, tool release): if it
   lives in a shared workflow, same as (1). If per-repo, dependabot config.
3. **Repo-specific** (real test failure): fix in the repo.

Rollback (fleet-wide undo): `git push -f origin <last-good-sha>:refs/tags/v1`.
