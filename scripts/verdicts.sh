#!/usr/bin/env bash
# Put the user's verdicts on capabilities into git (ADR 0007): the app keeps them in its own log
# (services/capabilities/verdicts.jsonl, git-ignored); this appends the ones not yet in git to
# contract/verdicts.jsonl on a branch from origin/main, in a temporary worktree (the live
# checkout is never touched), commits and opens a PR. contract/verdicts.jsonl merges by union.
#   make verdicts
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/services/.venv/bin/python"
PENDING="$(cd "$REPO/services" && "$PY" -c '
import json
from orchestrator.capabilities import Verdicts
from orchestrator.paths import Paths
for e in Verdicts(Paths.from_env().verdicts, version="").pending():
    print(json.dumps(e, ensure_ascii=False, sort_keys=True))
')"
if [ -z "$PENDING" ]; then
  echo "No verdicts waiting to go into git."
  exit 0
fi
COUNT="$(printf '%s\n' "$PENDING" | wc -l | tr -d ' ')"
BRANCH="verdicts/$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)/verdicts"
git -C "$REPO" fetch -q origin
git -C "$REPO" worktree add -q -b "$BRANCH" "$WORK" origin/main
trap 'git -C "$REPO" worktree remove --force "$WORK" >/dev/null 2>&1 || true' EXIT
printf '%s\n' "$PENDING" >> "$WORK/contract/verdicts.jsonl"
sort -u -o "$WORK/contract/verdicts.jsonl" "$WORK/contract/verdicts.jsonl"
git -C "$WORK" add contract/verdicts.jsonl
git -C "$WORK" commit -q -m "Verdicts on capabilities: $COUNT from the studio"
git -C "$WORK" push -q -u origin "$BRANCH"
cd "$WORK" && gh pr create --base main --head "$BRANCH" --title "Verdicts on capabilities: $COUNT from the studio" \
  --body "The user's verdicts (works, broken, not yet) from the Capabilities page, moved from the app's own log into contract/verdicts.jsonl (ADR 0007). Data only; contract/verdicts.jsonl merges by union."
