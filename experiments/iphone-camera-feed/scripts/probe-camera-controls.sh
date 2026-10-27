#!/usr/bin/env bash
set -euo pipefail

DEVICE="${1:-iPhoneWild Camera}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

swift "${ROOT_DIR}/scripts/probe_camera_controls.swift" "${DEVICE}"

