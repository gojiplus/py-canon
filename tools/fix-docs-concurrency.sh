#!/usr/bin/env bash
# Re-key a repo's docs.yml concurrency group on the ref.
#
# A constant `pages` group means every PR docs build contends for one slot.
# GitHub cancels the *pending* run in a group when a new one queues, so a burst
# of Dependabot PRs leaves all but the last with no `build` check at all -- and
# `build` is a required status check, so those PRs sit BLOCKED forever.
#
# docs.yml differs materially per repo, so this rewrites in place rather than
# templating. DRY_RUN=1 (the default) diffs and writes nothing.
set -euo pipefail

DRY=${DRY_RUN:-1}
DEST=.github/workflows/docs.yml
MSG='docs: key the concurrency group on the ref

A constant `pages` group meant every PR docs build contended for one slot.
GitHub cancels the pending run in a group when a new one queues, so a burst
of Dependabot PRs left all but the last with no `build` check at all -- and
`build` is a required status check, so those PRs sat BLOCKED forever.

Keyed on github.ref, PR builds no longer collide. cancel-in-progress is on
only for pull_request, so a default-branch Pages deployment is never killed
mid-flight.'

for repo in "$@"; do
  # Tolerate a failed lookup: one transient API error should not abort a
  # 25-repo sweep half way through and leave the fleet in mixed state.
  if ! branch=$(gh api "repos/$repo" --jq .default_branch 2>/dev/null); then
    printf '== %-28s LOOKUP FAILED, skipping\n' "$repo"
    continue
  fi
  meta=$(gh api "repos/$repo/contents/$DEST?ref=$branch" 2>/dev/null || echo '{}')
  sha=$(printf '%s' "$meta" | jq -r '.sha // empty')
  if [ -z "$sha" ]; then
    printf '== %-28s no %s, skipping\n' "$repo" "$DEST"
    continue
  fi
  cur=$(printf '%s' "$meta" | jq -r .content | base64 -d)

  new=$(printf '%s\n' "$cur" \
    | sed -E 's/^([[:space:]]*)group:[[:space:]]*"?pages"?[[:space:]]*$/\1group: docs-${{ github.ref }}/' \
    | sed -E "s/^([[:space:]]*)cancel-in-progress:[[:space:]]*false[[:space:]]*\$/\1cancel-in-progress: \${{ github.event_name == 'pull_request' }}/")

  if [ "$new" = "$cur" ]; then
    printf '== %-28s already keyed on the ref\n' "$repo"
    continue
  fi

  printf '== %-28s rewrite on %s\n' "$repo" "$branch"
  diff -u --label "$repo:$DEST" --label "fixed:$DEST" \
    <(printf '%s\n' "$cur") <(printf '%s\n' "$new") || true

  [ "$DRY" = "1" ] && continue

  gh api -X PUT "repos/$repo/contents/$DEST" \
    -f "message=$MSG" \
    -f "content=$(printf '%s\n' "$new" | base64 | tr -d '\n')" \
    -f "branch=$branch" -f "sha=$sha" --jq '"   -> " + .commit.html_url'
done
