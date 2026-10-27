"""Where things are saved (orchestrator/paths.py): today's layout by default, one variable moves
everything, each old variable still moves its one path, and no other module works it out."""
import re
from dataclasses import fields
from pathlib import Path

from orchestrator.paths import OVERRIDES, Paths

SERVICES = Path(__file__).resolve().parents[1]
REPO = SERVICES.parent
ORCHESTRATOR = SERVICES / "orchestrator"


def test_defaults_are_where_the_app_has_always_saved(tmp_path):
    paths = Paths.from_env({"MICROMIND_ASSETS": str(tmp_path)})  # an assets root the saves were not moved to
    assert paths.home == SERVICES
    assert paths.sessions == SERVICES / "sessions"
    assert paths.research == SERVICES / "research"
    assert paths.conversation_log == SERVICES / "logs" / "conversation.jsonl"
    assert paths.interaction_log == SERVICES / "logs" / "interactions.jsonl"
    assert paths.votes_log == SERVICES / "logs" / "votes.jsonl"
    assert paths.snapshots == SERVICES / "snapshots"
    assert paths.datasets == SERVICES / "datasets"
    assert paths.recordings == REPO / "recordings"
    assert paths.keys_file == Path.home() / "Desktop" / "micromind.txt"


def test_home_moves_sessions_research_and_logs_but_not_recordings_or_keys(tmp_path):
    paths = Paths.from_env({"MICROMIND_HOME": str(tmp_path), "MICROMIND_ASSETS": str(tmp_path / "assets")})
    for name in ("sessions", "research", "conversation_log", "interaction_log", "votes_log", "snapshots", "taught", "seen",
                 "verdicts", "datasets"):
        assert getattr(paths, name).is_relative_to(tmp_path), name
    assert paths.recordings == REPO / "recordings"
    assert paths.keys_file == Path.home() / "Desktop" / "micromind.txt"


def test_the_keys_file_of_the_private_launch_settings_is_found_by_every_launch(tmp_path, monkeypatch):
    """`make start` exports the launch settings' keysFile; a bench, a script or the app started any other way found
    ~/Desktop/micromind.txt, which had moved (2026-10-11), and ran without keys. MICROMIND_KEYS_FILE still wins."""
    import json

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")  # no ~/Desktop/micromind.txt here
    keys = tmp_path / "private" / "micromind.txt"
    keys.parent.mkdir()
    keys.write_text("GEMINI_API_KEY=x\n")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "launch.json").write_text(json.dumps({"schema": 1, "keysFile": str(keys)}))
    assert Paths.from_env({"MICROMIND_ASSETS": str(assets)}).keys_file == keys
    assert Paths.from_env({"MICROMIND_ASSETS": str(assets), "MICROMIND_KEYS_FILE": "/elsewhere"}).keys_file == Path("/elsewhere")
    (assets / "launch.json").write_text("not json")
    assert Paths.from_env({"MICROMIND_ASSETS": str(assets)}).keys_file == tmp_path / "user" / "Desktop" / "micromind.txt"


def test_each_variable_moves_its_one_path(tmp_path):
    base = Paths.from_env({"MICROMIND_HOME": str(tmp_path / "home")})
    for field, var in OVERRIDES.items():
        target = tmp_path / "moved" / field
        paths = Paths.from_env({"MICROMIND_HOME": str(tmp_path / "home"), var: str(target)})
        assert getattr(paths, field) == target, var
        for other in OVERRIDES:
            if other != field:
                assert getattr(paths, other) == getattr(base, other), (var, other)


def test_an_empty_variable_is_unset(tmp_path):
    assets = {"MICROMIND_ASSETS": str(tmp_path)}
    assert Paths.from_env({**assets, "MICROMIND_HOME": "", "MICROMIND_RESEARCH_DIR": ""}) == Paths.from_env(assets)


def test_under_puts_everything_under_the_root(tmp_path):
    paths = Paths.under(tmp_path)
    for f in fields(Paths):
        assert getattr(paths, f.name).is_relative_to(tmp_path), f.name
    assert paths.sessions == Paths.from_env({"MICROMIND_HOME": str(tmp_path)}).sessions


def test_constructors_read_the_environment_when_called_not_at_import(tmp_path, monkeypatch):
    from orchestrator.agent import Agent
    from orchestrator.arena import Arena
    from orchestrator.day.journal import Journal
    from orchestrator.research import ResearchManager

    monkeypatch.setenv("MICROMIND_HOME", str(tmp_path))
    assert Agent(hub=None).interaction_log == tmp_path / "logs" / "interactions.jsonl"
    assert Arena(hub=None, agent=None).votes_log == tmp_path / "logs" / "votes.jsonl"
    assert Journal("2026-10-05").root == tmp_path / "sessions"
    assert ResearchManager(hub=None).root == tmp_path / "research"


# Reading a save path from the environment, or working one out from a module's own place
# (Path(__file__).parents[N]), belongs in paths.py alone.
SAVE_ENV = re.compile(r"MICROMIND_(HOME|\w*_(LOG|DIR|FILE))\b")
FROM_HERE = re.compile(r"__file__|\.parents\[")
ALLOWED = {
    # These locate read-only source and bind proof to code, never choose save destinations.
    ("capability_workspace.py", "repo = prompts.SKILL.parents[3]"),
    ("fiji_sandbox.py", 'BOOTSTRAP = Path(__file__).resolve().parents[1] / "fiji_worker" / "functions.groovy"'),
    ("fiji_sandbox.py", '"boundarySha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'),
    ("bundle_runtime.py", "sources = [env_runner.SERVER, Path(env_runner.__file__), Path(sandbox.__file__), Path(__file__),"),
    ("bundle_runtime.py", "Path(bundle_contract.__file__), Path(reference_checks.__file__), Path(worker_resources.__file__),"),
    ("bundle_runtime.py", "Path(fiji_sandbox.__file__), Path(bundle_review.__file__), Path(scientific_ports.__file__),"),
    ("bundle_runtime.py", "Path(semantic_resources.__file__), Path(region_reader.__file__), Path(region_inputs.__file__),"),
    ("bundle_runtime.py", "Path(admission.__file__), Path(typed_files.__file__)]"),
    # The studio's built static files are read, never saved to.
    ("server.py", 'STUDIO_DIST = Path(__file__).resolve().parents[2] / "apps" / "studio" / "dist"'),
    # The shared numbers are read from the repo (contract/overlay.json), never written.
    ("contract.py", 'PATH = Path(__file__).resolve().parents[2] / "contract" / "overlay.json"'),
    # The capability catalogue and the verdicts in git are read from the repo, never written by the
    # app (its own verdicts go where paths.py says; ADR 0007).
    ("capabilities.py", 'ROOT = Path(__file__).resolve().parents[2]'),
    # Code the app runs in another process (Fiji's worker script), and the extension list's reviewed inventory: read
    # from the repo, never written.
    ("fiji_bindings.py", 'SERVER = Path(__file__).resolve().parent.parent / "fiji_worker" / "server.groovy"'),
    ("inventories.py", 'REPO = Path(__file__).resolve().parents[2]'),
    # The adapter server a contributed entry runs in, in its own environment: read from the repo, never written.
    ("env_runner.py", 'SERVER = Path(__file__).resolve().parent.parent / "env_worker" / "server.py"'),
    ("jobs.py", 'JOB = Path(__file__).resolve().parent.parent / "env_worker" / "job.py"'),  # a job's runner, read only
    # The bundled, rights-cleared example is read-only source; generated files live under Paths.snapshots.
    ("capability_example.py", "source = Path(__file__).resolve().parents[2] / 'examples' / 'three-particles' / 'example.py'"),
    # The contributor certificate is read-only source text, never an application save destination.
    ("publish.py", 'DCO = Path(__file__).resolve().parents[2] / "docs/contributing/DCO.txt"'),
    # The app's own Python reading a volume's headers in a process of its own: its script, read only.
    ("registrations.py", 'script = Path(__file__).resolve().parent / "volume_inspect.py"'),
    # macOS's sandbox profile lets a contributed worker read the app's own code and its Python's install: read only.
    ("sandbox.py", "services = Path(__file__).resolve().parent.parent"),
    ("sandbox.py", "return Path(_real(exe)).parents[1]"),
    # The Hooke Jupyter kernel's script, which a kernelspec names for Jupyter's own Python to run: read only.
    ("scientific_jupyter.py", "KERNEL = Path(__file__).with_name('jupyter_kernel.py')"),
}


def test_only_paths_py_decides_where_things_are_saved():
    found = []
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        if path.name == "paths.py" and path.parent == ORCHESTRATOR:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if (SAVE_ENV.search(code) or FROM_HERE.search(code)) and (path.name, code.strip()) not in ALLOWED:
                found.append(f"{path.relative_to(SERVICES)}:{n}: {line.strip()}")
    assert not found, "\n".join(found)


# --- the three homes (docs/saves.md) ----------------------------------------------------------------
def test_every_save_says_what_kind_it_is_and_how_long_it_is_kept():
    from orchestrator.paths import KINDS, LIFETIMES

    names = {f.name for f in fields(Paths)} - {"home"}
    assert names == set(KINDS), names ^ set(KINDS)
    assert set(KINDS.values()) <= set(LIFETIMES)


def test_the_three_homes_hold_each_kind_in_its_own_folder(tmp_path):
    from orchestrator.paths import KINDS

    homes = {"dataset": tmp_path / "datasets", "asset": tmp_path / "assets", "log": tmp_path / "logs",
             "kept": tmp_path / "logs" / "kept"}
    paths = Paths.saves(tmp_path)
    for name, kind in KINDS.items():
        if kind != "secret":
            path = getattr(paths, name)
            assert path.is_relative_to(homes[kind]) and (kind == "kept" or not path.is_relative_to(homes["kept"])), name
    assert not paths.keys_file.is_relative_to(tmp_path)  # a secret is never in a save


def test_once_moved_the_app_saves_in_the_homes_and_a_test_still_in_its_own_folder(tmp_path):
    from orchestrator.paths import MARKER

    assets = tmp_path / "MicroscopeMind"
    assets.mkdir()
    assert Paths.from_env({"MICROMIND_ASSETS": str(assets)}).sessions == SERVICES / "sessions"  # not moved yet
    (assets / MARKER).write_text("{}")
    moved = Paths.from_env({"MICROMIND_ASSETS": str(assets)})
    assert moved == Paths.saves(assets) and moved.logs == assets / "logs"
    home = tmp_path / "test"
    assert Paths.from_env({"MICROMIND_ASSETS": str(assets), "MICROMIND_HOME": str(home)}).sessions == home / "sessions"
    assert Paths.from_env({"MICROMIND_SAVES": str(tmp_path / "other")}).datasets == tmp_path / "other" / "datasets"


def test_the_paths_cli_finds_the_live_apps_home_and_nothing_else_reads_it(tmp_path, monkeypatch, capsys):
    """2026-10-11: `python -m orchestrator.paths logs` answered ~/MicroscopeMind/logs while the live app wrote under the
    launch settings' home (`make start` exports it), so scripts read the wrong logs. The CLI reads that home when nothing
    else says; the app, the benches, the sim and a second stack read their own environment (Paths.from_env) and keep
    their own homes."""
    import json

    from orchestrator.paths import MARKER, cli_environ, main

    assets, live = tmp_path / "MicroscopeMind", tmp_path / "MicroscopeMind" / "profiles" / "live"
    live.mkdir(parents=True)
    (assets / MARKER).write_text("{}")
    (assets / "launch.json").write_text(json.dumps({"schema": 1, "home": str(live)}))
    env = {"MICROMIND_ASSETS": str(assets)}
    monkeypatch.delenv("MICROMIND_HOME", raising=False)
    monkeypatch.delenv("MICROMIND_SAVES", raising=False)
    monkeypatch.setenv("MICROMIND_ASSETS", str(assets))
    assert cli_environ(env)["MICROMIND_HOME"] == str(live)
    assert main(["logs", "interaction_log"]) == 0
    assert capsys.readouterr().out.split() == [str(live / "logs"), str(live / "logs" / "interactions.jsonl")]
    # Everything else keeps its own: the saves' homes here, and a bench's, the sim's or a second stack's own home.
    assert Paths.from_env(env).logs == assets / "logs"
    bench = tmp_path / "bench-home"
    assert cli_environ({**env, "MICROMIND_HOME": str(bench)})["MICROMIND_HOME"] == str(bench)
    assert "MICROMIND_HOME" not in cli_environ({**env, "MICROMIND_SAVES": str(tmp_path / "saves")})
    # A setting that can't be read, or a home that is not a folder, is left out.
    (assets / "launch.json").write_text(json.dumps({"schema": 1, "home": str(tmp_path / "gone")}))
    assert "MICROMIND_HOME" not in cli_environ(env)
    (assets / "launch.json").write_text("not json")
    assert "MICROMIND_HOME" not in cli_environ(env)
