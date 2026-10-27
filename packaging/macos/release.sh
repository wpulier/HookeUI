#!/bin/bash
# Legacy entry point: builds and publications are now separate explicit actions.
set -euo pipefail
if [ -n "${RELEASE_CHANNEL:-}" ] && [ "$RELEASE_CHANNEL" != stable ]; then
  echo "RELEASE_CHANNEL=$RELEASE_CHANNEL is set. Nothing built or published." >&2
  exit 1
fi
echo "Use packaging/macos/release-channel.py beta VERSION FULL_DEV_SHA --notes NOTES.md --evidence URL --out NEW_DIR" >&2
echo "Add --publish only when authorized; promote the accepted Beta with promote. See packaging/macos/RELEASING.md." >&2
exit 1
