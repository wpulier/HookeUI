# Notices of what entries bring with them

Each folder is one pinned package an entry installs that its lockfile can't hold (an entry's `env.native`), named as
its file is (`<name>-<version>-<build>`), with:

- **the package's own licence files,** byte for byte as the package carries them (`info/licenses/`), a path's `/` written
  `__`;
- **`notice.json`:** where it came from and what it is, read from the package's own `info/about.json` and
  `info/index.json` after checking it by the sha256 the entry pins: its URL, sha256 and size, its build and platform,
  the licence it declares, its home and source, the component it is in `services/orchestrator/licences.py`, and each
  licence file's path in the package with its size and sha256.

`services/tests/test_native_notices.py` checks that every native package an entry pins has its folder here, that it
names the same file and sha256, that its licence files are the ones recorded, and that its component is in the entry's
`licence`. This is component accounting, not a legal clearance.

| Package | Licence it declares | Component | Used by |
|---|---|---|---|
| niftyreg 1.5.69.6 (h72ed088_1) | BSD-3-Clause; its bundled nifticlib public domain | `niftyreg` | brainreg |
| libpng 1.6.38 (h76d750c_0) | zlib-acknowledgement (PNG Reference Library License version 2) | `libpng` | brainreg |
| libzlib 1.2.13 (h03a7124_4) | Zlib (its licence file is headed "version 1.2.7"; the terms are zlib's) | `zlib` | brainreg |
| libcxx 14.0.6 (h04bba0f_0) | Apache-2.0 WITH LLVM-exception | `libcxx` | brainreg |

conda-forge's own recipe scripts (BSD-3-Clause, `info/recipe/*recipe-scripts-license.txt` in each package) build these
packages and are not part of what is installed.
