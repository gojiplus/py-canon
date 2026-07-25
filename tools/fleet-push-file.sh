#!/usr/bin/env bash
# Push one canon-owned file to every repo in FLEET via the Contents API.
#
# Not a clone sweep: default branches are a mix of main/master, two fleet repos
# have no local clone, and several clones are stale or dirty. The API sidesteps
# all of that. Writing under .github/workflows needs a token with the `workflow`
# scope.
#
# DRY_RUN=1 (the default) prints a diff per repo and writes nothing.
set -euo pipefail

SRC=${1:?usage: fleet-push-file.sh <local-file> <path-in-repo> <commit-msg-file>}
DEST=${2:?}
MSGFILE=${3:?}
FLEET_FILE=${FLEET_FILE:-$(dirname "$0")/../FLEET}
DRY=${DRY_RUN:-1}

MSG=$(cat "$MSGFILE")
B64=$(base64 < "$SRC" | tr -d '\n')
WANT=$(cat "$SRC")

# Read the whole list up front. Looping with `done < "$FLEET_FILE"` lets `gh`
# consume the remaining lines from stdin, so the sweep silently stops early.
repos=()
while read -r line; do
  [ -n "$line" ] && repos+=("$line")
done < "$FLEET_FILE"

for repo in "${repos[@]}"; do
  branch=$(gh api "repos/$repo" --jq .default_branch)
  meta=$(gh api "repos/$repo/contents/$DEST?ref=$branch" 2>/dev/null || echo '{}')
  sha=$(printf '%s' "$meta" | jq -r '.sha // empty')
  cur=$(printf '%s' "$meta" | jq -r '.content // empty' | base64 -d 2>/dev/null || true)

  if [ "$cur" = "$WANT" ]; then
    printf '== %-32s already current\n' "$repo"
    continue
  fi

  if [ -n "$sha" ]; then action=update; else action=create; fi
  printf '== %-32s %-6s on %s\n' "$repo" "$action" "$branch"
  diff -u --label "$repo:$DEST" --label "canon:$DEST" \
    <(printf '%s' "$cur") "$SRC" || true

  [ "$DRY" = "1" ] && continue

  args=(-X PUT "repos/$repo/contents/$DEST"
        -f "message=$MSG" -f "content=$B64" -f "branch=$branch")
  [ -n "$sha" ] && args+=(-f "sha=$sha")
  gh api "${args[@]}" --jq '"   -> " + .commit.html_url'
done
