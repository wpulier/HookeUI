#!/bin/bash
# Builds "MicroscopeMind Camera.app" (build/, git-ignored) when its source changed. Kept stable otherwise: macOS
# ties the camera permission to the app's signature, so an unneeded rebuild would ask for it again.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app="$here/build/MicroscopeMind Camera.app"
stamp="$here/build/.source"
want="$(cat "$here/main.swift" "$here/Info.plist" | shasum | cut -d' ' -f1)"
if [ -x "$app/Contents/MacOS/capture" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$want" ]; then exit 0; fi
command -v swiftc >/dev/null || { echo "swiftc not found: install the Xcode command line tools (xcode-select --install)" >&2; exit 1; }
mkdir -p "$app/Contents/MacOS"
cp "$here/Info.plist" "$app/Contents/Info.plist"
swiftc -O "$here/main.swift" -o "$app/Contents/MacOS/capture"
codesign --force --sign - "$app" >/dev/null 2>&1
echo "$want" >"$stamp"
echo "Built $app"
