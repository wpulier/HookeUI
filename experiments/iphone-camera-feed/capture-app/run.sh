#!/bin/bash
# The bridge's Continuity capture: launches "MicroscopeMind Camera.app" through LaunchServices (so macOS sees an
# app and wakes the iPhone for it) with its frames on a pipe, and copies them to this script's stdout, so the
# bridge reads it as it read FFmpeg. Stopping this script stops the app.
#   run.sh DEVICE SIZE FPS QUALITY
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app="$here/build/MicroscopeMind Camera.app"
work="$(mktemp -d -t microscopemind-capture)"
fifo="$work/frames"
mkfifo "$fifo"
token="mm-capture-$$"
tail_pid=""
stop() {
  pkill -f -- "$token" 2>/dev/null
  [ -n "$tail_pid" ] && kill "$tail_pid" 2>/dev/null  # it would follow the deleted log for ever (one leaked per restart)
  rm -rf "$work"
}
trap 'stop; exit 0' TERM INT HUP
trap stop EXIT
: >"$work/log"
tail -n +1 -f "$work/log" >&2 &
tail_pid=$!
open -g -j -n --stdout "$fifo" --stderr "$work/log" "$app" --args --device "$1" --size "$2" --fps "$3" --quality "$4" --token "$token"
cat "$fifo" &
wait $!
