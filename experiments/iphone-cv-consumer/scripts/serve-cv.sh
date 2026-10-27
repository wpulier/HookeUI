#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${CV_HOST:-127.0.0.1}"
PORT="${CV_PORT:-8766}"
BRIDGE_URL="${CAMERA_URL:-http://127.0.0.1:8765}"
PYTHON_BIN="${CV_PYTHON:-${ROOT_DIR}/.venv/bin/python}"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "CV Python environment is missing. Run: make cv-setup" >&2
  exit 1
fi

exec "${PYTHON_BIN}" "${ROOT_DIR}/scripts/cv_consumer_server.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --bridge-url "${BRIDGE_URL}"
