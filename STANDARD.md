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

- **uv** for everything: env, lock (`uv.lock` committed), version, build,
  publish.
  CI installs with `--frozen`.
- Build backend: **uv_build**. Use its standard `src/` layout and avoid backend
  configuration unless the package genuinely needs non-default file inclusion.
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
- **pydoclint** 0.9.1 (google style) for docstring–signature consistency, which
  ruff's `D` rules don't check. CI runs it with `uvx` in an isolated environment
  because `pydoclint` and `docstring-parser` dependencies used by application
  packages can otherwise install conflicting `docstring_parser` modules.
- **pytest + coverage**; a coverage floor is set per repo (template question).
  Coverage is reported as a markdown table in the GitHub **job summary** of the
  run that produced it — `coverage report --format=markdown` into
  `$GITHUB_STEP_SUMMARY`, from `reusable-ci.yml`.
  - **No `--cov-report=html` or `=xml` in `addopts`.** They write `htmlcov/`
    and `coverage.xml` onto a runner that is about to be deleted, and into the
    working tree of anyone who runs the suite locally. Four fleet repos were
    doing one or both and no CI in any of them read the result. Ask for a file
    report explicitly when you want one.

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

## Naming

Names should explain the domain concept they hold or the responsibility they
perform. Prefer `category_probabilities` to `probs6`,
`surname_not_in_dictionary` to `ood`, and `calibration_reference` to a generic
`metadata` field. A reader should not have to trace assignments to decode an
ordinary variable.

- Use the same term for the same concept across a package's code, public API,
  schemas, tests, and documentation. When a package needs domain-specific
  vocabulary, choose it once and keep it stable.
- Include units, scale, or role when omission would be ambiguous. Distinguish
  probabilities from percentages, raw logits from calibrated probabilities,
  and training rows from test rows.
- Name booleans as predicates such as `is_valid`, `has_known_features`, or
  `script_supported`. Use plural nouns for collections, nouns for classes, and
  verb phrases for functions that perform work.
- Avoid single-letter names, unexplained abbreviations, type-only names such as
  `data` or `result`, and numeric suffixes that encode meaning. Short indices
  and symbols are acceptable in a small mathematical expression when they
  match the documented notation.
- Name modules and files for one clear responsibility. Do not accumulate
  unrelated code in catch-all modules such as `misc` or `helpers`.

Ruff's naming rules enforce syntax, not judgment. Reviewers remain responsible
for clarity and consistency, and a rename is warranted when a name is legal but
forces the reader to reconstruct its meaning.

## Runtime assets

- Small ordered model metadata such as vocabularies, class labels, feature
  configuration, and training manifests use schema-versioned **JSON**. Loaders
  validate the schema version, required keys, value types, null policy,
  uniqueness where applicable, and semantic invariants such as the position of
  an unknown token. JSON keeps these artifacts inspectable and diffable without
  adding a serialization runtime to every model package.
- Runtime tables shipped in a wheel or kept as a persistent package cache use
  **Parquet with an explicit Arrow schema**. Loaders read Parquet directly and
  tests assert the logical dtypes. Do not infer production types from CSV.
- Structured records that are not naturally tabular may use **Protobuf** with
  a checked-in schema. The requirement is a declared, testable schema, not one
  universal storage format.
- CSV and TSV remain valid at user-facing import or export boundaries, as test
  fixtures, and as raw external research inputs outside the import package.
  External CSV downloads are temporary inputs: validate them, normalize them
  to the declared schema, and persist only Parquet or Protobuf.
- Do not package CSV, compressed CSV or TSV, CSV hidden in an archive, or an
  opaque archive that the runtime must unpack. Compression does not add a
  schema.
- Learned model weights and serialized estimators do not belong in the Python
  wheel. Publish them under the fleet's Hugging Face organization, keep a
  model card with provenance and limitations, and resolve them using the full
  40-character Hugging Face commit SHA. Branch names, tags, abbreviated SHAs,
  and unpinned `from_pretrained` calls are not reproducible.
- Development can override the download cache or model directory with a
  package-specific environment variable. Production defaults remain pinned.

## Versioning and release

- **The project metadata is the version.** Set it with `uv version X.Y.Z` and
  commit the resulting `pyproject.toml` and `uv.lock` changes in the release
  PR. The package version is therefore stable in source archives and builds
  made without Git metadata.
- Tag the merged release commit `vX.Y.Z`. The release workflow rejects a tag
  that does not match the project version, then runs: build → PEP 740
  attestations → **PyPI trusted publishing** (OIDC, no tokens) → GitHub
  Release with generated notes.
- PyPI's trusted publishing does not support reusable workflows, so the
  publish job lives in each repo's `release.yml` shim (template-managed);
  build and GitHub Release stay in the reusable workflow. Configure the
  PyPI publisher with workflow `release.yml`, environment `pypi`.
- Migrate legacy PyPI publisher entries to workflow `release.yml` and
  environment `pypi`; delete old publishing workflows instead of preserving
  filename-specific exceptions. Repos with no publisher need one standard
  trusted-publisher entry before their first release.
- CHANGELOG is the generated release notes; curate in the GitHub Release when
  it matters.

## CI (via reusable workflows)

Every repo's workflows are thin shims calling this repo's reusable workflows:

- `ci.yml` → lint (ruff check + format, pyright, pydoclint), test matrix
  (floor + ceiling), **wheel validation** (build the wheel, run `twine check`,
  install it in a clean env, and run the test suite against the installed
  package), `zizmor` on workflow files, dependency review on PRs.
- `docs.yml` → sphinx build with `-W` and doctests, deploy to GitHub Pages
  on default-branch pushes.
- `release.yml` → tag-driven publish as above.

Repository-bound projects that are deliberately not installable distributions
set `run-wheel: false` in the CI shim. The wheel is still built and checked;
only installation and installed-package tests are skipped. This is an explicit
exception for data corpora and similar projects, not a way to bypass a failing
package build.

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

- Dependabot: weekly grouped patch/minor with 7-day cooldown. **Everything
  Dependabot opens is eligible to auto-merge; CI decides whether it lands.**
  The sweep merges only when every check is terminal and none failed.
  - This used to hold Python majors for a human, and — because grouped security
    PRs report the group name rather than `minor-and-patch` — held major
    *security* bumps too. That inverted the risk: the updates most worth
    landing fast were the least likely to merge. Measured before changing it:
    57 open Dependabot PRs across the fleet, 37 of them green and unmerged, and
    `in-rolls/indicate` sitting on eight dependencies with published security
    fixes.
  - What makes this safe is not that majors are harmless. It is that **releases
    are tag-driven**: a merged major reaches `main`, not users, and nothing
    ships until someone pushes `vX.Y.Z`. A bad merge is a revert, not a
    withdrawn release.
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
violations, invalid runtime assets, docstring gaps, dead links, CI-matrix
mismatch.

An entry in `FLEET` means py-canon monitors the repository. It does not mean
the repository has adopted the standard. Adoption means the repository records
Copier answers, uses the shared workflow and Sphinx layers, and passes
`preen check --strict`. The inventory must report those facts separately so a
listed but unmigrated package is never presented as conforming.

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
