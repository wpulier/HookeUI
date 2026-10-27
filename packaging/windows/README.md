# Windows backend path (H11 groundwork)

The Windows counterpart of the Linux test container. It is a development check of what already works on Windows, and
of what refuses. It is **not** a Windows build of Hooke and **not** a qualification run: a P8 or Q child needs a reset
physical instance (ticket 57's checklist), never a developer machine or a hosted VM.

| Piece | What it does |
| --- | --- |
| `import_probe.py` | Runs on macOS or Linux and imports every backend module with the POSIX-only standard modules blocked, as on Windows. On 2026-10-21 it found 45 of 437 modules failing on `fcntl`. After `orchestrator/file_locks.py`, all 438 import. It is an emulation, not a Windows run |
| `run-backend-tests.ps1` | On a Windows machine: a pinned uv (checked by SHA-256), uv's Python 3.13 and `services/uv.lock`; then the import probe and `pytest -m "not timing"` with its own `MICROMIND_HOME`, writing `junit.xml` and `pytest.txt` to `-Out` |
| `.github/workflows/backend-other-os.yml` | The same on hosted `windows-2025` and `ubuntu-24.04` VMs. **Started by hand only** (`workflow_dispatch`): hosted minutes on a private repository count against its quota, and no spending is authorised, so starting it is the coordinator's or Will's call |

What is known before any Windows run:
- `services/uv.lock` resolves wheels-only for `x86_64-pc-windows-msvc` with every group: `uv export --frozen
  --all-groups`, then `uv pip compile --only-binary :all: --python-platform x86_64-pc-windows-msvc`, on 2026-10-21.
- The process-model gaps are G3 in the feasibility note:
  - `os.killpg` (5 uses), `start_new_session` (17), `signal.SIGKILL` (5) and `pass_fds` (1) have no Windows meaning;
  - `os.getpgid`, `os.waitid` and `os.WNOHANG` don't exist on Windows;
  - the code that uses them refuses or needs Job Objects.
- File locking: the stand-in refuses every lock on Windows, so downloads, publishing, bundle review, scientific
  Projects and retention fail closed there until a `LockFileEx` implementation is written and tested on Windows.
- Confinement: every unreviewed entry is refused (G2), with words that now name Windows.
