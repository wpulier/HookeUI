#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8765}"
TARGET_DEVICE="${TARGET_DEVICE:-iPhoneWild Camera}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec python3 "${ROOT_DIR}/scripts/iphone_bridge_server.py" \
  --port "${PORT}" \
  --target-device "${TARGET_DEVICE}"
