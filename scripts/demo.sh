#!/usr/bin/env bash
# A demo session: record the camera feed and the whole screen together, and on stop gather
# everything that happened (logs, reports, flags) into one folder beside the recordings.
#
#   make demo-start      # camera MP4 (the bridge) + screen with microphone (macOS screencapture)
#   make demo-stop       # stop both; save logs, voice and day reports, flags; write the manifest
#   make demo-status
#
# Also press Record in the studio's top bar: it records the app tab with the voice you hear
# (GPT-Live) and the microphone (docs/demo.md). Press F in the studio to flag a moment.
# The screen is recorded with macOS screencapture (ScreenCaptureKit), never with an FFmpeg
# camera device: only the camera bridge may open the iPhone (AGENTS.md). The terminal that runs
# this needs Screen Recording permission (System Settings > Privacy & Security).
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAMERA_URL="${CAMERA_URL:-http://127.0.0.1:${CAMERA_PORT:-8765}}"
ORCH_URL="${ORCH_URL:-http://127.0.0.1:${ORCH_PORT:-8770}}"
# The recordings folder (orchestrator/paths.py: recordings/, or ~/MicroscopeMind/assets/recordings once the saves moved).
RECORDINGS="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths recordings 2>/dev/null || echo "$ROOT/recordings")"
DEMOS="$RECORDINGS/demos"
CURRENT="$DEMOS/.current"
PY="$ROOT/services/.venv/bin/python"
SEGMENT_S="${SEGMENT_S:-30}"  # screen clip length: stop waits at most this long

say() { printf '%s\n' "$*"; }
json_get() { "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {"d": d}))' "$1" 2>/dev/null || true; }
# Is the camera file $1 still being written? The bridge finishes it after the stop, however long
# that takes (a long recording on a busy disk took over 10 s on 2026-10-06).
camera_finishing() {
  curl -s -m 3 "$CAMERA_URL/api/record/status" |
    CAM="$1" "$PY" -c 'import json,os,sys; d=json.load(sys.stdin); print(any(e.get("path") == os.environ["CAM"] for e in d.get("finishing", [])))' 2>/dev/null || true
}

start() {
  if [ -f "$CURRENT" ] && [ -d "$(cat "$CURRENT")" ]; then
    say "A demo is already recording: $(cat "$CURRENT") (make demo-stop first)"
    exit 1
  fi
  local stamp out
  stamp="$(date +%Y%m%d-%H%M%S)"
  out="$DEMOS/$stamp"
  mkdir -p "$out"
  echo "$out" >"$CURRENT"

  # What is running, for the record.
  curl -s -m 3 "$CAMERA_URL/api/health" >"$out/camera-health.json" || echo '{}' >"$out/camera-health.json"
  curl -s -m 3 "$ORCH_URL/api/health" >"$out/app-health.json" || echo '{}' >"$out/app-health.json"

  # The camera feed: the bridge records what the phone sends (MP4). The demo is one owner of
  # that recording: the studio's Record and Stop leave it running, and demo-stop ends only it.
  curl -s -m 10 -X POST "$CAMERA_URL/api/record/start?owner=demo" >"$out/camera-record-start.json" || true
  local cam
  cam="$(json_get 'd.get("path") or d.get("recording", {}).get("path") or ""' <"$out/camera-record-start.json")"

  # The screen with the microphone, in back-to-back clips of SEGMENT_S seconds, joined into
  # screen.mov on stop. screencapture -v saves a recording only when it ends by itself (its
  # time limit): stopped by a signal or from a script it throws the recording away (tested
  # 2026-10-06), so each clip ends on its own and stop waits for the current one.
  mkfifo "$out/.screen-in"
  (sleep 10800 >"$out/.screen-in") &  # an open, silent stdin for screencapture
  echo $! >"$out/.screen-holder.pid"
  (n=0
   while [ ! -f "$out/.screen-stop" ]; do
     n=$((n + 1)); began=$(date +%s)
     screencapture -v -g -k -V "$SEGMENT_S" "$out/screen-$(printf %04d "$n").mov" <"$out/.screen-in" >>"$out/screencapture.log" 2>&1 || true
     [ $(( $(date +%s) - began )) -lt 2 ] && sleep 2  # failing at once (no permission): do not spin
   done) &
  echo $! >"$out/.screen.pid"

  "$PY" - "$out" "$stamp" "$cam" <<'PY'
import json, subprocess, sys, time
out, stamp, cam = sys.argv[1:4]
sha = subprocess.run(["git", "-C", out, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(["git", "-C", out, "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True).stdout.strip())
app = json.load(open(f"{out}/app-health.json")) if open(f"{out}/app-health.json").read().strip() else {}
json.dump({"demo": stamp, "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "startedEpoch": time.time(),
           "git": {"sha": sha, "dirty": dirty}, "camera": {"recording": cam},
           "screen": "screen.mov", "app": {k: app.get(k) for k in ("state", "options", "settings") if k in app}},
          open(f"{out}/manifest.json", "w"), indent=1)
PY

  sleep 4
  if kill -0 "$(cat "$out/.screen.pid")" 2>/dev/null && pgrep -f "screencapture -v .*$out" >/dev/null; then
    say "Recording the screen (with the microphone) into $out (clips of ${SEGMENT_S} s, joined into screen.mov on stop)"
  else
    say "The screen recording did not start: $(cat "$out/screencapture.log" 2>/dev/null)"
    say "Give this terminal Screen Recording permission (System Settings > Privacy & Security), or record with Cmd-Shift-5."
  fi
  if [ -n "$cam" ]; then say "Recording the camera into $cam"; else say "The camera is not recording: $(cat "$out/camera-record-start.json")"; fi
  say "Now press Record in the studio (the app tab with the voice), and F in the studio to flag a moment."
}

stop() {
  [ -f "$CURRENT" ] || { say "No demo is recording."; exit 1; }
  local out
  out="$(cat "$CURRENT")"

  # The screen: let the current clip end by itself (at most SEGMENT_S), then join the clips.
  if [ -f "$out/.screen.pid" ]; then
    touch "$out/.screen-stop"
    say "Finishing the screen recording (up to ${SEGMENT_S} s)..."
    for _ in $(seq 1 $((SEGMENT_S + 10))); do kill -0 "$(cat "$out/.screen.pid")" 2>/dev/null || break; sleep 1; done
    kill "$(cat "$out/.screen-holder.pid")" 2>/dev/null || true
    if ls "$out"/screen-*.mov >/dev/null 2>&1; then
      for f in "$out"/screen-*.mov; do printf "file '%s'\n" "$f"; done >"$out/.screen-list"
      if ffmpeg -loglevel error -y -f concat -safe 0 -i "$out/.screen-list" -c copy "$out/screen.mov" 2>>"$out/screencapture.log"; then
        rm -f "$out"/screen-*.mov  # joined: the clips are in screen.mov
      fi
    fi
  fi
  curl -s -m 10 -X POST "$CAMERA_URL/api/record/stop?owner=demo" >"$out/camera-record-stop.json" || true

  # The recordings beside the session: hard links (same disk, no copy), else symlinks.
  link() { ln "$1" "$2" 2>/dev/null || ln -s "$1" "$2"; }
  local cam stopped others waited=0
  cam="$(json_get 'd.get("path") or d.get("recording", {}).get("path") or ""' <"$out/camera-record-stop.json")"
  stopped="$(json_get 'd.get("stopped")' <"$out/camera-record-stop.json")"
  others="$(json_get '", ".join(d.get("owners") or [])' <"$out/camera-record-stop.json")"
  if [ -n "$cam" ] && [ "$stopped" = True ]; then
    # Linked only once complete: the bridge gives the file its index at the front last.
    while [ "$(camera_finishing "$cam")" = True ]; do
      [ $((waited % 10)) -eq 0 ] && say "Finishing the camera file ($waited s)..."
      sleep 1; waited=$((waited + 1))
    done
    [ -f "$cam" ] && link "$cam" "$out/camera-$(basename "$cam")"
  elif [ -n "$cam" ] && [ -f "$cam" ]; then
    # Someone else (the studio's Record) is still recording it: a symlink, which shows the
    # whole file once they stop.
    say "The camera keeps recording for ${others:-another caller}; linked to it: $cam"
    ln -s "$cam" "$out/camera-$(basename "$cam")" 2>/dev/null || true
  fi
  find "$RECORDINGS" -maxdepth 1 -name 'MicroscopeMind-*' -newer "$out/manifest.json" -print0 2>/dev/null |
    while IFS= read -r -d '' f; do link "$f" "$out/studio-$(basename "$f")"; done

  # Everything that happened.
  NOTE="demo $(basename "$out")" "$ROOT/scripts/save-logs.sh" >"$out/save-logs.txt" 2>&1 || true
  (cd "$ROOT/services" && .venv/bin/python -m bench.voice_report >"$out/voice-report.txt" 2>&1) || true
  (cd "$ROOT/services" && .venv/bin/python -m bench.day_report >"$out/day-report.txt" 2>&1) || true
  "$PY" - "$out" "$(cd "$ROOT/services" && "$PY" -m orchestrator.paths interaction_log)" "$ROOT/services" <<'PY'
import json, sys, time
sys.path.insert(0, sys.argv[3])
out, log = sys.argv[1:3]
manifest = json.load(open(f"{out}/manifest.json"))
since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(manifest["startedEpoch"]))
try:  # which options were on: every request's log line carries them (options.Settings.to_wire)
    from orchestrator.options import Settings
    option_keys = set(Settings().to_wire())
except Exception:
    option_keys = set()
flags, kinds, options = [], {}, {}
import os
for line in (open(log, encoding="utf-8", errors="replace") if os.path.exists(log) else []):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if str(d.get("at", "")) < since:
        continue
    kinds[d.get("kind", "?")] = kinds.get(d.get("kind", "?"), 0) + 1
    if not options and option_keys and option_keys <= set(d):
        options = {k: d[k] for k in sorted(option_keys)}
    if d.get("kind") == "flag":
        flags.append(d)
with open(f"{out}/flags.jsonl", "w") as f:
    for d in flags:
        f.write(json.dumps(d) + "\n")
manifest.update(stoppedAt=time.strftime("%Y-%m-%dT%H:%M:%S"), flags=len(flags), logged=kinds,
                options=options or "no request was logged during the demo")
json.dump(manifest, open(f"{out}/manifest.json", "w"), indent=1)
print(f"{len(flags)} flag(s); logged: {kinds}")
PY
  rm -f "$out/.screen-in" "$out/.screen.pid" "$out/.screen-holder.pid" "$out/.screen-stop" "$out/.screen-list" "$CURRENT"
  say "Saved in $out:"
  ls -la "$out" | sed 1d
}

status() {
  if [ -f "$CURRENT" ]; then
    local out
    out="$(cat "$CURRENT")"
    say "Recording since $(basename "$out"): screen $(du -h "$out/screen.mov" 2>/dev/null | cut -f1 || echo '?')"
    curl -s -m 3 "$CAMERA_URL/api/record/status"; echo
  else
    say "No demo is recording."
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) say "usage: $0 start|stop|status"; exit 2 ;;
esac
