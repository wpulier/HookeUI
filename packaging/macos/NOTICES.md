# Third-party software in HookeUI.app

## Shipped inside the app

- **FFmpeg 7.1.5** (`Contents/Helpers/ffmpeg`, `ffprobe`), licensed under the GNU Lesser General Public License,
  version 3 or later: FFmpeg-COPYING.LGPLv3 and its incorporated
  GPLv3 terms. It is an unmodified FFmpeg release built
  without GPL or nonfree parts; FFmpeg-BUILD.txt gives the exact configuration. The source it
  was built from, `ffmpeg-7.1.5.tar.xz` (sha256 in FFmpeg-BUILD.txt), is published beside each HookeUI release and at
  <https://ffmpeg.org/releases/>. FFmpeg is a trademark of Fabrice Bellard.
- **SRT 1.5.4**, statically linked for Blackmagic video reception, MPL-2.0:
  SRT-LICENSE.txt. <https://github.com/Haivision/srt>
- **OpenSSL 3.5.9**, statically linked for SRT encryption, Apache-2.0:
  OpenSSL-LICENSE.txt. <https://openssl-library.org/>
  Exact sources and build scripts accompany each release; STREAM-BUILD.txt records their hashes.
- **uv 0.7.19** (`Contents/Helpers/uv`) by Astral, under the MIT or Apache-2.0 licence:
  uv-LICENSE-MIT, uv-LICENSE-APACHE. <https://github.com/astral-sh/uv>
- **The bundled example specimen** (human mitosis, David Root, CC0-1.0): `app/assets/examples/NOTICE.md`.

## Downloaded on first launch

uv downloads a Python 3.13 build (python-build-standalone, under the Python Software Foundation licence and its
components' licences) and the packages pinned in `app/services/uv.lock`, each under its own licence, into
`~/Library/Application Support/HookeUI`. Models are downloaded only when you turn one on; each model's card says its
licence and where it comes from.

Scientific unit-name identity uses **Pint 0.26.1**, under BSD-3-Clause
(https://github.com/hgrecco/pint). Its pinned wheel and dependencies are part of
the same first-launch lock; their licence files remain in the installed packages.
Hooke uses canonical unit names to recognize spelling aliases, without converting
scientific values or replacing accepted calibration records.
