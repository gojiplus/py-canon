# What is actually in the fleet

Phase 3 of moving the Python packages onto py-canon: establish what "all my Python packages"
means before migrating anything.

## The numbers

Across six orgs — `gojiplus`, `finite-sample`, `appeler`, `in-rolls`, `notnews`, `themains` —
**282 non-archived, non-fork repositories**, of which **67 are packages** (65 carry a
`pyproject.toml`, 2 still use `setup.py`).

Adoption among those 67:

| state | repos |
|---|--:|
| canon CI **and** template-adopted | 17 |
| canon CI only | 2 |
| **neither** | **48** |

`FLEET` lists 26. All 26 appear in the scan, which is the check that matters — the scan is a
strict superset of the known-good list. But **41 packages are not in `FLEET` at all**, so the
file undercounts by roughly two-thirds.

The 48-repo backlog, by last push and by org:

| last push | repos | | org | repos |
|---|--:|---|---|--:|
| 2026 | 40 | | finite-sample | 21 |
| 2025 | 7 | | gojiplus | 12 |
| 2020 | 1 | | in-rolls | 6 |
| | | | appeler | 6 |
| | | | notnews | 3 |

Forty of the forty-eight were pushed this year, so this is a live backlog rather than an archive
of abandoned work.

## What the other 215 repos are

Not packages, and mostly should not be forced into a package standard:

| kind | how it was identified |
|---|---|
| scripts and analyses | no `pyproject.toml`, no `setup.py` |
| apps | `requirements.txt` and an entry point such as `app.py` |
| **GitHub Actions** | `action.yml` / `action.yaml` at the root |

The Actions are the interesting category. They are Python, they are maintained, and py-canon's
standard does not fit them — there is no wheel to build, nothing to publish to PyPI, and the
release workflow has nothing to release. **They are the first real candidates for an exemption
tier** rather than for migration.

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
| **adopt** | packages not yet on the standard | 48 |
| **complete** | on canon CI, template adoption not finished | 2 |
| **done** | canon CI and template | 17 |
| **exempt, with a reason** | GitHub Actions; anything whose CI the template would destroy | tbd |
| **out of scope** | scripts, analyses, apps | the remaining ~215 |

`rmcp` is the worked example for the exemption tier: it is a package, but its CI builds a Docker
image and runs R integration tests inside it, and the template's `ci.yml` would delete that.

## Before migrating

1. **Extend `FLEET` to all 67.** It drives `fleet-health.yml`, which currently watches 26 and is
   blind to 41 packages.
2. **Convert the two `setup.py` packages** — `finite-sample/pysum`, `appeler/clean-names` — to
   `pyproject.toml` first. `preen adopt` rewrites `[tool.*]` sections and assumes the file exists.
3. **Decide the exemption tier**, so a repo that cannot conform is recorded with a reason rather
   than sitting permanently red.

Batch smallest-first by org so an adoption bug costs one small repo rather than twenty. `appeler`
and `notnews` are the smallest backlogs and the natural place to find out what `preen adopt` gets
wrong on a mature repo.
