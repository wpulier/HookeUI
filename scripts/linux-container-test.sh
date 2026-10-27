#!/bin/bash
# Run the backend test suite in a headless Ubuntu 24.04 container (packaging/linux/test-container/README.md).
# Usage: scripts/linux-container-test.sh <out-dir> [git-ref]   (default ref: HEAD; uncommitted changes are not tested)
# Needs Docker. Take the heavy-run slot first when other sessions share the machine (.scratch board, section 1).
set -euo pipefail
out=$(mkdir -p "$1" && cd "$1" && pwd); ref=${2:-HEAD}
root=$(git rev-parse --show-toplevel); cd "$root"
platform=${PLATFORM:-linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}
docker build -q --platform "$platform" -f packaging/linux/test-container/Dockerfile -t hooke-linux-test:local services > "$out/image.txt"
git rev-parse "$ref" > "$out/commit.txt"
git archive --format=tar "$ref" | docker run --rm -i --platform "$platform" --cpus "${CPUS:-6}" --memory "${MEMORY:-10g}" \
  -e JOBS="${JOBS:-4}" -e PYTEST_ARGS="${PYTEST_ARGS:-}" -v "$out:/out" \
  --entrypoint bash hooke-linux-test:local -c "$(cat packaging/linux/test-container/run-tests.sh)"
