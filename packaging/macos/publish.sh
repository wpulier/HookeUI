#!/bin/bash
# Publishes what build.sh made: a GitHub release people download HookeUI from, whose appcast.xml every installed copy
# of that channel reads for updates (SUFeedURL is the latest release's appcast.xml on the channel's releases
# repository; packaging/macos/channels.json). Never overwrites a release. RELEASING.md is the procedure.
#
#   packaging/macos/publish.sh                      HookeUI (GH_TOKEN with contents:write on RELEASES, or a gh login)
#   packaging/macos/publish.sh --channel beta       HookeUI Beta, to its own releases repository (the protected
#                                                   workflow, mac-app-channel.yml)
#
# Another channel is named only by --channel, never by the environment: a RELEASE_CHANNEL left exported from a
# Beta build (build.sh reads it) must not turn `make release` into a Beta release, so publish.sh refuses it.
#
# release.json goes with it (commit, build, hashes): a Stable promotion checks it releases the Beta's own commit.
# The release is made as a draft, its uploaded files are downloaded again and compared with what was built, and only
# then is it made the latest: installed copies see the new feed at once, with the dmg it names already in place.
# Afterwards the feed and dmg are fetched as anyone fetches them and compared again.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${RELEASE_CHANNEL:-}" ] && [ "$RELEASE_CHANNEL" != stable ]; then
  echo "RELEASE_CHANNEL=$RELEASE_CHANNEL is set: publish.sh takes another channel only from --channel." \
    "Nothing published." >&2
  exit 1
fi
CHANNEL=stable
if [ "${1:-}" = "--channel" ]; then CHANNEL="${2:?usage: publish.sh [--channel CHANNEL]}"; shift 2; fi
COMPANION_APPROVAL=""
if [ "${1:-}" = "--companions-sha256" ]; then COMPANION_APPROVAL="${2:?companion manifest approval required}"; shift 2; fi
[ $# -eq 0 ] || { echo "usage: publish.sh [--channel CHANNEL]" >&2; exit 2; }
CHANNEL_ENV="$(python3 "$HERE/channels.py" env "$CHANNEL")"
while IFS='=' read -r key value; do
  case "$key" in CHANNEL_*) printf -v "$key" '%s' "$value" ;; esac
done <<<"$CHANNEL_ENV"
if [ "$CHANNEL" = stable ]; then OUT="${OUT:-$HERE/build}"; else OUT="${OUT:-$HERE/build-$CHANNEL}"; fi
TOOLS="${TOOLS:-$HERE/build}"
RELEASES="${RELEASES:-$CHANNEL_RELEASES}"
FEED="https://github.com/$RELEASES/releases/latest/download/appcast.xml"
python3 "$HERE/channels.py" feed "$CHANNEL" "$FEED"  # never another channel's repository
field() { python3 -c "import json,sys; print(json.load(open('$OUT/release.json')).get(sys.argv[1], ''))" "$1"; }
VERSION="$(field version)"
DMG="$OUT/$(field dmg)"
stop() { echo "$*. Nothing published." >&2; exit 1; }
[ "$(field channel)" = "$CHANNEL" ] || stop "$OUT holds a build of the '$(field channel)' channel, not $CHANNEL"
[ "$(field bundleId)" = "$CHANNEL_BUNDLE_ID" ] || stop "$OUT's app is $(field bundleId), not $CHANNEL_BUNDLE_ID"
if [ "$(field notarized)" != "True" ] && [ "${ALLOW_UNNOTARIZED:-0}" != "1" ]; then
  stop "$CHANNEL_NAME $VERSION is not notarized: macOS would refuse to open it"
fi
[ -f "$OUT/appcast.xml" ] || stop "No appcast.xml (the update-signing key was missing at build time)"
EXPECTED="https://github.com/$RELEASES/releases/download/v$VERSION/$(basename "$DMG")"
grep -qF "url=\"$EXPECTED\"" "$OUT/appcast.xml" || stop "The appcast doesn't name $EXPECTED"
if command -v swiftc >/dev/null 2>&1; then
  swiftc -O "$HERE/verify_update.swift" -o "$OUT/verify_update"
  "$OUT/verify_update" "$CHANNEL_ED_PUBLIC_KEY" "$OUT/appcast.xml" "$DMG" || stop "Not signed by the $CHANNEL key"
else
  stop "No swiftc to check the $CHANNEL key's signatures (verify_update.swift)"
fi
if gh release view "v$VERSION" --repo "$RELEASES" >/dev/null 2>&1; then
  stop "v$VERSION is already published on $RELEASES; a release is never replaced. Build a new version"
fi
# Fail before any public write. Only this checked explicit list enters the upload.
COMPANION_PATHS="$(python3 "$HERE/release_companions.py" paths "$OUT" --approval "$COMPANION_APPROVAL")"
COMPANIONS=()
while IFS= read -r asset; do
  [ -z "$asset" ] || COMPANIONS+=("$asset")
done <<<"$COMPANION_PATHS"
FF="$TOOLS/ffmpeg/source"
(cd "$OUT" && shasum -a 256 "$(basename "$DMG")" appcast.xml) >"$OUT/SHA256SUMS"
(cd "$FF" && shasum -a 256 *.tar.xz *.tar.gz) >>"$OUT/SHA256SUMS"
NOTES_FILE="$(mktemp -t hookeui-notes)"
{
  [ -n "${NOTES:-}" ] && cat "$NOTES" && echo
  echo "Download **$(basename "$DMG")**, open it and drag $CHANNEL_NAME into Applications. Apple Silicon Macs, macOS 14 or later."
  if [ "$CHANNEL" != stable ]; then
    echo "$CHANNEL_NAME is a testing release. It installs beside HookeUI and keeps its own Projects, data and keys in ~/$(python3 -c "import json; print(json.load(open('$HERE/channels.json'))['$CHANNEL']['dataFolder'])"); it never opens HookeUI's."
  fi
  echo "$CHANNEL_NAME checks for updates automatically. Choose when to download and restart, or use $CHANNEL_NAME → Check for Updates…. Save drafts and finish active work before restarting."
  echo
  echo "The FFmpeg (LGPL-3.0-or-later), SRT (MPL-2.0), and OpenSSL (Apache-2.0) sources and build scripts are attached."
} >"$NOTES_FILE"

gh release create "v$VERSION" --repo "$RELEASES" --draft --title "$CHANNEL_NAME $VERSION" --notes-file "$NOTES_FILE" \
  "$DMG" "$OUT/appcast.xml" "$OUT/release.json" "$OUT/SHA256SUMS" "$FF"/*.tar.xz "$FF"/*.tar.gz "$FF"/*.asc "$FF"/*BUILD.txt "$FF"/*.sh ${COMPANIONS[@]+"${COMPANIONS[@]}"}
rm -f "$NOTES_FILE"
CHECK="$(mktemp -d -t hookeui-publish)"
gh release download "v$VERSION" --repo "$RELEASES" --dir "$CHECK" --pattern "$(basename "$DMG")" --pattern appcast.xml
(cd "$CHECK" && shasum -a 256 -c <(head -2 "$OUT/SHA256SUMS")) ||
  stop "The draft's files differ from the build. The draft v$VERSION on $RELEASES is left for inspection"
if [ ${#COMPANIONS[@]} -gt 0 ]; then
  PATTERNS=()
  for asset in "${COMPANIONS[@]}"; do PATTERNS+=(--pattern "$(basename "$asset")"); done
  gh release download "v$VERSION" --repo "$RELEASES" --dir "$CHECK" "${PATTERNS[@]}"
  python3 "$HERE/release_companions.py" downloaded "$OUT" --approval "$COMPANION_APPROVAL" --download-dir "$CHECK"
fi
gh release edit "v$VERSION" --repo "$RELEASES" --draft=false --latest
echo "Published: https://github.com/$RELEASES/releases/tag/v$VERSION"

# As anyone fetches it (the CDN may lag a little).
for attempt in 1 2 3 4 5 6; do
  rm -f "$CHECK/anon-appcast.xml" "$CHECK/anon.dmg"
  if curl -fsSL -o "$CHECK/anon-appcast.xml" "$FEED" && cmp -s "$CHECK/anon-appcast.xml" "$OUT/appcast.xml" &&
    curl -fsSL -o "$CHECK/anon.dmg" "$EXPECTED" && cmp -s "$CHECK/anon.dmg" "$DMG" &&
    python3 "$HERE/release_companions.py" anonymous "$OUT" --approval "$COMPANION_APPROVAL" --url "https://github.com/$RELEASES/releases/download/v$VERSION"; then
    echo "Checked as anyone fetches it: $FEED offers $VERSION, and its dmg matches the build."
    rm -rf "$CHECK"
    exit 0
  fi
  sleep 20
done
echo "PUBLISHED BUT NOT CONFIRMED: $FEED or $EXPECTED did not match the build when fetched anonymously." >&2
echo "Follow RELEASING.md \"A bad or unconfirmed offer\" before anything else." >&2
exit 2
