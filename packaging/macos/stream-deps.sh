#!/bin/bash
# Build-only dependencies for the portable receiver. No Homebrew libraries enter the app.
# SRT retains encryption support; OpenSSL 3 requires FFmpeg's LGPLv3 configuration.
set -euo pipefail
OUT="$1"; WORK="$2"
SRT_VERSION=1.5.4
SRT_SHA=d0a8b600fe1b4eaaf6277530e3cfc8f15b8ce4035f16af4a5eb5d4b123640cdd
SSL_VERSION=3.5.9
SSL_SHA=603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
JOBS="${HOOKE_BUILD_JOBS:-4}"
mkdir -p "$WORK" "$OUT/source"
fetch() {
  local name="$1" url="$2" sha="$3"
  if [ ! -f "$OUT/source/$name" ]; then curl -fL --retry 2 "$url" -o "$OUT/source/$name"; fi
  (cd "$OUT/source" && echo "$sha  $name" | shasum -a 256 -c -)
  tar xf "$OUT/source/$name" -C "$WORK"
}
fetch "openssl-$SSL_VERSION.tar.gz" "https://github.com/openssl/openssl/releases/download/openssl-$SSL_VERSION/openssl-$SSL_VERSION.tar.gz" "$SSL_SHA"
fetch "srt-$SRT_VERSION.tar.gz" "https://codeload.github.com/Haivision/srt/tar.gz/refs/tags/v$SRT_VERSION" "$SRT_SHA"
PREFIX="$WORK/deps"
(
  cd "$WORK/openssl-$SSL_VERSION"
  ./Configure darwin64-arm64-cc no-shared no-tests no-module --prefix="$PREFIX" --libdir=lib -mmacosx-version-min=14.0 >"$WORK/openssl-configure.log"
  make -j"$JOBS" >"$WORK/openssl-make.log" 2>&1
  make install_sw >"$WORK/openssl-install.log" 2>&1
)
# uv supplies a pinned build tool only on the build host, never on a user's Mac.
cmake() { uv tool run --from cmake==3.31.6 cmake "$@"; }
cmake -S "$WORK/srt-$SRT_VERSION" -B "$WORK/srt-build" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 \
  -DENABLE_APPS=OFF -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DENABLE_ENCRYPTION=ON \
  -DUSE_OPENSSL_PC=OFF -DOPENSSL_ROOT_DIR="$PREFIX" -DOPENSSL_USE_STATIC_LIBS=TRUE \
  -DOPENSSL_CRYPTO_LIBRARY="$PREFIX/lib/libcrypto.a" -DOPENSSL_SSL_LIBRARY="$PREFIX/lib/libssl.a" \
  -DOPENSSL_INCLUDE_DIR="$PREFIX/include" >"$WORK/srt-configure.log"
cmake --build "$WORK/srt-build" --parallel "$JOBS" >"$WORK/srt-make.log" 2>&1
cmake --install "$WORK/srt-build" >"$WORK/srt-install.log"
cp "$WORK/openssl-$SSL_VERSION/LICENSE.txt" "$OUT/source/OpenSSL-LICENSE.txt"
cp "$WORK/srt-$SRT_VERSION/LICENSE" "$OUT/source/SRT-LICENSE.txt"
cp "$0" "$OUT/source/stream-deps.sh"
printf '%s\n' "SRT $SRT_VERSION sha256 $SRT_SHA; static, encryption enabled" \
  "OpenSSL $SSL_VERSION sha256 $SSL_SHA; static; darwin64-arm64; macOS 14 minimum" >"$OUT/source/STREAM-BUILD.txt"
