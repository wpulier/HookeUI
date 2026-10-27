#!/usr/bin/env bash
# ObservationUI: start, stop or check every server in one place (the Makefile calls this).
#
#   scripts/studio.sh start [--dev]   the live microscope app (add --dev for hot reload)
#   scripts/studio.sh stop            stop everything of ObservationUI, on any port
#   scripts/studio.sh status          what is running, and where
#
# Servers:
#   camera bridge   :8765  owns the iPhone; can play test footage (footage/) into the feed
#                          instead, switched from the studio's camera menu
#   orchestrator    :8770  perception, scene, AI; serves the studio web app; runs the GPU worker
#   dev studio      :5180  only with --dev: the web app with hot reload (Vite), same backend
#
# One place to turn it on and off. Starting first stops whatever of ObservationUI is already
# running (any port), so there is only ever one copy. Stopping (make stop, Ctrl-C, or closing
# the terminal that ran start) ends every server and worker. Only processes from this checkout
# are touched (a worktree in .claude/worktrees is another checkout); another program on one of
# our ports is reported and left alone.
set -eo pipefail  # not -u: bash 3.2 (macOS) treats empty arrays as unset

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/services/.venv/bin/python"
# A packaged copy (the Mac app, packaging/macos/README.md) ships its studio and capture app built, and has no Node,
# Xcode tools or system Python: the bridge runs on the app's own Python, and nothing is checked or built here.
PACKAGED=""
[ -f "$ROOT/packaged.json" ] && PACKAGED=1
BRIDGE_PY="python3"
[ -n "$PACKAGED" ] && BRIDGE_PY="$PY"
# Read-only HTML design sandbox: independent of the camera/model runtime.
# Enter before product setup/stop logic so another developer's live work is untouched.
if [ "${1:-}" = "design" ]; then
  shift
  exec "$PY" "$ROOT/design/studio/server.py" --port "${DESIGN_PORT:-5194}" "$@"
fi
# Only start reads private defaults. Invalid settings fail before touching an
# existing runtime; stop/status remain available to recover. No shell config/eval.
if [ "${1:-}" = "start" ] && [ -x "$PY" ]; then
  launch_defaults="$("$PY" "$ROOT/scripts/studio_launch.py")"
  while IFS='=' read -r launch_key launch_value; do
    [ -z "$launch_key" ] || export "$launch_key=$launch_value"
  done <<< "$launch_defaults"
fi
CAMERA_PORT="${CAMERA_PORT:-8765}"
ORCH_PORT="${ORCH_PORT:-8770}"
DEV_PORT="${DEV_PORT:-5180}"
CV_PORT="${CV_PORT:-8766}"
CAMERA_URL="http://127.0.0.1:$CAMERA_PORT"
BRIDGE_SCRIPT="iphone_bridge_server.py"
ORCH_URL="http://127.0.0.1:$ORCH_PORT"
# Which licences this copy may use (services/orchestrator/licences.py, ADR 0005). Research is
# the default here and in direct app/GPU launches for this noncommercial platform. A commercial
# build must explicitly set MICROMIND_EDITION=commercial; preserve any supplied override so
# the shared licence router also refuses research components for an invalid nonempty value.
export MICROMIND_EDITION="${MICROMIND_EDITION:-research}"
# Test footage for the studio's camera menu: the assets store shared by every checkout
# (assets/README.md; services/orchestrator/assets.py).
FOOTAGE_DIR="${FOOTAGE_DIR:-${MICROMIND_ASSETS:-$HOME/MicroscopeMind}/footage}"
# The camera's live source (the iPhone, Blackmagic, another camera or No camera), kept across starts. With none saved
# yet, a first run starts on No camera (pictures and videos; nothing searches for a camera, 2026-10-15).
# MICROMIND_CAMERA_SOURCE (a stream URL, `make start CAMERA_SOURCE=...`) wins over both: Blackmagic on that listener.
CAMERA_SOURCE_FILE="${CAMERA_SOURCE_FILE:-${MICROMIND_ASSETS:-$HOME/MicroscopeMind}/camera-source.json}"
# Datasets the blue dot collects (docs/datasets.md): the live app's go outside every checkout,
# beside the test assets. A second stack on other ports keeps its own (under services/), so it can
# never carry on the live app's dataset.
if [ -z "${MICROMIND_HOME:-}" ]; then
  if [ "$ORCH_PORT" = "8770" ]; then
    export MICROMIND_DATASETS_DIR="${MICROMIND_DATASETS_DIR:-${MICROMIND_ASSETS:-$HOME/MicroscopeMind}/datasets}"
  else
    export MICROMIND_DATASETS_DIR="${MICROMIND_DATASETS_DIR:-$ROOT/services/datasets-$ORCH_PORT}"
  fi
fi
# Where the saves are (docs/saves.md): services/ and recordings/ as before, or the three homes in
# ~/MicroscopeMind once `make saves-move` moved them (orchestrator/paths.py decides; it is asked here).
SAVES_ROOT=""
if [ -z "${MICROMIND_HOME:-}" ]; then
  SAVES_ROOT="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths saves 2>/dev/null || true)"
fi
LOG_DIR="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths logs 2>/dev/null || true)"
RECORDINGS_DIR="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths recordings 2>/dev/null || true)"
# The datasets Record collects, where the orchestrator keeps them: the bridge plays a session's own recording
# from there (POST /feed?source=recording, the studio's camera menu).
DATASETS_DIR="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths datasets 2>/dev/null || true)"
# The app's snapshots and clearer pictures, where it keeps them: the bridge may play any picture or recording in them
# (POST /feed?source=asset, a session's sources), as it may in the datasets and footage folders.
SNAPSHOTS_DIR="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths snapshots 2>/dev/null || true)"
RESULTS_DIR="$(cd "$ROOT/services" && .venv/bin/python -m orchestrator.paths results 2>/dev/null || true)"
LOG_DIR="${LOG_DIR:-$ROOT/services/logs}"
RECORDINGS_DIR="${RECORDINGS_DIR:-$ROOT/recordings}"
DATASETS_DIR="${DATASETS_DIR:-${MICROMIND_DATASETS_DIR:-$ROOT/services/datasets}}"
SNAPSHOTS_DIR="${SNAPSHOTS_DIR:-$ROOT/services/snapshots}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/services/results}"
# Logs per port, so a second stack (tests, other ports) never truncates the main logs.
log_name() { if [ "$2" = "$3" ]; then echo "$LOG_DIR/$1.log"; else echo "$LOG_DIR/$1-$2.log"; fi; }
BRIDGE_LOG="$(log_name camera-bridge "$CAMERA_PORT" 8765)"
ORCH_LOG="$(log_name orchestrator "$ORCH_PORT" 8770)"
DEV_LOG="$(log_name dev-studio "$DEV_PORT" 5180)"
RUN_DIR="$ROOT/services/logs"  # the launchers' pid files: this checkout's, wherever the saves are
LAUNCHER_PID="$RUN_DIR/studio-$ORCH_PORT.pid"  # the running `start`, so stop/replace can end it cleanly

# --- what is running ---------------------------------------------------------------------
health() { curl -s -m 2 "$1" 2>/dev/null || true; }
is_bridge() { health "$CAMERA_URL/api/health" | grep -q '"targetDevice"'; }
feed_footage() { health "$CAMERA_URL/feed" | sed -n 's/.*"footage": "\(.*\)",*$/\1/p' | head -1; }
is_orchestrator() { health "$ORCH_URL/api/health" | grep -q '"microscope-mind-orchestrator"'; }
is_dev_studio() { health "http://127.0.0.1:$DEV_PORT/" | grep -Eq '<title>(ObservationUI|MicroscopeMind) Studio</title>'; }
is_cv() { health "http://127.0.0.1:$CV_PORT/api/health" | grep -q '"iphone-cv-consumer"'; }
pids_on() { lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; }

# Another program on one of our ports: say so and stop (it is never killed).
foreign() {
  echo "Port $1 is used by another program (pid $(pids_on "$1" | tr '\n' ' ')), not $2. Free it, or set $3=<port>." >&2
  exit 1
}

# This script and its own subshells (they share its command line) are never "already running".
is_self() {
  local pid="$1"
  while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
    [ "$pid" = "$$" ] && return 0
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  done
  return 1
}

# A process of this checkout: its command line or working directory is in it. A worktree in its
# .claude/worktrees, where other sessions run their tests and sims, is a checkout of its own: its
# processes are never this one's (a start inside the worktree finds them, ROOT being the worktree).
in_checkout() { # command cwd
  case "$1" in *"$ROOT/.claude/worktrees/"*) return 1 ;; esac
  case "$2" in "$ROOT/.claude/worktrees/"*) return 1 ;; esac
  case "$1" in *"$ROOT/"*) return 0 ;; esac
  case "$2" in "$ROOT"|"$ROOT"/*) return 0 ;; esac
  return 1
}

# Every ObservationUI process from this checkout, on any port: launchers, camera bridges (and
# their ffmpeg), orchestrators (and their GPU and research workers), dev studios, the edge
# dashboard and scenario-test stacks. Matched by command line and working directory, so
# nothing outside this checkout is ever touched.
ours() {
  local pid cmd cwd
  for pid in $(pgrep -f "studio\.sh start|iphone_bridge_server|capture-app/run\.sh|MicroscopeMind Camera\.app|orchestrator\.server|multiprocessing|research_worker|gpu_worker|ffmpeg -nostdin|vite|sim\.harness|serve-cv|iphone-cv-consumer|cv_server" 2>/dev/null); do
    is_self "$pid" && continue
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)" || continue
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    if in_checkout "$cmd" "$cwd"; then echo "$pid"; fi
  done
}

# Launchers by working directory alone (make runs ./scripts/studio.sh from the checkout).
launchers() {
  local pid
  for pid in $(pgrep -f "studio\.sh start" 2>/dev/null); do
    is_self "$pid" && continue
    if in_checkout "" "$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"; then echo "$pid"; fi
  done
}

stop_everything() {
  local pids left
  # Launchers first: each stops its own servers cleanly.
  for pid in $(launchers); do kill "$pid" 2>/dev/null || true; done
  for _ in $(seq 1 50); do [ -z "$(launchers)" ] && break; sleep 0.2; done
  pids="$(ours | tr '\n' ' ')"
  [ -n "${pids// }" ] && kill $pids 2>/dev/null || true
  for _ in $(seq 1 40); do [ -z "$(ours)" ] && break; sleep 0.2; done
  left="$(ours | tr '\n' ' ')"
  if [ -n "${left// }" ]; then
    kill -9 $left 2>/dev/null || true  # a capture stuck on the camera can ignore a polite stop
    sleep 0.5
  fi
  rm -f "$RUN_DIR"/studio-*.pid
}

cmd_stop() {
  echo "Stopping ObservationUI"
  stop_everything
  local left="" port
  [ -n "$(ours)" ] && left=" (some processes would not stop: $(ours | tr '\n' ' '))"
  for port in "$DEV_PORT" "$ORCH_PORT" "$CAMERA_PORT"; do
    [ -n "$(pids_on "$port")" ] && left="$left :$port is used by another program"
  done
  if [ -n "$left" ]; then echo "  stopped, except$left"; else echo "  all stopped; nothing of ObservationUI is running"; fi
}

cmd_status() {
  line() { printf '  %-15s %-5s %s\n' "$1" "$2" "$3"; }
  echo "ObservationUI"
  if is_bridge; then
    local footage live
    footage="$(feed_footage)"
    live="$(health "$CAMERA_URL/api/health" | grep -q '"ok": true' && echo "live" || echo "no frames")"
    if [ -n "$footage" ]; then line "camera bridge" "up" "$CAMERA_URL (test footage: $footage)"
    elif health "$CAMERA_URL/api/health" | grep -q '"sourceKind": "blackmagic"'; then line "camera bridge" "up" "$CAMERA_URL (Blackmagic, $live)"
    else line "camera bridge" "up" "$CAMERA_URL (iPhone through Continuity Camera, $live)"; fi
  else line "camera bridge" "down" ""; fi
  if is_orchestrator; then
    local h; h="$(health "$ORCH_URL/api/health")"
    if echo "$h" | grep -q '"ready":true'; then line "orchestrator" "up" "$ORCH_URL/  (studio; worker ready; see Models for setup)"
    else line "orchestrator" "up" "$ORCH_URL/  (studio; analysis worker starting)"; fi
  else line "orchestrator" "down" ""; fi
  if is_dev_studio; then line "dev studio" "up" "http://127.0.0.1:$DEV_PORT/  (hot reload)"; else line "dev studio" "down" "(only with make dev)"; fi
  if is_cv; then line "edge dashboard" "up" "http://127.0.0.1:$CV_PORT/  (original pipeline)"; fi
  local n; n="$(ours | wc -l | tr -d ' ')"
  if [ "$n" -gt 0 ]; then echo "  $n ObservationUI process(es) running in all (servers and workers; make stop stops every one)"
  else echo "  nothing of ObservationUI is running"; fi
  return 0
}

# --- start -------------------------------------------------------------------------------
children=()
names=()
cleanup() {
  trap - EXIT INT TERM HUP
  [ -f "$LAUNCHER_PID" ] && [ "$(cat "$LAUNCHER_PID")" = "$$" ] && rm -f "$LAUNCHER_PID"
  if [ "${#children[@]}" -gt 0 ]; then
    echo
    echo "Stopping ObservationUI"
    for i in "${!children[@]}"; do
      kill "${children[$i]}" 2>/dev/null && echo "  stopped ${names[$i]}" || true
    done
    wait 2>/dev/null || true
  fi
}

spawn() { # name log command...
  local name="$1" log="$2"
  shift 2
  # Appended, never overwritten: a restart after a problem keeps the evidence.
  echo "===== $name started $(date '+%Y-%m-%d %H:%M:%S') =====" >>"$log"
  "$@" >>"$log" 2>&1 &
  children+=($!)
  names+=("$name")
}

wait_until() { # seconds what check-function
  local seconds="$1" what="$2" check="$3"
  for _ in $(seq 1 $((seconds * 5))); do
    "$check" && return 0
    for i in "${!children[@]}"; do
      if ! kill -0 "${children[$i]}" 2>/dev/null; then
        echo "The ${names[$i]} stopped while starting. Its log: $LOG_DIR/" >&2
        exit 1
      fi
    done
    sleep 0.2
  done
  echo "Gave up waiting for $what after ${seconds}s. Logs: $LOG_DIR/" >&2
  exit 1
}

# Build the web app quietly, but show the compiler's output (tsc errors) if it fails.
build_studio() {
  local build_log; build_log="$(mktemp -t studio-build)"
  if ! (cd "$ROOT/apps/studio" && npm run --silent build) >"$build_log" 2>&1; then
    cat "$build_log" >&2
    rm -f "$build_log"
    echo "The studio web app did not build (errors above). Fix them, then run make start again." >&2
    exit 1
  fi
  rm -f "$build_log"
}

workers_ready() { health "$ORCH_URL/api/health" | grep -q '"ready":true'; }

cmd_start() {
  local dev="${1:-}"
  if [ ! -x "$PY" ]; then echo "Services environment is missing. Run: make setup" >&2; exit 1; fi
  if [ -n "$PACKAGED" ]; then
    if [ ! -f "$ROOT/apps/studio/dist/build.json" ]; then echo "This copy's studio build is missing. Install the app again." >&2; exit 1; fi
  else
    if [ ! -d "$ROOT/apps/studio/node_modules" ]; then echo "Web app dependencies are missing. Run: make setup" >&2; exit 1; fi
    python3 "$ROOT/scripts/studio_setup.py"
  fi
  mkdir -p "$LOG_DIR" "$RUN_DIR"

  # One copy only: whatever of MicroscopeMind is already running (any port) is stopped first.
  if [ -n "$(ours)" ]; then
    echo "Stopping the ObservationUI that is already running"
    stop_everything
  fi
  [ -n "$(pids_on "$ORCH_PORT")" ] && foreign "$ORCH_PORT" "the orchestrator" ORCH_PORT
  [ -n "$dev" ] && [ -n "$(pids_on "$DEV_PORT")" ] && foreign "$DEV_PORT" "the dev studio" DEV_PORT
  [ -n "$(pids_on "$CAMERA_PORT")" ] && foreign "$CAMERA_PORT" "the camera bridge" CAMERA_PORT

  # Saves moved out of the checkout: what older code wrote in the old places since is linked into the
  # new homes before anything starts writing (orchestrator/saves.py sync; never deletes).
  if [ -n "$SAVES_ROOT" ]; then
    (cd "$ROOT/services" && "$PY" -m orchestrator.saves sync) || echo "Saves: could not bring the old places in step (above); starting anyway" >&2
  fi

  # Backend-only changes and commits also change the release identity. A source
  # mtime check alone can serve an older bundle labelled with a different build.
  # The camera page (served by the bridge) is built with it; a missing one is built too.
  if [ -z "$PACKAGED" ] && { ! (cd "$ROOT/services" && "$PY" -c 'from orchestrator.build_info import runtime_identity; raise SystemExit(0 if runtime_identity()["matches"] else 1)') \
      || [ ! -f "$ROOT/apps/studio/dist-camera/camera.html" ]; }; then
    echo "Building the studio web app"
    build_studio
  fi

  trap cleanup EXIT
  trap 'cleanup; exit 0' INT TERM HUP  # Ctrl-C, make stop or closing the terminal: everything stops
  echo $$ >"$LAUNCHER_PID"
  # The bridge's iPhone capture app (macOS wakes the iPhone only for an app; capture-app/README.md): built when its
  # source changed, else kept, so its camera permission stays. Without it the bridge falls back to FFmpeg.
  "$ROOT/experiments/iphone-camera-feed/capture-app/build.sh" \
    || echo "The iPhone capture app did not build; the bridge uses FFmpeg, which may not wake the iPhone." >&2
  echo "Starting the camera bridge"
  # An isolated acceptance run must not discover or control the owner's phone.
  # Pair this with a separate stream address and data profile (docs/clean-start.md).
  local bridge_options=()
  [ "${CAMERA_NO_PHONE:-0}" = "1" ] && bridge_options+=(--no-phone)
  spawn "camera bridge" "$BRIDGE_LOG" \
    "$BRIDGE_PY" "$ROOT/experiments/iphone-camera-feed/scripts/$BRIDGE_SCRIPT" --port "$CAMERA_PORT" \
    --target-device "${CAMERA_TARGET:-iPhoneWild Camera}" --footage-dir "$FOOTAGE_DIR" --recording-dir "$RECORDINGS_DIR" \
    --datasets-dir "$DATASETS_DIR" --media-root "$SNAPSHOTS_DIR" --media-root "$RESULTS_DIR" \
    --source-file "$CAMERA_SOURCE_FILE" --default-source none --trials-dir "$LOG_DIR/camera-trials" "${bridge_options[@]}"
  wait_until 20 "the camera bridge" is_bridge

  echo "Starting the orchestrator"
  spawn "orchestrator" "$ORCH_LOG" \
    bash -c "cd '$ROOT/services' && exec '$PY' -m orchestrator.server --port '$ORCH_PORT' --bridge-url '$CAMERA_URL'"
  wait_until "${ORCH_WAIT_S:-30}" "the orchestrator" is_orchestrator  # the Mac app gives a slower first start longer

  if [ -n "$dev" ]; then
    echo "Starting the dev studio (hot reload)"
    spawn "dev studio" "$DEV_LOG" \
      bash -c "cd '$ROOT/apps/studio' && ORCHESTRATOR_URL='$ORCH_URL' exec ./node_modules/.bin/vite --host 127.0.0.1 --port '$DEV_PORT' --strictPort"
    wait_until 30 "the dev studio" is_dev_studio
  fi

  echo "Starting analysis workers (model setup is shown in Models)"
  wait_until 120 "the analysis workers" workers_ready

  echo
  if health "$CAMERA_URL/api/health" | grep -q '"ok": true'; then
    echo "ObservationUI is running on the live camera"
  else
    echo "ObservationUI is running. No camera frames yet: in the studio, add a recording or picture,"
    echo "or press Connect live camera (it opens the camera console below)."
  fi
  if [ -n "$dev" ]; then
    echo "  Studio         http://127.0.0.1:$DEV_PORT/   (hot reload; edits to apps/studio show at once)"
    echo "  Built studio   $ORCH_URL/"
  else
    echo "  Studio         $ORCH_URL/"
  fi
  echo "  Camera page    $CAMERA_URL/camera/"
  echo "  Logs           ${LOG_DIR/#$HOME/~}/ ($(basename "$BRIDGE_LOG"), $(basename "$ORCH_LOG")$([ -n "$dev" ] && echo ", $(basename "$DEV_LOG")"))"
  echo "Press Ctrl-C to stop everything (or run make stop from another terminal)."

  # Stay in the foreground; if any server exits, say which and stop the rest.
  while true; do
    for i in "${!children[@]}"; do
      if ! kill -0 "${children[$i]}" 2>/dev/null; then
        echo "The ${names[$i]} stopped. Last lines of its log:" >&2
        case "${names[$i]}" in
          "camera bridge") tail -n 15 "$BRIDGE_LOG" >&2 ;;
          orchestrator) tail -n 15 "$ORCH_LOG" >&2 ;;
          "dev studio") tail -n 15 "$DEV_LOG" >&2 ;;
        esac
        exit 1
      fi
    done
    sleep 1
  done
}

# Sourcing the script (tests) defines the functions without running a command.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    start) shift; cmd_start "${1:-}" ;;
    stop) cmd_stop ;;
    status) cmd_status ;;
    *) echo "usage: scripts/studio.sh start [--dev] | stop | status" >&2; exit 2 ;;
  esac
fi
