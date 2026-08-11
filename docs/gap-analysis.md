# py-canon against outside practice

Written before migrating ~103 Python repos onto this standard, on the principle that the standard
should settle first — moving a fleet twice is the expensive mistake.

Compared against three external references, chosen because each covers something the others do
not:

- **[sp-repo-review](https://github.com/scientific-python/repo-review)** — the closest analogue to
  `preen`: a checker that runs rules against a repository, from the
  [Scientific Python Development Guide](https://learn.scientific-python.org/development/).
- **[SPEC 0](https://scientific-python.org/specs/spec-0000/)** — the ecosystem's written policy on
  how long to support a Python version. Followed by NumPy, SciPy, pandas, scikit-learn.
- **[OpenSSF Scorecard](https://github.com/ossf/scorecard)** — 19 supply-chain checks, the common
  vocabulary for "is this repo safe to depend on".

Every external claim links to its source. Every claim about this repo names a file and line.

---

## 1. Where py-canon is behind

### 1.1 Python floor — a choice to record, not a gap to close

`STANDARD.md:43` requires `requires-python = ">=3.11"` with no upper bound, and `:44` says CI
tests floor and ceiling, "3.11 and 3.14 today".

[SPEC 0](https://scientific-python.org/specs/spec-0000/) — the policy NumPy, SciPy, pandas and
scikit-learn follow — says a project may stop supporting a Python version **three years after it
was released**. The cost it is trading away is real: every supported version is another CI leg and
another set of language features you cannot use.

| version | released | may be dropped under SPEC 0 |
|---|---|---|
| 3.11 | Oct 2022 | Oct 2025 — passed |
| 3.12 | Oct 2023 | Oct 2026 |
| 3.13 | Oct 2024 | Oct 2027 |
| 3.14 | Oct 2025 | Oct 2028 |

**SPEC 0 grants permission, not an obligation.** So supporting 3.11 today is not a defect — it is
a deliberate choice to serve users the wider ecosystem has stopped serving. An earlier draft of
this document called the floor "stale" and recommended raising it; that framing was wrong, and it
is corrected here rather than quietly deleted.

**The decision: keep `>=3.11` as the requirement, prefer 3.12.** That distinction needs to survive
into the files, or it will be re-argued every time someone reads SPEC 0:

- `requires-python = ">=3.11"` stays the floor a repo must not go below.
- 3.12 becomes what the **template generates** for a new package, and what the docs recommend
  when a maintainer has no reason to support 3.11.
- The CI matrix keeps testing the floor and the ceiling, because the floor is what is promised.

Measured across the 26 repos in `FLEET`, this is also the lower-friction reading:

| declared `requires-python` | repos |
|---|--:|
| `>=3.11` | **18** |
| `>=3.12` | 6 |
| `>=3.13` | 2 |

Had the floor been raised, **18 of 26 would have gone non-conformant** until each bumped — a large
bill for a change nobody needed. Recording a preference costs nothing and leaves those 18 alone,
while the 8 already at 3.12+ show the fleet moving that way of its own accord.

**Recommend: no `v2` needed.** Preference is documentation; only a raised floor would have been
breaking.

### 1.2 pre-commit is shipped but never checked

The template ships `template/.pre-commit-config.yaml`, so every adopted repo starts with hooks.
Nothing in `preen` ever looks at it again — there is no pre-commit check among the nineteen
(`preen/src/preen/checks/`).

sp-repo-review makes this `PY006` ("Has pre-commit config") and devotes a whole family (`PC1xx`)
to *which* hooks are configured.

Shipped-but-unenforced is worse than absent: it will rot silently in adopted repos and nobody
finds out, which is the same failure mode as a workflow that reports green while doing nothing.

**Recommend: adopt a minimal version** — check the file exists and parses. Checking *which* hooks,
as sp-repo-review does, is redundant here: ruff, pyright, pydoclint and codespell already run in
`reusable-ci`, and hooks are a convenience rather than the gate.

### 1.3 No task runner

sp-repo-review `PY007`: "Supports an easy task runner (nox, tox, pixi, etc.)". py-canon has none —
contributors run `uv run pytest`, `uv run ruff` and so on by hand, and the canonical list of what
CI does lives only in `reusable-ci.yml`.

**Recommend: consider, do not rush.** The gap is real but small: `uv run` already gives one entry
point, and a runner's main benefit — reproducing CI locally — could be had more cheaply. Worth a
decision, not a default.

### 1.4 Presence checks that sp-repo-review has and preen does not

`preen`'s `structure` check only validates that `tests/` and `examples/` sit at the repo root
rather than inside the package (`preen/src/preen/checks/structure.py`). It does not check that
README, `docs/` or `.gitignore` exist — sp-repo-review's `PY002`, `PY004`, `PY008`.

**Recommend: adopt, cheaply.** These are near-free and catch the genuinely broken repo. Low value
on repos generated from the template, real value on the ~77 untracked repos about to be assessed.

### 1.5 No SECURITY.md

Scorecard's `Security-Policy`. `STANDARD.md:135` delegates this to org-level `.github` repos,
which is legitimate — GitHub falls back to the org default. But nothing verifies the fallback
exists for all six orgs.

**Recommend: verify once per org, not per repo.** A per-repo check would be noise.

---

## 2. Where py-canon is ahead

Worth recording so it is not "simplified" away later.

| area | py-canon | outside |
|---|---|---|
| Action pinning | every `uses:` is a commit SHA with a version comment, Dependabot configured for both `/` and `/template` | Scorecard `Pinned-Dependencies` scores this; sp-repo-review does not check it |
| Workflow security lint | `zizmor --min-severity high` on every PR (`reusable-ci.yml:126`) | sp-repo-review `SEC001` recommends it; Scorecard covers the same ground via `Dangerous-Workflow` |
| Release signing | PEP 740 attestations (`reusable-release.yml:100`, `template/.github/workflows/release.yml:59`) | Scorecard `Signed-Releases` |
| Token scope | `permissions: {}` at workflow level, granted per job | Scorecard `Token-Permissions` |
| Dependency updates | Dependabot plus a scheduled sweep that re-arms silently disarmed PRs | Scorecard `Dependency-Update-Tool` checks only that a tool exists |
| Tests before publish | `run-tests` defaults true in `reusable-release.yml`, against the exact commit being tagged | neither reference requires this |
| Dead links, citation, changelog, dependency tree | `preen` checks all four | sp-repo-review checks none |

The last row is the interesting one: `preen` is stronger on *documentation and metadata hygiene*,
sp-repo-review is stronger on *developer-workflow scaffolding*. They are not competing
implementations of the same idea.

---

## 3. Deliberately different — recorded so it is not relitigated

**Rulesets require status checks, not reviews** (`STANDARD.md:117`). Scorecard's `Code-Review`
would score this poorly. It is deliberate: these are largely single-maintainer repos, and
requiring review would mean either self-approval theatre or a permanently blocked queue. The
compensating control is that CI is required and auto-merge never approves (`STANDARD.md:115`).

**No fuzzing** (Scorecard `Fuzzing`). Appropriate for libraries parsing untrusted input; most of
this fleet does not.

**`Contributors` (two or more organisations)** is unreachable for a personal fleet and should not
be treated as a gap.

---

## 4. What this implies for the migration

1. **The floor is settled and does not block anything.** `>=3.11` stays; 3.12 is a preference
   expressed in the template and the docs. Nothing to sequence around, and no repo goes
   non-conformant.
2. **Add the cheap presence checks before the inventory**, not after. They are exactly what
   distinguishes a real package from an abandoned script among the ~77 untracked repos.
3. **Do not add a task runner as part of the migration.** It is the one item here that is a
   genuine question rather than a gap, and bundling it would make the migration hostage to a
   design debate.

## Method, and its limits

sp-repo-review's checks were read from source
(`scientific-python/cookie:src/sp_repo_review/checks/`) rather than from documentation, because
the published page lists the ID prefixes without describing them. Scorecard's list came from its
README. SPEC 0 dates are from the SPEC itself.

**Not covered:** pyOpenSci's peer-review criteria, which target packages seeking their review
badge and are broader than CI (they assess documentation quality and community fit). Worth a
separate pass if any of these packages pursue that.

**Measured, not assumed:** the `requires-python` table in §1.1 comes from reading
`pyproject.toml` in all 26 `FLEET` repos, not from inference. A first pass at counting it was
wrong — the pattern matched every version string on the line rather than one floor per repo, and
summed to 32 across 26 repos. The corrected figures are one floor per repo and sum to 26.

**Not measured:** the same for the ~77 repos outside `FLEET`. They are the subject of the
inventory phase and will shift these proportions.
