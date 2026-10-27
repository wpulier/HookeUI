# Headless Linux backend test container

The backend's Python test suite on Ubuntu 24.04, in Docker. This is H11 groundwork: a development check of what
already works off macOS, and of what refuses. It is **not** a Linux build of Hooke, not a clean machine, and it
never qualifies a P8 or Q child. It has no GPU and no display, and on this Mac it is arm64, not the proposed x86_64
target. The research note's §4.2 has the results and its §8 the P8 mapping
(`docs/research/2026-10-20-h11-linux-windows-feasibility.md`).

```bash
scripts/linux-container-test.sh /tmp/linux-tests            # tests HEAD; uncommitted changes are not in it
```

- The image holds only the locked environment (`uv sync --locked --python 3.13 --all-groups`). Build it again when
  `services/uv.lock` changes.
- The run unpacks `git archive <ref>` into the container, makes it a git repository (some tests ask git for the
  root), and runs `pytest -m "not timing"` with `MICROMIND_HOME` and `MICROMIND_KEYCHAIN=off` of its own.
- It writes `junit.xml`, `pytest.txt`, `platform.txt` and `commit.txt` to the output folder.
- `JOBS` (default 4), `CPUS` (6), `MEMORY` (10g) and `PYTEST_ARGS` (for example a file list) tune it. With Docker
  Desktop's default 8 GB VM, `JOBS=4` makes some memory-admission checks refuse ("not enough free memory"); those
  pass with `JOBS=1`.
- On a shared machine, take the heavy-run slot first.
