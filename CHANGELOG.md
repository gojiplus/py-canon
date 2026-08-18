# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Release tags match the version committed in `pyproject.toml`; the fleet-facing
`v1` tag is a moving pointer advanced by the promote workflow after green CI,
not a release of its own.

## [Unreleased]

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
