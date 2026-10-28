#!/bin/bash
# Builds HookeUI.app and HookeUI-<version>.dmg from one commit (packaging/macos/README.md).
#
#   packaging/macos/build.sh VERSION [REV]
#   RELEASE_CHANNEL=beta packaging/macos/build.sh VERSION [REV]     HookeUI Beta.app, HookeUI-Beta-<version>.dmg
#
# RELEASE_CHANNEL (default stable): which app of packaging/macos/channels.json, with its own name, bundle identifier,
# folders, ports, Keychain service, releases repository, feed and update key. Building publishes nothing (publish.sh
# does).
# OUT (default packaging/macos/build, or build-<channel>) holds the results; TOOLS (default packaging/macos/build)
# keeps FFmpeg, uv and Sparkle between builds of every channel.
# SIGN_IDENTITY: the Developer ID that signs everything. NOTARY_PROFILE (default "notary"): the notarytool keychain
# profile (NOTARY_KEYCHAIN: a keychain other than the login one, as in CI); without it the app and dmg are signed but
# not notarized, and the script says so. ICNS: optional custom app icon supplied by the builder. HOOKEUI_MARK_DARK and
# HOOKEUI_MARK_LIGHT: optional SVG marks for the studio's menu button, supplied the same way (apps/studio/vite.config.ts;
# without them the core's mark). SPARKLE_KEY_FILE (default
# ~/.config/hookeui-release/sparkle_ed25519.key, or sparkle_ed25519-<channel>.key): the channel's update-signing key;
# NOTES: this version's release notes.
# FEED_URL: where installed apps look for updates (the latest GitHub release's appcast.xml on the channel's releases
# repository). ED_PUBLIC_KEY_FILE: the update key's public half the app trusts (default: the channel's, channels.json).
# The appcast is signed with the channel's key and checked against the public half its app trusts (verify_update.swift).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VERSION="${1:?usage: build.sh VERSION [REV]}"
REV="$(git -C "$REPO" rev-parse --verify "${2:-HEAD}^{commit}")"
BUILD_NUMBER="${BUILD_NUMBER:-$(git -C "$REPO" rev-list --count "$REV")}"  # what Sparkle compares: always higher
CHANNEL="${RELEASE_CHANNEL:-stable}"
CHANNEL_ENV="$(python3 "$HERE/channels.py" env "$CHANNEL")"  # KEY=VALUE lines, read without evaluating them
while IFS='=' read -r key value; do
  case "$key" in CHANNEL_*) printf -v "$key" '%s' "$value" ;; esac
done <<<"$CHANNEL_ENV"
TOOLS="${TOOLS:-$HERE/build}"
if [ "$CHANNEL" = stable ]; then OUT="${OUT:-$HERE/build}"; else OUT="${OUT:-$HERE/build-$CHANNEL}"; fi
IDENTITY="${SIGN_IDENTITY:-Developer ID Application: WILLIAM PULIER (SY7TABCD5M)}"
PROFILE="${NOTARY_PROFILE:-notary}"
UV_VERSION=0.7.19
UV_SHA256=698d24883fd441960fb4bc153b7030b89517a295502017ff3fdbba2fb0a0aa67
SPARKLE_VERSION=2.10.0
SPARKLE_SHA256=c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c
RELEASES="${RELEASES:-$CHANNEL_RELEASES}"
FEED_URL="${FEED_URL:-https://github.com/$RELEASES/releases/latest/download/appcast.xml}"
python3 "$HERE/channels.py" feed "$CHANNEL" "$FEED_URL"  # never another channel's feed
if [ "$CHANNEL" = stable ]; then KEY_NAME=sparkle_ed25519.key; else KEY_NAME="sparkle_ed25519-$CHANNEL.key"; fi
SPARKLE_KEY_FILE="${SPARKLE_KEY_FILE:-$HOME/.config/hookeui-release/$KEY_NAME}"
ED_PUBLIC_KEY_FILE="${ED_PUBLIC_KEY_FILE:-$CHANNEL_ED_PUBLIC_KEY}"
[ -s "$ED_PUBLIC_KEY_FILE" ] || {
  echo "No update key for the $CHANNEL channel at $ED_PUBLIC_KEY_FILE (README \"Channels\"). For a test build, give" \
    "ED_PUBLIC_KEY_FILE a throwaway key's public half." >&2
  exit 1
}
ED_PUBLIC="$(cat "$ED_PUBLIC_KEY_FILE")"
APP="$OUT/$CHANNEL_NAME.app"
DMG="$OUT/$CHANNEL_FILE_STEM-$VERSION.dmg"
SRC="$OUT/src"
say() { printf '\n== %s\n' "$*"; }

mkdir -p "$OUT"
mkdir -p "$TOOLS"
rm -rf "$OUT/snapshot" "$APP" "$OUT/dmg" "$OUT/$CHANNEL_FILE_STEM.zip" "$DMG" "$OUT/release.json" "$OUT/appcast.xml"
git -C "$REPO" worktree remove --force "$SRC" >/dev/null 2>&1 || rm -rf "$SRC"
trap 'git -C "$REPO" worktree remove --force "$SRC" >/dev/null 2>&1 || true' EXIT

say "The studio, built from a clean checkout of $REV"
git -C "$REPO" worktree add --quiet --detach "$SRC" "$REV"
# vite.config.ts asks the services' Python for the build identity (stdlib only).
uv venv --quiet --python 3.13 "$SRC/services/.venv"
(cd "$SRC/apps/studio" && npm ci --no-audit --no-fund --loglevel=error && npm run --silent build)

say "The source snapshot"
python3 "$HERE/snapshot.py" --rev "$REV" --out "$OUT/snapshot" --identity "$SRC/apps/studio/dist/build.json" \
  --app-version "$VERSION"
TREE="$OUT/snapshot/tree"
mkdir -p "$TREE/apps/studio"
cp -R "$SRC/apps/studio/dist" "$SRC/apps/studio/dist-camera" "$TREE/apps/studio/"
CAP="$TREE/experiments/iphone-camera-feed/capture-app"
mkdir -p "$CAP/build"
# capture-app/build.sh's stamp: the app copied in at first launch is this source's build, so it is never rebuilt.
cat "$CAP/main.swift" "$CAP/Info.plist" | shasum | cut -d' ' -f1 >"$CAP/build/.source"
python3 "$HERE/finalize_snapshot.py" "$OUT/snapshot"
if find "$TREE" -type f -print0 | xargs -0 file | grep -q "Mach-O"; then
  echo "The snapshot holds a binary; code ships only as signed helpers" >&2; exit 1
fi

say "FFmpeg (LGPL) and uv $UV_VERSION"
"$HERE/ffmpeg.sh" "$TOOLS/ffmpeg"
python3 "$HERE/verify_receiver.py" "$TOOLS/ffmpeg/bin/ffmpeg" --smoke
if [ ! -x "$TOOLS/uv/uv" ]; then
  mkdir -p "$TOOLS/uv"
  curl -fsSL -o "$TOOLS/uv/uv.tar.gz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz"
  echo "$UV_SHA256  $TOOLS/uv/uv.tar.gz" | shasum -a 256 -c -
  tar -xzf "$TOOLS/uv/uv.tar.gz" -C "$TOOLS/uv" --strip-components 1
  curl -fsSL -o "$TOOLS/uv/LICENSE-MIT" "https://raw.githubusercontent.com/astral-sh/uv/$UV_VERSION/LICENSE-MIT"
  curl -fsSL -o "$TOOLS/uv/LICENSE-APACHE" "https://raw.githubusercontent.com/astral-sh/uv/$UV_VERSION/LICENSE-APACHE"
fi

if [ ! -d "$TOOLS/sparkle/Sparkle.framework" ]; then
  rm -rf "$TOOLS/sparkle" && mkdir -p "$TOOLS/sparkle"
  curl -fsSL -o "$TOOLS/sparkle/Sparkle.tar.xz" \
    "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz"
  echo "$SPARKLE_SHA256  $TOOLS/sparkle/Sparkle.tar.xz" | shasum -a 256 -c -
  tar -xf "$TOOLS/sparkle/Sparkle.tar.xz" -C "$TOOLS/sparkle"
fi

say "The app's own Python, made as a first launch makes it and measured (runtime_download.py)"
# packaged.json's runtimeDownload: the first launch's question says these numbers (SetupWords.swift). packaged.json is
# outside the content hash, so this changes no runtime identity.
"$SRC/services/.venv/bin/python" -I "$HERE/runtime_download.py" --uv "$TOOLS/uv/uv" --services "$TREE/services" \
  --packaged "$TREE/packaged.json" --work "$OUT/runtime-measure"

say "$CHANNEL_NAME.app ($CHANNEL channel, $CHANNEL_BUNDLE_ID)"
C="$APP/Contents"
mkdir -p "$C/MacOS" "$C/Resources/NOTICES" "$C/Helpers" "$C/Frameworks"
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -F "$TOOLS/sparkle" -framework Sparkle \
  -Xlinker -rpath -Xlinker @executable_path/../Frameworks "$HERE/HookeUI/"*.swift -o "$C/MacOS/HookeUI"
ditto "$TOOLS/sparkle/Sparkle.framework" "$C/Frameworks/Sparkle.framework"
# The command an outside agent runs to use this app (ADR 0026; packaging/macos/hooke): it reads this bundle's Info.plist.
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 "$HERE/hooke/main.swift" "$HERE/HookeUI/AgentShared.swift" \
  -o "$C/Helpers/hooke"
python3 "$HERE/channels.py" plist "$CHANNEL" --template "$HERE/HookeUI/Info.plist" --version "$VERSION" \
  --build "$BUILD_NUMBER" --feed "$FEED_URL" --edkey "$ED_PUBLIC" --out "$C/Info.plist"
if [ -n "${ICNS:-}" ]; then
  cp "$ICNS" "$C/Resources/AppIcon.icns"
else
  iconset="$OUT/AppIcon.iconset"
  rm -rf "$iconset" && mkdir -p "$iconset"
  swift "$HERE/placeholder-icon.swift" "$OUT/icon-1024.png"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$OUT/icon-1024.png" --out "$iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$OUT/icon-1024.png" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$C/Resources/AppIcon.icns"
fi
ditto "$TREE" "$C/Resources/app"
cp "$TOOLS/uv/uv" "$TOOLS/ffmpeg/bin/ffmpeg" "$TOOLS/ffmpeg/bin/ffprobe" "$C/Helpers/"
CAMERA="$C/Helpers/MicroscopeMind Camera.app"
mkdir -p "$CAMERA/Contents/MacOS"
swiftc -O -target arm64-apple-macos14.0 "$CAP/main.swift" -o "$CAMERA/Contents/MacOS/capture"
python3 "$HERE/channels.py" plist "$CHANNEL" --template "$HERE/HookeUI/Camera-Info.plist" --version "$VERSION" \
  --build "$BUILD_NUMBER" --out "$CAMERA/Contents/Info.plist"
cp "$HERE/NOTICES.md" "$C/Resources/NOTICES/"
cp "$TOOLS/ffmpeg/source/COPYING.LGPLv3" "$C/Resources/NOTICES/FFmpeg-COPYING.LGPLv3"
cp "$TOOLS/ffmpeg/source/COPYING.GPLv3" "$C/Resources/NOTICES/FFmpeg-COPYING.GPLv3"
cp "$TOOLS/ffmpeg/source/SRT-LICENSE.txt" "$TOOLS/ffmpeg/source/OpenSSL-LICENSE.txt" "$TOOLS/ffmpeg/source/STREAM-BUILD.txt" "$C/Resources/NOTICES/"
cp "$TOOLS/ffmpeg/source/BUILD.txt" "$C/Resources/NOTICES/FFmpeg-BUILD.txt"
cp "$TOOLS/uv/LICENSE-MIT" "$C/Resources/NOTICES/uv-LICENSE-MIT"
cp "$TOOLS/uv/LICENSE-APACHE" "$C/Resources/NOTICES/uv-LICENSE-APACHE"
cp "$TOOLS/sparkle/LICENSE" "$C/Resources/NOTICES/Sparkle-LICENSE"

say "Signing with $IDENTITY"
sign() { codesign --force --timestamp --options runtime --sign "$IDENTITY" "$@"; }
sign "$C/Helpers/uv" "$C/Helpers/hooke"
sign --entitlements "$HERE/HookeUI/HookeUI.entitlements" "$C/Helpers/ffmpeg" "$C/Helpers/ffprobe"
sign --entitlements "$HERE/HookeUI/camera.entitlements" "$CAMERA"
# Sparkle's own parts, inside out, as its documentation signs them for a Developer ID app.
SP="$C/Frameworks/Sparkle.framework/Versions/B"
sign "$SP/XPCServices/Installer.xpc"
sign --preserve-metadata=entitlements "$SP/XPCServices/Downloader.xpc"
sign "$SP/Autoupdate" "$SP/Updater.app"
sign "$C/Frameworks/Sparkle.framework"
sign --entitlements "$HERE/HookeUI/HookeUI.entitlements" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

notarize() { # file: submit, wait, and fail unless Apple accepted it
  local out id
  out="$(xcrun notarytool submit "$1" --keychain-profile "$PROFILE" ${NOTARY_KEYCHAIN:+--keychain "$NOTARY_KEYCHAIN"} --wait 2>&1)"
  echo "$out"
  id="$(echo "$out" | sed -n 's/^  id: //p' | head -1)"
  if ! echo "$out" | grep -q "status: Accepted"; then
    [ -n "$id" ] && xcrun notarytool log "$id" --keychain-profile "$PROFILE" ${NOTARY_KEYCHAIN:+--keychain "$NOTARY_KEYCHAIN"} || true
    echo "Apple did not accept $1" >&2; exit 1
  fi
}
NOTARIZED=0
if xcrun notarytool history --keychain-profile "$PROFILE" ${NOTARY_KEYCHAIN:+--keychain "$NOTARY_KEYCHAIN"} >/dev/null 2>&1; then
  say "Notarizing the app"
  ditto -c -k --keepParent "$APP" "$OUT/$CHANNEL_FILE_STEM.zip"
  notarize "$OUT/$CHANNEL_FILE_STEM.zip"
  xcrun stapler staple "$APP"
  NOTARIZED=1
else
  say "No notarytool profile \"$PROFILE\" in the keychain: the app and dmg are signed but NOT notarized"
fi

say "The disk image"
mkdir -p "$OUT/dmg"
ditto "$APP" "$OUT/dmg/$CHANNEL_NAME.app"
ln -s /Applications "$OUT/dmg/Applications"
hdiutil create -quiet -volname "$CHANNEL_NAME" -srcfolder "$OUT/dmg" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$DMG"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"
if [ "$NOTARIZED" = 1 ]; then
  say "Notarizing the disk image"
  notarize "$DMG"
  xcrun stapler staple "$DMG"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
  spctl --assess --type execute --verbose=2 "$APP"
fi

say "The update feed"
if [ -f "$SPARKLE_KEY_FILE" ]; then
  SIGNATURE="$("$TOOLS/sparkle/bin/sign_update" --ed-key-file "$SPARKLE_KEY_FILE" -p "$DMG")"
  python3 "$HERE/appcast.py" --dmg "$DMG" --version "$VERSION" --build "$BUILD_NUMBER" --signature "$SIGNATURE" \
    --url "https://github.com/$RELEASES/releases/download/v$VERSION/$(basename "$DMG")" ${NOTES:+--notes "$NOTES"} \
    --title "$CHANNEL_NAME" --out "$OUT/appcast.xml"
  # The feed is signed too (SURequireSignedFeed): an installed app reads nothing else.
  "$TOOLS/sparkle/bin/sign_update" --ed-key-file "$SPARKLE_KEY_FILE" "$OUT/appcast.xml"
  # Signed by the key this channel's app trusts, never another channel's (an app refuses an update it can't verify).
  swiftc -O "$HERE/verify_update.swift" -o "$OUT/verify_update"
  "$OUT/verify_update" "$ED_PUBLIC_KEY_FILE" "$OUT/appcast.xml" "$DMG"
else
  echo "No update-signing key at $SPARKLE_KEY_FILE: no appcast.xml, so this build can't be published as an update" >&2
fi

python3 - "$OUT" "$DMG" "$APP" "$VERSION" "$REV" "$NOTARIZED" "$BUILD_NUMBER" "$CHANNEL" "$CHANNEL_BUNDLE_ID" <<'EOF'
import hashlib, json, os, subprocess, sys
out, dmg, app, version, rev, notarized, build, channel, bundle = sys.argv[1:]
size = lambda p: int(subprocess.check_output(["du", "-sk", p]).split()[0]) * 1024
report = {"channel": channel, "bundleId": bundle, "version": version, "build": int(build), "revision": rev, "appcast": os.path.exists(os.path.join(out, "appcast.xml")), "notarized": notarized == "1", "dmg": os.path.basename(dmg),
          "dmgBytes": os.path.getsize(dmg), "dmgSha256": hashlib.sha256(open(dmg, "rb").read()).hexdigest(),
          "appBytes": size(app), "snapshot": json.load(open(os.path.join(out, "snapshot/report.json")))["contentHash"]}
json.dump(report, open(os.path.join(out, "release.json"), "w"), indent=1)
print(json.dumps(report, indent=1))
EOF
