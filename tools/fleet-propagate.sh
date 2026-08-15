#!/usr/bin/env bash
# Push a py-canon change into the repos that cannot pick it up on their own.
#
# The README lists three propagation paths. Only one of them actually runs
# without help, and the difference is worth stating because it decides what
# this script is for:
#
#   reusable workflows   `uses: ...@v1`. The promote job advances v1 after green
#                        CI, so the next run of a caller's workflow already has
#                        the change. Nothing to do here.
#
#   py_canon.sphinx      A git dependency on `@v1`. The *pyproject* reference
#                        moves, but `uv.lock` freezes the commit it resolved to
#                        -- gojiplus/sharepack's lock still pins 3ebfb1cd while
#                        v1 has moved on. Docs build against whatever the lock
#                        says, so this needs a lock refresh to land.
#
#   template/            Copier scaffolding and the [tool.*] config. Nothing
#                        runs `copier update`, so a template change reaches new
#                        repos via `preen new`/`preen adopt` and reaches
#                        existing ones never.
#
# So: refresh the pinned py-canon commit, and open a PR where that produced a
# diff. `copier update` is opt-in rather than default -- see COPIER below for
# what running it actually did.
#
# Local rather than a workflow because GITHUB_TOKEN cannot write to other
# repositories and this repo has no PAT -- the same constraint
# fleet-push-file.sh documents.
#
# usage: fleet-propagate.sh [<owner/repo> ...]     (default: everything in FLEET)
# DRY_RUN=1 (the default) prints each diff and opens nothing.
# COPIER=1 also attempts `copier update` -- read the warning above it first.
set -euo pipefail

DRY=${DRY_RUN:-1}
BRANCH=${BRANCH:-fleet/propagate-py-canon}
FLEET_FILE=${FLEET_FILE:-$(dirname "$0")/../FLEET}

if [ "$#" -gt 0 ]; then
  repos=("$@")
else
  repos=()
  while read -r line; do
    [ -n "$line" ] && repos+=("$line")
  done < "$FLEET_FILE"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

for repo in "${repos[@]}"; do
  name=${repo##*/}
  dir="$work/$name"
  if ! gh repo clone "$repo" "$dir" -- --depth 1 >/dev/null 2>&1; then
    printf '== %-32s clone failed\n' "$repo"
    continue
  fi
  cd "$dir"

  did=""

  # COPIER=1 opts in to `copier update`. It is off by default, and both reasons
  # were found by running it:
  #
  #   It usually does nothing. Repos record `_commit: v1`, and v1 is a *moving*
  #   tag. copier compares the recorded ref against the requested one, sees
  #   "v1" both times, and reports "Keeping template version 1" without looking
  #   at what v1 now points to.
  #
  #   When it does act, it can delete. On appeler/naamkaran it removed
  #   `Citation.cff` and left no citation file at all: the repo spells it
  #   `Citation.cff`, the template ships `CITATION.cff`, and on a
  #   case-insensitive filesystem the render collided with the existing file.
  #   An auto-merged copier PR would have taken the file with it.
  #
  # copier must also run first and on a clean tree -- it refuses outright once
  # the lock refresh below has touched anything.
  if [ "${COPIER:-0}" = "1" ] && [ -f .copier-answers.yml ]; then
    out=$(uvx copier update --trust --defaults --vcs-ref=v1 2>&1) || true
    if printf '%s' "$out" | grep -q "dirty; cannot continue"; then
      printf '== %-32s copier refused: working tree not clean\n' "$repo"
    fi
    if ! git diff --quiet; then
      did="template"
      if git diff --name-status | grep -q '^D'; then
        printf '== %-32s WARNING: copier deletes files here -- read the diff\n' "$repo"
      fi
    fi
  fi

  # The lock pins the commit `@v1` resolved to, so the tag moving is not enough
  # on its own -- docs build against whatever the lock says. This is the part
  # that reliably has something to do: gojiplus/sharepack was pinned to
  # 3ebfb1cd, eighteen commits behind the v1 it claims to track.
  if grep -q "py-canon" uv.lock 2>/dev/null; then
    if uv lock --upgrade-package py-canon >/dev/null 2>&1; then
      git diff --quiet uv.lock || did="${did:+$did+}lock"
    fi
  fi

  if [ -z "$did" ]; then
    printf '== %-32s already current\n' "$repo"
    cd - >/dev/null
    continue
  fi

  printf '== %-32s %s\n' "$repo" "$did"
  git --no-pager diff --stat
  git --no-pager diff

  if [ "$DRY" = "1" ]; then
    cd - >/dev/null
    continue
  fi

  git checkout -b "$BRANCH" >/dev/null 2>&1
  git add -A
  git commit -qm "chore: pick up the current py-canon

Opened by tools/fleet-propagate.sh in gojiplus/py-canon: a lock refresh for the
pinned py-canon commit, a \`copier update\` against the template at v1, or both.

Nothing here was hand-written. If the result is wrong, the fix belongs in the
template rather than in this diff."
  git push -q -u origin "$BRANCH"
  gh pr create --repo "$repo" --head "$BRANCH" \
    --title "chore: pick up the current py-canon" \
    --body "Opened by \`tools/fleet-propagate.sh\` in gojiplus/py-canon.

The \`@v1\` reusable workflows update themselves; these two layers do not:

- **\`uv.lock\`** pins the *commit* \`py-canon@v1\` resolved to, so docs keep
  building against an old \`py_canon.sphinx\` until the lock is refreshed.
- **\`copier update\`** is what carries template changes -- scaffolding and the
  shared \`[tool.*]\` config -- into a repo that already exists. Nothing runs it
  on a schedule.

Nothing here was hand-written. If the result is wrong, the fix belongs in the
template rather than in this diff."
  cd - >/dev/null
done
