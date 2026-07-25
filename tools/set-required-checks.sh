#!/usr/bin/env bash
# Replace a repo ruleset's required status checks with an explicit list.
#
# Rulesets drift: they were written against a check topology that later
# changed, and a required context that never reports blocks every PR forever
# with no visible failure. GET-mutate-PUT rather than sending `rules` alone,
# because partial-update semantics for conditions/bypass_actors are not
# guaranteed.
#
# usage: set-required-checks.sh <owner/repo> <context> [<context> ...]
# DRY_RUN=1 (the default) prints the before/after and writes nothing.
set -euo pipefail

repo=${1:?usage: set-required-checks.sh <owner/repo> <context> ...}
shift
[ "$#" -gt 0 ] || { echo "refusing to leave a ruleset with zero required checks" >&2; exit 1; }
DRY=${DRY_RUN:-1}

contexts=$(printf '%s\n' "$@" | jq -R '{context: .}' | jq -s '.')

id=$(gh api "repos/$repo/rulesets" --jq \
  '[.[] | select(.target=="branch")][0].id')
[ -n "$id" ] || { echo "$repo: no branch ruleset" >&2; exit 1; }

ruleset=$(gh api "repos/$repo/rulesets/$id")

before=$(printf '%s' "$ruleset" | jq -c \
  '[.rules[]?|select(.type=="required_status_checks")|.parameters.required_status_checks[].context]')

payload=$(printf '%s' "$ruleset" | jq --argjson ctx "$contexts" \
  '{name, target, enforcement, conditions, bypass_actors,
    rules: [.rules[] | if .type == "required_status_checks"
            then .parameters.required_status_checks = $ctx
            else . end]}')

printf '== %s (ruleset %s)\n   before: %s\n   after:  %s\n' \
  "$repo" "$id" "$(printf '%s' "$before" | jq -c .)" "$(printf '%s' "$contexts" | jq -c '[.[].context]')"

[ "$DRY" = "1" ] && exit 0

printf '%s' "$payload" | gh api -X PUT "repos/$repo/rulesets/$id" --input - --jq '"   -> updated: " + .name'
