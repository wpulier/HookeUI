#!/usr/bin/env bash
# Save today's evidence into one folder: what was asked and answered, what the app did, the
# chat as shown (typed and spoken), the server logs and today's researchers' steps.
#
#   make save-logs                      -> <logs>/saved/<time>/ (services/logs, or ~/MicroscopeMind/logs once the saves moved)
#   make save-logs NOTE="blood cells labelled wrong at 14:05"
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Where the saves are now (orchestrator/paths.py decides: services/, or the three homes once moved).
where() { (cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths "$1"); }
LOGS="$(where logs)"
RESEARCH="$(where research)"
SESSIONS="$(where sessions)"
RECORDINGS="$(where recordings)"
DAY="$(date +%Y-%m-%d)"
OUT="$LOGS/saved/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# Today's lines of the structured logs (each line carries its own time).
today() { # file -> lines from today
  [ -f "$1" ] || return 0
  python3 - "$1" "$DAY" <<'PY'
import json, sys, time
path, day = sys.argv[1], sys.argv[2]
for line in open(path, encoding="utf-8", errors="replace"):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    at = d.get("at")
    when = time.strftime("%Y-%m-%d", time.localtime(at)) if isinstance(at, (int, float)) else str(at or "")[:10]
    if when == day:
        sys.stdout.write(line)
PY
}
today "$LOGS/interactions.jsonl" >"$OUT/interactions.jsonl"
today "$LOGS/conversation.jsonl" >"$OUT/conversation.jsonl"
for f in orchestrator.log camera-bridge.log; do
  [ -f "$LOGS/$f" ] && tail -n 20000 "$LOGS/$f" >"$OUT/$f"
done
# Today's researchers: their record and steps (their pictures stay where they are).
# (also those moved aside by "start today afresh" before sessions existed)
for folder in "$RESEARCH/$DAY" "$RESEARCH/archive/$DAY"-cleared-*; do
  [ -d "$folder" ] || continue
  for d in "$folder"/*/; do
    [ -f "$d/session.json" ] || continue
    mkdir -p "$OUT/research/$(basename "$d")"
    cp "$d/session.json" "$OUT/research/$(basename "$d")/"
    [ -f "$d/steps.jsonl" ] && cp "$d/steps.jsonl" "$OUT/research/$(basename "$d")/"
  done
done
# Today's conversations: the day's journal (threads, turns, subjects, summaries).
for folder in "$SESSIONS/$DAY" "$SESSIONS/archive/$DAY"-cleared-*; do
  [ -f "$folder/journal.jsonl" ] || continue
  mkdir -p "$OUT/sessions/$(basename "$folder")"
  cp "$folder/journal.jsonl" "$OUT/sessions/$(basename "$folder")/"
done
{
  echo "Saved $(date '+%Y-%m-%d %H:%M:%S')"
  [ -n "${NOTE:-}" ] && echo "Note: $NOTE"
  echo "Recordings: $RECORDINGS/"
} >"$OUT/README.txt"

echo "Saved today's logs to ${OUT/#$HOME/~}"
echo "  $(wc -l <"$OUT/interactions.jsonl" | tr -d ' ') interactions, $(wc -l <"$OUT/conversation.jsonl" | tr -d ' ') chat lines$( [ -d "$OUT/research" ] && echo ", $(ls "$OUT/research" | wc -l | tr -d ' ') researchers")"
