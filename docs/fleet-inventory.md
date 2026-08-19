# What is actually in the fleet

Phase 3 of moving the Python packages onto py-canon: establish what "all my Python packages"
means before migrating anything.

## The numbers

Across six orgs — `gojiplus`, `finite-sample`, `appeler`, `in-rolls`, `notnews`, `themains` —
**282 non-archived, non-fork repositories**. Of those, **67 carry packaging metadata** (65 a
`pyproject.toml`, 2 a `setup.py`) — but that is a weak test, and the number it produces is
misleading.

**Carrying a `pyproject.toml` does not make something a package.** Analyses and apps use one to
pin dependencies. The load-bearing question is whether the thing is *published*:

| | repos |
|---|--:|
| on PyPI — real, distributed packages | **47** |
| packaging metadata but not published | 20 |

Several of the 20 are plainly not packages on inspection: `in-rolls/assam_elex_rolls_2026` is
electoral-roll dataset work, `notnews/bench_marks` is a benchmark harness. Others are genuine
packages that simply have not shipped yet — `gojiplus/py-canon` and `finite-sample/simcheck`
among them. The 20 need a per-repo judgement rather than a rule.

Adoption among the **47 published** packages:

| state | repos |
|---|--:|
| on canon CI | 15 |
| **not on canon CI — the backlog** | **32** |

`FLEET` lists 26. All 26 appear in the scan, which is the check that matters — the scan is a
strict superset of the known-good list. But it mixes published and unpublished repos and misses
many of the 47.

The 32-repo backlog is live rather than abandoned: the overwhelming majority were pushed this
year, and it is concentrated in `finite-sample` and `gojiplus`.

## What the other 215 repos are

Not packages, and mostly should not be forced into a package standard:

| kind | how it was identified |
|---|---|
| scripts and analyses | no `pyproject.toml`, no `setup.py` |
| apps | `requirements.txt` and an entry point such as `app.py` |
| **GitHub Actions** | `action.yml` / `action.yaml` at the root |

The Actions are worth naming separately. They are Python and maintained, but there is no wheel to
build and nothing to publish, so the package standard simply does not apply. **Out of scope, not
exempt** — an earlier draft called them exemption-tier candidates, which implied they were things
we had decided not to migrate. They were never migration candidates.

The exemption tier is for a narrower thing: repos that genuinely *are* published packages but
cannot take the standard. `finite-sample/rmcp` is the case — its CI builds a Docker image and runs
R integration tests inside it, and the template's `ci.yml` would delete that.

Confirmed 2026-08-18: rmcp stays in FLEET for monitoring and keeps its own CI as the gate. The
exemption is recorded machine-readably in rmcp's `[tool.preen] skip_checks` (with rationale in
place), so a stray `preen check` there reports the decision instead of drift.

### Five more that are not packages, despite carrying pyproject.toml

Found by checking for importable code rather than trusting the manifest:

| repo | declared name | what it is |
|---|---|---|
| `appeler/clean-names` | clean-names | two loose scripts; `find_packages()` returns nothing, so its `setup.py` builds an empty distribution. Not on PyPI under any spelling. Untouched since 2020 |
| `in-rolls/mplads` | mplads | MPLADS analysis. `analysis/00_descriptive_stats.py`, `01_merge_election_mplads.py` — a numbered research pipeline. Declares `packages = ["src"]`, but `src/` has no `__init__.py` |
| `in-rolls/pai` | pai-scraper | Panchayat Advancement Index scraper — `scripts/pai_scraper_resumable.py` |
| `notnews/bench_marks` | rule-of-blah | judicial-coverage analysis — `scripts/analyze_judicial_coverage.py` |
| `finite-sample/ensemble-proximity` | stable-selection | no importable package; declared name does not match the repo |

Each has **zero `__init__.py`**. The `pyproject.toml` is doing dependency management for `uv`, not
declaring something to distribute. Scraping and academic work, correctly out of scope.

## Two errors in producing this, and how they surfaced

Recorded because the method gets reused on the next fleet, and both mistakes produced clean,
plausible-looking tables.

**1. Filtering on GitHub's language field dropped real packages.** The first scan selected repos
where `primaryLanguage == "Python"`, giving a tidy 103. That field is a byte count. Nine packages —
`naampy`, `pranaam`, `parsernaam`, `instate`, `naamkaran`, `outkast`, `piedomains`, `notnews`,
`indicate` — are classified as **Jupyter Notebook**, because notebooks outweigh their source. All
nine are in `FLEET` and several already run canon CI. The filter silently excluded them.

Caught by cross-checking the scan against `FLEET` and finding 9 entries missing. Had that check
been skipped, the migration would have covered 51 repos and quietly omitted some of the
most-used packages in the fleet.

*The lesson worth keeping: `primaryLanguage` is a heuristic about bytes. Whether a repo is a
package is answered by `pyproject.toml`, and nothing else.*

**2. The file-existence test reported every repo as a package.** The second scan used
`gh api repos/X/contents/pyproject.toml --jq '.sha'` and tested whether the output was empty. On a
404, `gh` writes the error body to stdout and does not apply `--jq`, so the variable held
`{"message":"Not Found",...}` — non-empty for every repo. Result: "282 of 282 are packages".

Caught because the answer was absurd, not because the code looked wrong. The reliable test is
`gh api ... >/dev/null 2>&1` and the **exit code**, which is what the earlier adoption scan in the
same session had used correctly.

Both errors shared a shape: a confident table, internally consistent, wrong. Neither was found by
re-reading the code. Both were found by checking the output against something already known to be
true.

## Proposed tiers

| tier | what | count |
|---|---|--:|
| **adopt** | published packages not yet on canon CI | **32** |
| **done** | published and on canon CI | 15 |
| **judgement needed** | importable, not published — ship them or reclassify | 15 |
| **exempt, with a reason** | published packages the template would break — `rmcp` | 1 known |
| **out of scope** | scripts, analyses, apps, Actions, and the 5 above | ~220 |

The 32 is the real backlog. Everything else is either already done, a decision about whether a
thing is meant to be published at all, or not a package.

## Before migrating

1. **Extend `FLEET` to the 47 published packages.** It drives `fleet-health.yml`, which currently
   watches 26 — a list that mixes published and unpublished repos.
2. **Convert `finite-sample/pysum` off `setup.py`.** Done (`finite-sample/pysum#1`); `preen adopt`
   rewrites `[tool.*]` sections and assumes `pyproject.toml` exists. `appeler/clean-names` was the
   other `setup.py` repo and is **not** being converted — it has no importable code to package.
3. **Decide the exemption tier**, so a repo that cannot conform is recorded with a reason rather
   than sitting permanently red.
4. **Settle the 15 unpublished-but-importable repos.** Each is either a package that has not
   shipped yet or an analysis wearing packaging metadata, and only you can say which.

Batch smallest-first by org so an adoption bug costs one small repo rather than twenty. `appeler`
and `notnews` are the smallest backlogs and the natural place to find out what `preen adopt` gets
wrong on a mature repo.
