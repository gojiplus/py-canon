# py-canon

One versioned standard for the fleet's Python packages, plus the machinery
that keeps ~50 repos on it without copy-paste.

**The standard:** [STANDARD.md](STANDARD.md) covers uv, ruff, pyright, google
docstrings, src layout, typed runtime assets, Hugging Face model storage,
tag-driven releases with trusted publishing, and Sphinx with furo. Changing
the fleet's mind is a PR to that file and the assets here.

**The machinery (DRY hierarchy — reference where possible, materialize only
where forced):**

| Layer | Lives here as | Repos consume it via | Propagation |
|---|---|---|---|
| CI / docs / release logic | `.github/workflows/reusable-*.yml` | 5-line workflow shims `uses: gojiplus/py-canon/.github/workflows/reusable-ci.yml@v1` | Instant on next run |
| Sphinx config | `src/py_canon/sphinx.py` | `docs/conf.py`: `from py_canon.sphinx import configure; configure(globals())` | Next docs build |
| Scaffolding + `[tool.*]` config | `template/` (copier) | `preen new` / `preen adopt` | **New repos only — see below** |

The first two rows are references: a workflow shim names `@v1` and GitHub
resolves it at run time, and `docs/conf.py` imports `py_canon.sphinx`. Nothing
is copied, so moving `v1` is the whole propagation mechanism.

The third row cannot work that way. `[tool.ruff]` has to live in *your*
`pyproject.toml` and `.pre-commit-config.yaml` in *your* repo — those tools read
their own file and have no way to extend a URL. So the bytes are materialized
into each repo, and materialized bytes drift.

Two known gaps, both measured rather than assumed:

- **The Sphinx layer goes stale.** `py-canon @ git+...@v1` moves, but `uv.lock`
  freezes the commit it resolved to. `gojiplus/sharepack` was pinned eighteen
  commits behind the `v1` it claims to track. A lock refresh is what lands it.
- **`copier update` does not currently propagate anything.** Repos record
  `_commit: v1`, a *moving* tag, so copier compares `v1` against `v1` and
  reports "Keeping template version 1". When it does act it can delete: on
  `appeler/naamkaran` it removed `Citation.cff` outright, because the repo
  spells it that way and the template ships `CITATION.cff`.

`tools/fleet-propagate.sh` does the lock refresh across the fleet and puts
`copier update` behind an opt-in flag.

**Conformance:** [`preen`](https://github.com/gojiplus/preen) runs
`check --strict` in every repo's CI and fails on drift from the standard.

## Adopting a repo

```bash
uvx preen adopt          # retrofit an existing package
uvx preen new mypackage  # scaffold a new one
```

## Versioning

`uv version X.Y.Z` updates the `py-canon` package version. A matching
`vX.Y.Z` tag releases that version. Repos reference the moving major tag `v1`
for reusable workflows and the template; breaking changes to the standard
bump that tag to `v2`.
