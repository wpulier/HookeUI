"""Which backend modules would fail to *import* on Windows: an emulation, run on macOS or Linux.

It blocks the POSIX-only standard modules Windows does not have (fcntl, resource, pwd, grp, termios, pty, tty,
posix, syslog, crypt) and imports every backend module, recording the first missing module each one needs. It does
not emulate Windows behaviour (paths, signals, processes); a module that imports here can still fail on Windows. It is
the cheap first gate before a run on a real Windows machine (packaging/windows/README.md).

    cd services && .venv/bin/python ../packaging/windows/import_probe.py [--out probe.json]
"""
import argparse
import importlib
import importlib.abc
import json
import pkgutil
import sys
import traceback
from pathlib import Path

# Not `posix` or `_posixsubprocess`: the standard library itself branches to `nt`/`_winapi` on Windows, and blocking
# them here would only break the host's own subprocess module.
POSIX_ONLY = {"fcntl", "resource", "pwd", "grp", "termios", "pty", "tty", "syslog", "crypt"}
PACKAGES = ("orchestrator", "gpu_worker", "env_worker", "research_worker", "hosted")


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in POSIX_ONLY:
            raise ModuleNotFoundError(f"No module named {name!r} (not on Windows)", name=name)
        return None


def modules(root: Path):
    for pkg in PACKAGES:
        base = root / pkg
        if not base.is_dir():
            continue
        yield pkg
        for info in pkgutil.walk_packages([str(base)], prefix=f"{pkg}."):
            yield info.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    root = Path.cwd().resolve()
    sys.path.insert(0, str(root))
    for name in list(sys.modules):
        if name.split(".")[0] in POSIX_ONLY:
            del sys.modules[name]
    sys.meta_path.insert(0, Block())
    result = {"blocked": sorted(POSIX_ONLY), "ok": [], "fail": {}}
    for name in sorted(set(modules(root))):
        try:
            importlib.import_module(name)
            result["ok"].append(name)
        except ModuleNotFoundError as e:
            if (e.name or "").split(".")[0] in POSIX_ONLY:
                here = Path(__file__).resolve()
                origin = next((Path(f.filename).resolve().relative_to(root).as_posix()
                               for f in reversed(traceback.extract_tb(e.__traceback__))
                               if Path(f.filename).resolve() != here and Path(f.filename).resolve().is_relative_to(root)), None)
                result["fail"][name] = {"missing": e.name, "imported_at": origin}
            else:
                result["fail"][name] = {"other": f"ModuleNotFoundError: {e.name}"}
        except Exception as e:  # noqa: BLE001 - a probe: record anything else, it is not a Windows signal
            result["fail"][name] = {"other": f"{type(e).__name__}: {str(e)[:160]}"}
    roots = {}
    for v in result["fail"].values():
        if "imported_at" in v:
            roots.setdefault(f"{v['imported_at']} imports {v['missing']}", 0)
            roots[f"{v['imported_at']} imports {v['missing']}"] += 1
    result["root_causes"] = dict(sorted(roots.items(), key=lambda kv: -kv[1]))
    text = json.dumps(result, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    windows = sum(1 for v in result["fail"].values() if "imported_at" in v)
    print(f"{len(result['ok'])} import; {windows} fail on a POSIX-only module; "
          f"{len(result['fail']) - windows} fail for other reasons (not a Windows signal)")
    for cause, n in result["root_causes"].items():
        print(f"  {n:4d}  {cause}")


if __name__ == "__main__":
    main()
