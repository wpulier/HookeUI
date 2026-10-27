#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUDY_HOME="${STUDY_HOME:-$HOME/MicroscopeMind/workspace-study-preview}"
if [ ! -f "$ROOT/.git" ]; then
  echo "Run this preview from an isolated Git worktree, not the live checkout." >&2
  exit 1
fi
cd "$ROOT"
exec env -i HOME="$HOME" PATH="$PATH" USER="${USER:-}" TMPDIR="${TMPDIR:-/tmp}" \
  MICROMIND_HOME="$STUDY_HOME/profile" MICROMIND_ASSETS="$STUDY_HOME/assets" \
  MICROMIND_CATALOG="$STUDY_HOME/catalog" MICROMIND_MODEL_FILES="$STUDY_HOME/model-cache" \
  MICROMIND_RECORDINGS_DIR="$STUDY_HOME/profile/recordings" \
  MICROMIND_KEYS_FILE=/dev/null MICROMIND_KEYCHAIN=off \
  MICROMIND_CATALOG_AUTO=0 HF_HUB_OFFLINE=1 MICROMIND_WORKSPACE_STUDY=1 \
  HF_HOME="$STUDY_HOME/huggingface" TORCH_HOME="$STUDY_HOME/torch" \
  CAMERA_PORT=19765 ORCH_PORT=19770 DEV_PORT=19180 CV_PORT=19766 \
  CAMERA_NO_PHONE=1 MICROMIND_CAMERA_SOURCE='srt://127.0.0.1:19710?mode=listener&latency=20000' \
  ./scripts/studio.sh "${1:-start}" "${@:2}"
