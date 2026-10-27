"""A scratch catalog and a fixture channel for capability maintenance (ticket 59's MT1), for the sim's
maintenance-during-live scenario and `python -m sim.maintenance_fixture` (the journey from the command line).

Everything is in its own folder: a local Git repository as the reviewed channel (MICROMIND_REVIEWED_CHANNEL), holding one
entry made from catalog/models/spot-shape (its real adapter and lockfile: numpy and SciPy, CPU only, no weights), and a
catalog of its own (MICROMIND_CATALOG). This Mac's catalog, environments and saves are never touched. Every step runs as
the app's own command-line entry points in processes of their own: channels update, envs (Install), maintenance.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SERVICES = Path(__file__).resolve().parent.parent
SOURCE = SERVICES.parent / "catalog" / "models" / "spot-shape"
ID = "sim-maintained-spot"
GIT = {"GIT_AUTHOR_NAME": "sim", "GIT_AUTHOR_EMAIL": "sim@localhost", "GIT_COMMITTER_NAME": "sim",
       "GIT_COMMITTER_EMAIL": "sim@localhost"}


class Fixture:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or tempfile.mkdtemp(prefix="micromind-maintenance-"))
        self.upstream = self.root / "upstream"
        self.catalog = self.root / "catalog"
        keep = {k: v for k, v in os.environ.items() if not k.startswith("MICROMIND_") or k == "MICROMIND_KEYS_FILE"}
        self.env = {**keep, **GIT, "MICROMIND_CATALOG": str(self.catalog), "MICROMIND_REVIEWED_CHANNEL": str(self.upstream),
                    "MICROMIND_HOME": str(self.root / "home"), "MICROMIND_ASSETS": str(self.root / "assets"),
                    "MICROMIND_CATALOG_AUTO": "0"}

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.upstream, env=self.env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def publish(self, version: str, *, adapter: str = "", component_licence: str = "", entry: str = ID,
                commit: bool = True) -> str:
        """Upstream merges a version of the entry. `adapter` is appended to the real adapter (a fix, or a break);
        `component_licence` changes its declared licence. Returns the commit (with `commit` False, nothing is committed
        yet: several entries go in one commit)."""
        folder = self.upstream / "catalog" / "models" / entry
        if not self.upstream.exists():
            (self.upstream / "catalog" / "models").mkdir(parents=True)
            self._git("init", "-q", "-b", "main")
        shutil.rmtree(folder, ignore_errors=True)
        shutil.copytree(SOURCE, folder)
        text = (folder / "model.toml").read_text()
        lines = [line for line in text.splitlines() if not line.startswith(("derived_from", "# "))]
        text = "\n".join(lines) + "\n"
        text = (text.replace('id = "spot-shape"', f'id = "{entry}"')
                .replace('name = "Spot shape"', f'name = "Sim maintained spots{entry[len(ID):]}"')
                .replace('version = "1"', f'version = "{version}"').replace('licence = ["spot-shape"]', f'licence = ["{entry}"]')
                .replace('"install:spot-shape"', f'"install:{entry}"'))
        if component_licence:
            text = text.replace('licence = "Apache-2.0"', f'licence = "{component_licence}"')
        (folder / "model.toml").write_text(text)
        if adapter:
            with (folder / "adapter.py").open("a") as stream:
                stream.write("\n" + adapter + "\n")
        if not commit:
            return ""
        self._git("add", ".")
        self._git("commit", "-q", "-m", f"{entry} {version}")
        return self._git("rev-parse", "HEAD")

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", *args], cwd=SERVICES, env=self.env, capture_output=True, text=True,
                              check=check)

    def attempt(self, entry: str = ID, *, again: bool = False) -> subprocess.Popen:
        """The maintenance worker's process, as the app's Maintainer starts it (lowest priority, own process group)."""
        sys.path.insert(0, str(SERVICES))
        from orchestrator.maintenance.worker import command

        return subprocess.Popen(command(entry, again=again), cwd=SERVICES, env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)

    def status(self, entry: str = ID) -> dict:
        return json.loads(self.run("orchestrator.maintenance", "status", entry).stdout)

    def python(self, code: str) -> str:
        return subprocess.run([sys.executable, "-c", code], cwd=SERVICES, env=self.env, capture_output=True, text=True,
                              check=True).stdout

    def turn_on(self, policy: str = "auto") -> None:
        """v1 published, fetched, installed by Install and proven (ADR 0021's first activation), then `policy`."""
        self.publish("1")
        self.run("orchestrator.channels", "update")
        installed = self.run("orchestrator.envs", ID, check=False)
        if installed.returncode:
            raise RuntimeError(f"installing {ID} failed: {installed.stdout[-600:]} {installed.stderr[-600:]}")
        self.run("orchestrator.maintenance", "policy", ID, policy)

    def pin(self, generation: str, kind: str, owner: str) -> None:
        self.python(f"from orchestrator.maintenance import pins; pins.add({ID!r}, {generation!r}, {kind!r}, {owner!r})")

    def resolves(self, generation: str) -> str:
        """The version that generation runs, if it is still here, ready and proven ("" if not)."""
        return self.python(
            "from gpu_worker import manifests as catalog; from orchestrator import env_runner\n"
            f"found = env_runner.generation(catalog.get({ID!r}), {generation!r})\n"
            "print(found.m.version if found else '')").strip()

    def leftovers(self) -> list[str]:
        """Processes still running from this fixture's folder: none should be after an attempt."""
        out = subprocess.run(["pgrep", "-fl", str(self.root)], capture_output=True, text=True).stdout
        return [line for line in out.splitlines() if line.strip()]


def portfolio(n: int = 10) -> int:
    """MT4 from the command line, with real builds: n entries of one channel turned on and set to automatic maintenance,
    then one commit with an update for each: compatible fixes, a licence change (Needs review), a broken fix (fails its
    proof), one left as it was. Their attempts run one after another as the Maintainer runs them. Prints each outcome."""
    import time

    f = Fixture()
    names = [f"{ID}-{i:02d}" for i in range(1, n + 1)]
    print("scratch:", f.root)
    for name in names:
        f.publish("1", entry=name, commit=False)
    f._git("add", ".")
    f._git("commit", "-q", "-m", "v1")
    f.run("orchestrator.channels", "update")
    t0 = time.monotonic()
    installed = f.run("orchestrator.envs", *names, check=False)
    print(f"installed {n} in {time.monotonic() - t0:.0f} s (exit {installed.returncode})")
    for name in names:
        f.run("orchestrator.maintenance", "policy", name, "auto")
    plan = {name: "fix" for name in names}
    plan[names[-3]], plan[names[-2]], plan[names[-1]] = "licence", "broken", "unchanged"
    for name, what in plan.items():
        if what == "fix":
            f.publish("2", entry=name, adapter="# a compatible fix", commit=False)
        elif what == "licence":
            f.publish("2", entry=name, adapter="# a fix", component_licence="GPL-3.0", commit=False)
        elif what == "broken":
            f.publish("2", entry=name, adapter="def run(picture, params):\n    return 'not a table'", commit=False)
    f._git("add", ".")
    f._git("commit", "-q", "-m", "v2")
    t0 = time.monotonic()
    for name in names:
        t1 = time.monotonic()
        out, _ = f.attempt(name).communicate()
        row = json.loads(out.strip().splitlines()[-1]) if out.strip() else {"state": "no answer"}
        print(f"{name}  {plan[name]:9}  {row.get('state'):13}  {time.monotonic() - t1:5.1f} s  {row.get('says', '')[:90]}")
    print(f"all {n} attempts in {time.monotonic() - t0:.0f} s; left running: {f.leftovers()}")
    return 0


def main() -> int:
    """The MT1 journey from the command line, on a scratch catalog: auto, a compatible fix applied, a licence change at
    Needs review, a broken fix failed with the update kept, Undo. Prints each status."""
    f = Fixture()
    print("scratch:", f.root)
    f.turn_on("auto")
    first = f.status()
    print("1 turned on:", first["active"]["version"], first["policy"]["words"])
    f.pin(first["active"]["generation"], "recipe", "journey-recipe")
    f.publish("2", adapter="# a compatible fix: nothing declared changes")
    print("2 attempt:", f.attempt().communicate()[0].strip()[-300:])
    print("  status:", f.status()["says"], "| v1 still resolves:", f.resolves(first["active"]["generation"]))
    f.publish("3", component_licence="GPL-3.0")
    print("3 attempt:", f.attempt().communicate()[0].strip()[-300:])
    f.publish("4", adapter="def run(picture, params):\n    return 'not a table'")
    print("4 attempt:", f.attempt().communicate()[0].strip()[-300:])
    print("  status:", f.status()["says"])
    print("5 undo:", f.run("orchestrator.maintenance", "undo", ID, check=False).stdout.strip()[-200:])
    print("  status:", json.dumps({k: f.status()[k] for k in ("state", "says", "policy")}))
    print("left running:", f.leftovers())
    return 0


if __name__ == "__main__":
    raise SystemExit(portfolio() if sys.argv[1:2] == ["portfolio"] else main())
