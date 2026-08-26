# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Release tags match the version committed in `pyproject.toml`; the fleet-facing
`v1` tag is a moving pointer advanced by the promote workflow after green CI,
not a release of its own.

## [Unreleased]

### Fixed

- `scientific-python.org` joins `[tool.preen] link_ignore`. The three URLs
  `docs/gap-analysis.md` cites all answer 200 from a workstation, but from
  GitHub Actions egress the request hangs until lychee gives up — and which of
  them fails varies run to run, so every pull request in the repo was blocked
  by a link that is not broken.

## [1.2.0] - 2026-08-19

### Changed

- The Python floor moves to **3.12**: `requires-python = ">=3.12"` in the
  standard and the template, ruff `target-version = "py312"`, and
  `reusable-ci.yml`'s default matrix becomes `["3.12", "3.14"]`. Twenty-five
  fleet repos already declared 3.12 or higher and had to override
  `python-versions` by hand because the default's 3.11 leg could not resolve
  against their floor; that workaround is no longer needed.

## [1.1.1] - 2026-08-19

### Fixed

- The lint job runs `preen check --strict --skip tests`. Preen's `tests` check
  re-ran the whole pytest suite inside a job budgeted for linting, duplicating
  work the test matrix already does with coverage — and cancelling the lint job
  at its 15-minute timeout on repos with slow suites.

## [1.1.0] - 2026-08-19

### Changed

- `reusable-ci.yml` runs `preen check --strict` in the lint job by default
  (`run-preen` now defaults to true), and the template's ci.yml shim sets it
  explicitly. The Conformance section of STANDARD.md is now true by
  construction: every fleet repo passed the check before the flip.
- `FLEET` extended from 26 to 47 entries — every published PyPI package across
  the six orgs, ownership-verified by project URL or author email rather than
  name existence.
- Added the Naming standard (domain-meaningful names, one term per concept,
  boolean predicates) and the schema-versioned JSON rule for small ordered
  model metadata.

### Fixed

- Repository-bound projects still build their wheel and validate its metadata;
  `run-wheel: false` now skips only clean-environment installation and tests.

## [1.0.1] - 2026-08-17

### Added

- Fleet inventory (`docs/fleet-inventory.md`) and a comparison of this standard
  against sp-repo-review, SPEC 0 and OpenSSF Scorecard (`docs/gap-analysis.md`),
  both written before migrating repos rather than after.
- `fleet-health.yml`, a daily scan of every repo in `FLEET` that maintains a
  single issue listing repos with failing default-branch CI.
- Reusable Dependabot auto-merge, distributed by reference rather than copied
  into each repo.
- Operator tooling under `tools/`: fleet-wide file push, required-check
  repair, docs-concurrency repair, Dependabot backfill.
- A `gate` job in `reusable-ci.yml` giving rulesets one stable required
  context, so the test matrix can change without orphaning a required check.

### Changed

- Raised the `uv_build` floor to 0.12.5 and its compatibility ceiling to the
  next minor release in both py-canon and generated projects.
- Replaced Hatchling and tag-derived versions with native `uv_build` and
  versions managed by `uv version`; release builds now reject mismatched tags.
- Standardized on `release.yml` for PyPI trusted publishing instead of keeping
  legacy workflow filenames.
- `v1` is advanced only by the promote workflow after CI passes, never by hand.
- The release workflow publishes from the caller's `release.yml`, because PyPI
  trusted publishing cannot bind its OIDC claims to a reusable workflow.
- Expanded the ruff rule set and dropped the rules that conflict with
  `ruff format`.
- Auto-merge asks GitHub to merge rather than trusting `mergeStateStatus`, and
  never arms a PR where no check is required.
- License metadata moved to the PEP 639 form (`license = "MIT"` plus
  `license-files`), in this repo and in the template.

### Fixed

- A release no longer fails when the same version is published twice.
- Wheel tests keep the built wheel installed, install only the declared test
  dependencies, verify its import outside the checkout, and keep checkout
  source paths behind the installed artifact while running tests.
- CI and release tests install `dev` plus `test` when declared, ignore configured
  default groups, and avoid mutually exclusive operational environments.
- Repository-bound projects can explicitly disable wheel validation without
  weakening the default for distributable packages.
- Windows runners work: the test job pins bash.
- zizmor policy covers reusable-workflow subpath refs.

## [1.0.0] - 2026-07-22

### Added

- Initial release: the standard (`STANDARD.md`), reusable CI, docs and release
  workflows, the copier template, and shared Sphinx configuration
  (`py_canon.sphinx`).

[Unreleased]: https://github.com/gojiplus/py-canon/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/gojiplus/py-canon/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/gojiplus/py-canon/releases/tag/v1.0.0
