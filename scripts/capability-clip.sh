#!/usr/bin/env bash
# A preview for a capability's card on the Capabilities page (ADR 0007): a few silent seconds cut
# from a recording, or a still, into docs/capabilities/media/<id>.<mp4|jpg>, and the capability's
# "media" set in contract/capabilities.json.
#   make capability-clip ID=zoom FROM=recordings/demos/.../studio-....mp4 START=822 DUR=8
#   make capability-clip ID=track-motion FROM=services/bench/results/motion-....jpg
# A studio recording is cropped to its left part by default (CROP), so the chat (what was said)
# is never in a preview; CROP is an ffmpeg crop (w:h:x:y).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ID="${ID:?ID= the capability}"
FROM="${FROM:?FROM= a recording or a picture}"
CROP="${CROP:-iw*0.78:ih:0:0}"
OUT="$REPO/docs/capabilities/media"
mkdir -p "$OUT"
case "${FROM##*.}" in
  jpg|jpeg|png|webp)
    NAME="$ID.jpg"
    ffmpeg -v error -y -i "$FROM" -vf "scale='min(960,iw)':-2" -q:v 4 "$OUT/$NAME" ;;
  *)
    NAME="$ID.mp4"
    ffmpeg -v error -y -ss "${START:?START= seconds into the recording}" -t "${DUR:-6}" -i "$FROM" -an \
      -vf "crop=$CROP,scale=960:-2,fps=12" -c:v libx264 -preset slow -crf 30 -pix_fmt yuv420p -movflags +faststart "$OUT/$NAME" ;;
esac
"$REPO/services/.venv/bin/python" - "$REPO/contract/capabilities.json" "$ID" "$NAME" <<'PY'
import json, sys
path, cid, name = sys.argv[1:4]
data = json.load(open(path))
match = [c for c in data["capabilities"] if c["id"] == cid]
if not match:
    sys.exit(f"there is no capability {cid}")
match[0]["media"] = name
json.dump(data, open(path, "w"), indent=1, ensure_ascii=False)
PY
echo "$OUT/$NAME ($(du -h "$OUT/$NAME" | cut -f1)) is the preview of $ID"
