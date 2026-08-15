#!/usr/bin/env bash
# Open a remediation PR against one fleet repo -- but only where a PR is an
# honest answer.
#
# Three failure classes come out of `py_canon.fleet_triage`, and only one of
# them takes a pull request per repo:
#
#   canon      A shared workflow or the `v1` tag. The fix is ONE PR to
#              py-canon plus the promote workflow. Opening N per-repo PRs for
#              this is the wrong shape, so this script refuses.
#   ecosystem  Unchanged code went red on its own. If it lives in the shared
#              workflow, see above; if it is a dependency, Dependabot already
#              owns it.
#   repo       The repo's own conformance drift is mechanical -- `preen fix`
#              knows the standard -- so that gets a PR. A real test failure
#              does not: any patch would be a guess, and a guessed PR costs a
#              reviewer more than the issue it replaces. `--file-issue` puts
#              the triage in front of the maintainer instead.
#
# This runs locally rather than in fleet-health.yml because GITHUB_TOKEN cannot
# write to other repositories and this repo has no PAT configured -- the same
# constraint fleet-push-file.sh documents.
#
# usage: fleet-open-fix-pr.sh <owner/repo> [--file-issue]
# DRY_RUN=1 (the default) prints what it would do and writes nothing.
set -euo pipefail

repo=${1:?usage: fleet-open-fix-pr.sh <owner/repo> [--file-issue]}
mode=${2:-preen-fix}
DRY=${DRY_RUN:-1}
BRANCH=fleet/preen-fix

root=$(dirname "$0")/..
excerpt=$(cd "$root" && uv run python - "$repo" <<'PY'
import json
import sys

from py_canon.fleet_triage import (
    classify,
    error_excerpt,
    failed_steps,
    is_external_resource,
    is_mechanical,
    latest_ci_runs,
    RED_CONCLUSIONS,
    RepoStatus,
    _gh,
    detect_same_sha_flip,
    uses_canon_ci,
)

repo = sys.argv[1]
branch = _gh("api", f"repos/{repo}", "--jq", ".default_branch", check=False)
runs = latest_ci_runs(repo, branch)
latest = next((r for r in runs if r.get("conclusion")), None)
if latest is None or latest["conclusion"] not in RED_CONCLUSIONS:
    print(json.dumps({"class": "green"}))
    raise SystemExit(0)

run_id = int(latest["id"])
status = RepoStatus(
    repo=repo,
    branch=branch,
    conclusion=latest["conclusion"],
    run_id=run_id,
    run_url=latest.get("html_url", ""),
    failed_steps=failed_steps(repo, run_id),
    excerpt=error_excerpt(repo, run_id),
    uses_canon_ci=uses_canon_ci(repo),
    same_sha_flip=detect_same_sha_flip(runs),
)
# A single-repo invocation has no cross-repo correlation to draw on, so the
# shared-step set is empty by construction. That biases toward `repo`, which
# is the safe direction: it can only make this script more cautious.
print(json.dumps({
    "class": classify(status, set()),
    "mechanical": is_mechanical(status),
    "external": is_external_resource(status),
    "run_url": status.run_url,
    "steps": [f"{j} -> {s}" for j, s in status.failed_steps],
    "excerpt": status.excerpt,
}))
PY
)

cls=$(printf '%s' "$excerpt" | jq -r '.class')
run_url=$(printf '%s' "$excerpt" | jq -r '.run_url // ""')
printf '== %-32s class=%s\n' "$repo" "$cls"

case "$cls" in
  green) echo "   nothing to do"; exit 0 ;;
  canon)
    cat >&2 <<EOF
   REFUSING: this is canon-caused. Fix it once in py-canon and let the promote
   workflow move v1 -- do not open a pull request per repo.
   $run_url
EOF
    exit 2 ;;
  ecosystem)
    cat >&2 <<EOF
   REFUSING: unchanged code went red on its own. Fix the shared workflow, or
   let Dependabot carry the dependency bump. A per-repo patch here papers over
   a cause that lives elsewhere.
   $run_url
EOF
    exit 2 ;;
esac

if [ "$mode" = "--file-issue" ]; then
  body=$(printf '%s' "$excerpt" | jq -r '
    "Default-branch CI is failing.\n\n" +
    "- run: " + .run_url + "\n" +
    (.steps | map("- failed: `" + . + "`") | join("\n")) + "\n\n" +
    "```\n" + (.excerpt | join("\n")) + "\n```\n\n" +
    (if .external then
      "The excerpt matches a fetch that stopped working rather than a logic error. Check the remote before the code.\n\n"
     else "" end) +
    "Filed from the fleet-health triage in gojiplus/py-canon."')
  if [ "$DRY" = "1" ]; then
    echo "--- would open an issue on $repo ---"; printf '%s\n' "$body"; exit 0
  fi
  gh issue create --repo "$repo" --title "CI is failing on the default branch" --body "$body"
  exit 0
fi

if [ "$(printf '%s' "$excerpt" | jq -r '.mechanical')" != "true" ]; then
  cat >&2 <<EOF
   REFUSING: the failing step is not a conformance check, so \`preen fix\` is
   not the answer to it. It would still find drift and open a pull request --
   one that does not make CI green and answers a question the reviewer did not
   ask. Re-run with --file-issue to hand the maintainer the triage instead.
   $(printf '%s' "$excerpt" | jq -r '.steps | map("   failed: " + .) | join("\n")')
   $run_url
EOF
  exit 3
fi

# `repo` class, mechanical path: let preen apply the standard and PR the diff.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
gh repo clone "$repo" "$work/repo" -- --depth 1 >/dev/null 2>&1
cd "$work/repo"

# `--auto` is load-bearing: plain `preen fix` prompts per issue and aborts on a
# closed stdin, which looks identical to "nothing to fix" from out here.
uvx preen fix --auto >/dev/null || true

if git diff --quiet; then
  cat >&2 <<EOF
   REFUSING: preen has nothing to fix, so this is a real failure in the repo's
   own code. Diagnosing it is not mechanical and a guessed patch costs a
   reviewer more than it saves. Re-run with --file-issue to hand the
   maintainer the triage instead.
   $run_url
EOF
  exit 3
fi

echo "--- preen fix diff ---"
git --no-pager diff --stat
git --no-pager diff

if [ "$DRY" = "1" ]; then
  echo "   (DRY_RUN=1, nothing pushed)"
  exit 0
fi

git checkout -b "$BRANCH"
git commit -am "chore: apply the fleet standard

Mechanical \`preen fix\` output, opened from the fleet-health triage in
gojiplus/py-canon. Default-branch CI was failing: $run_url"
git push -u origin "$BRANCH"
gh pr create --repo "$repo" --head "$BRANCH" \
  --title "chore: apply the fleet standard" \
  --body "Mechanical \`preen fix\` output, opened from the fleet-health triage in gojiplus/py-canon.

Default-branch CI was failing: $run_url

Nothing here was hand-written; if \`preen fix\` is wrong, that is a bug to fix in preen rather than to patch around here."
