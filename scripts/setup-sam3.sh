#!/usr/bin/env bash
# Install SAM 3.1 (Meta's code + Apple Silicon patch) into services/.venv.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/services/vendor"
COMMIT="2345a4ad109ac29c569da749c91d84f10dc08c40"
if [ ! -d "$VENDOR/sam3/.git" ]; then
  git clone -q https://github.com/facebookresearch/sam3 "$VENDOR/sam3"
fi
cd "$VENDOR/sam3"
git fetch -q --depth 1 origin "$COMMIT" 2>/dev/null || true
git checkout -q -f "$COMMIT"
git clean -qfd sam3
git apply "$VENDOR/sam3-apple-silicon.patch"
PY="$ROOT/services/.venv/bin/python"
uv pip install --python "$PY" --no-deps -e "$VENDOR/sam3"
# Only what services/uv.lock lacks, pinned: ftfy, regex and einops come from the lock, and nothing here re-pins a locked
# package, so the next `make setup` (or the app's own sync) leaves SAM 3.1 working. Meta pins ftfy==6.1.1; its one call
# (ftfy.fix_text) is the same in the lock's version. The patch reads its vocabulary without pkg_resources (setuptools>=81).
uv pip install --python "$PY" "iopath==0.1.10" "portalocker==4.4.1" "pycocotools==2.0.11" "psutil==7.2.2"
"$PY" -c "from sam3.model_builder import build_sam3_predictor; print('SAM 3 package ready (Apple Silicon patch applied)')" 2>&1 | grep -v -i warn
