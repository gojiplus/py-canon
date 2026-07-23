# py-canon

One versioned standard for the fleet's Python packages, plus the machinery
that keeps ~50 repos on it without copy-paste.

**The standard:** [STANDARD.md](STANDARD.md) — uv, ruff, pyright, google
docstrings, src/ layout, tag-driven releases with trusted publishing,
Sphinx+furo docs. Changing the fleet's mind is a PR to that file and the
assets here.

**The machinery (DRY hierarchy — reference where possible, materialize only
where forced):**

| Layer | Lives here as | Repos consume it via | Propagation |
|---|---|---|---|
| CI / docs / release logic | `.github/workflows/reusable-*.yml` | 5-line workflow shims `uses: gojiplus/py-canon/.github/workflows/reusable-ci.yml@v1` | Instant on next run |
| Sphinx config | `src/py_canon/sphinx.py` | `docs/conf.py`: `from py_canon.sphinx import configure; configure(globals())` | Next docs build |
| Scaffolding + `[tool.*]` config | `template/` (copier) | `preen new` / `preen adopt`, reconciled by `copier update` PRs | Auto-merged PRs |

**Conformance:** [`preen`](https://github.com/gojiplus/preen) runs
`check --strict` in every repo's CI and fails on drift from the standard.

## Adopting a repo

```bash
uvx preen adopt          # retrofit an existing package
uvx preen new mypackage  # scaffold a new one
```

## Versioning

Tags (`v1.2.3`) version everything at once: the reusable workflows, the
template, and the `py-canon` package. Repos reference the moving major tag
`v1`; breaking changes to the standard bump to `v2`.
