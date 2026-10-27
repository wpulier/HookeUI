#!/bin/bash
# The Mac app's FFmpeg: a static LGPLv3 build (no --enable-gpl or nonfree) that links dynamically only
# macOS's own frameworks and libraries. H.264 is Apple's VideoToolbox; the camera recorders choose it when libx264
# is absent (bridge_common.h264_encoder). Its source is checked against FFmpeg's release signing key.
#   packaging/macos/ffmpeg.sh OUT_DIR      writes OUT_DIR/bin/{ffmpeg,ffprobe} and OUT_DIR/source/ (what was built)
set -euo pipefail
VERSION=7.1.5
SHA256=de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f
KEY=FCF986EA15E6E293A5644F10B4322F04D67658D8  # FFmpeg release signing key <ffmpeg-devel@ffmpeg.org>
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECIPE="$(cat "$HERE/ffmpeg.sh" "$HERE/stream-deps.sh" | shasum -a 256 | cut -d' ' -f1)"
if [ -x "$OUT/bin/ffmpeg" ] && [ -x "$OUT/bin/ffprobe" ] && [ "$(cat "$OUT/.recipe" 2>/dev/null || true)" = "$RECIPE" ]; then
  "$OUT/bin/ffmpeg" -hide_banner -protocols 2>/dev/null | grep -qx '  srt' && exit 0
fi
PKG_CONFIG_BIN="$(command -v pkg-config)"

work="$OUT/work"
rm -rf "$work"
mkdir -p "$work" "$OUT/source"
"$HERE/stream-deps.sh" "$OUT" "$work"
cd "$work"
curl -fsSLO "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz"
curl -fsSLO "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz.asc"
echo "$SHA256  ffmpeg-$VERSION.tar.xz" | shasum -a 256 -c -
if command -v gpg >/dev/null; then
  export GNUPGHOME="$work/gnupg"
  mkdir -m 700 "$GNUPGHOME"
  # Importing can exit non-zero when gpg-agent can't start (a long path); the VALIDSIG check below is what counts.
  curl -fsSL https://ffmpeg.org/ffmpeg-devel.asc | gpg --quiet --import 2>/dev/null || true
  gpg --status-fd 1 --verify "ffmpeg-$VERSION.tar.xz.asc" "ffmpeg-$VERSION.tar.xz" 2>/dev/null | grep -q "VALIDSIG $KEY" \
    || { echo "ffmpeg-$VERSION.tar.xz is not signed by FFmpeg's release key" >&2; exit 1; }
fi
tar xf "ffmpeg-$VERSION.tar.xz"
cd "ffmpeg-$VERSION"
# Only the pinned static dependencies; never discover Homebrew libraries.
export PKG_CONFIG_LIBDIR="$work/deps/lib/pkgconfig" PKG_CONFIG_PATH= PATH=/usr/bin:/bin:/usr/sbin:/sbin
CONFIGURE=(--prefix="$work/prefix" --arch=arm64 --cc=clang --disable-autodetect --disable-doc --disable-debug
  --enable-version3 --enable-libsrt --pkg-config="$PKG_CONFIG_BIN" --pkg-config-flags=--static
  --extra-cxxflags=-mmacosx-version-min=14.0 --disable-ffplay --disable-shared --enable-static --enable-pthreads --enable-videotoolbox --enable-audiotoolbox
  --enable-avfoundation --enable-zlib --enable-bzlib --enable-iconv --enable-neon
  --extra-cflags=-mmacosx-version-min=14.0 --extra-ldflags=-mmacosx-version-min=14.0 --extra-libs="-liconv -lc++")
./configure "${CONFIGURE[@]}" >"$work/configure.log"
grep -q "^License: LGPL version 3 or later" "$work/configure.log" || { echo "not an LGPL configuration" >&2; exit 1; }
make -j"${HOOKE_BUILD_JOBS:-4}" >"$work/make.log" 2>&1
make install >"$work/install.log"
for tool in ffmpeg ffprobe; do
  if otool -L "$work/prefix/bin/$tool" | tail -n +2 | grep -v -E "^\s+(/usr/lib/|/System/Library/)"; then
    echo "$tool links a library macOS doesn't have" >&2; exit 1
  fi
done
mkdir -p "$OUT/bin"
cp "$work/prefix/bin/ffmpeg" "$work/prefix/bin/ffprobe" "$OUT/bin/"
# What the LGPL asks for: its licence, the exact source and how it was configured.
cp "$work/ffmpeg-$VERSION.tar.xz" "$work/ffmpeg-$VERSION.tar.xz.asc" "$OUT/source/"
cp COPYING.LGPLv3 COPYING.GPLv3 "$OUT/source/"
"$OUT/bin/ffmpeg" -hide_banner -protocols 2>/dev/null | grep -qx '  srt'
printf '%s\n' "$RECIPE" >"$OUT/.recipe"
cp "$HERE/ffmpeg.sh" "$OUT/source/ffmpeg-build.sh"
printf '%s\n' "FFmpeg $VERSION, sha256 $SHA256, configured with:" "./configure ${CONFIGURE[*]}" >"$OUT/source/BUILD.txt"
rm -rf "$work"
echo "FFmpeg $VERSION (LGPL) in $OUT/bin"
