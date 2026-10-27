#!/bin/bash
# Stops a bad offer on one channel (RELEASING.md "A bad or unconfirmed offer"): the channel's feed is the latest
# release's appcast.xml, so making an earlier, verified release the latest again withdraws the bad one from every copy
# that has not installed it. Nothing is deleted: the bad release stays (marked withdrawn, a prerelease, so it never
# becomes the latest again) as incident evidence, and copies that installed it keep it until a newer fixed build.
#
#   packaging/macos/feed-recover.sh CHANNEL GOOD_VERSION BAD_VERSION "why"
#
# GH_TOKEN: contents:write on the channel's releases repository. Needs swiftc (verify_update.swift).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USAGE="usage: feed-recover.sh CHANNEL GOOD_VERSION BAD_VERSION REASON"
CHANNEL="${1:?$USAGE}"
GOOD="${2:?$USAGE}"
BAD="${3:?$USAGE}"
REASON="${4:?$USAGE}"
CHANNEL_ENV="$(python3 "$HERE/channels.py" env "$CHANNEL")"
while IFS='=' read -r key value; do
  case "$key" in CHANNEL_*) printf -v "$key" '%s' "$value" ;; esac
done <<<"$CHANNEL_ENV"
RELEASES="$CHANNEL_RELEASES"
FEED="https://github.com/$RELEASES/releases/latest/download/appcast.xml"
WORK="$(mktemp -d -t hookeui-recover)"
stop() { echo "$*. Nothing changed." >&2; exit 1; }
[ "$GOOD" != "$BAD" ] || stop "The good and bad versions are the same"

state() { gh release view "v$1" --repo "$RELEASES" --json isDraft,isPrerelease --jq '"\(.isDraft) \(.isPrerelease)"'; }
[ "$(state "$GOOD")" = "false false" ] || stop "v$GOOD is not a published release on $RELEASES"
state "$BAD" >/dev/null || stop "v$BAD is not a release on $RELEASES"

# The release going back on offer must still verify against the channel's key, as published.
gh release download "v$GOOD" --repo "$RELEASES" --dir "$WORK/good" --pattern appcast.xml --pattern '*.dmg'
swiftc -O "$HERE/verify_update.swift" -o "$WORK/verify_update"
"$WORK/verify_update" "$CHANNEL_ED_PUBLIC_KEY" "$WORK/good/appcast.xml" "$WORK/good/"*.dmg ||
  stop "v$GOOD does not verify against the $CHANNEL key"
build() { python3 -c "import sys,xml.etree.ElementTree as E; print(E.parse(sys.argv[1]).find('.//{http://www.andymatuschak.org/xml-namespaces/sparkle}version').text)" "$1"; }
gh release download "v$BAD" --repo "$RELEASES" --dir "$WORK/bad" --pattern appcast.xml
GOOD_BUILD="$(build "$WORK/good/appcast.xml")"
BAD_BUILD="$(build "$WORK/bad/appcast.xml")"

notes="$(gh release view "v$BAD" --repo "$RELEASES" --json body --jq .body)"
printf '**Withdrawn %s:** %s\n\n%s\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "$REASON" "$notes" >"$WORK/notes.md"
gh release edit "v$BAD" --repo "$RELEASES" --prerelease --latest=false --notes-file "$WORK/notes.md"
gh release edit "v$GOOD" --repo "$RELEASES" --latest

for attempt in 1 2 3 4 5 6; do
  if curl -fsSL -o "$WORK/now.xml" "$FEED" && cmp -s "$WORK/now.xml" "$WORK/good/appcast.xml"; then
    echo "The $CHANNEL feed offers v$GOOD (build $GOOD_BUILD) again; v$BAD (build $BAD_BUILD) is withdrawn, not deleted."
    echo "Copies that installed v$BAD keep it: release a fix with a build number above $BAD_BUILD (release_number.py"
    echo "reads every release, withdrawn ones too). Record this in the release ticket."
    exit 0
  fi
  sleep 20
done
echo "The feed at $FEED does not yet match v$GOOD's appcast. Check it again before anything else." >&2
exit 2
