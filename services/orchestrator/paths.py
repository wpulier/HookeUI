"""Where the orchestrator saves things: one place, so a test or the sim moves all of it at once.

`Paths.from_env()` is the app's layout: `MICROMIND_HOME` (default services/) holds sessions/,
research/, snapshots/, evidence/, taught/, seen/, models/, capabilities/, datasets/ and logs/; each MICROMIND_* variable below still moves its one
path. The live app keeps datasets outside every checkout (scripts/studio.sh sets MICROMIND_DATASETS_DIR to
~/MicroscopeMind/datasets); by default they follow MICROMIND_HOME, so a test or the sim never finds the real ones. Recordings stay
at the repo root, next to the camera bridge's, and the keys file stays outside the repo.
`Paths.under(root)` puts everything under one folder, for tests and the sim harness.
Read when asked for, never at import, so setting a variable later still takes effect.

Three homes (docs/saves.md): every save is one kind, with how long it is kept (KINDS, LIFETIMES).
Once the saves were moved out of the checkout (`make saves-move` writes ~/MicroscopeMind/.saves.json),
the live layout is `Paths.saves(root)`: ~/MicroscopeMind/datasets, ~/MicroscopeMind/logs (temporary;
logs/kept for judgments people made) and ~/MicroscopeMind/assets (the user's sessions, researchers'
work, recordings...). Which layout: MICROMIND_HOME (tests, the sim) > MICROMIND_SAVES > the marker >
services/ as before.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping

SERVICES = Path(__file__).resolve().parents[1]
REPO = SERVICES.parent

# The test assets store (orchestrator/assets.py, assets/README.md): outside every checkout, so the
# live app, each worktree, the benches and the sim harness share one copy. Its manifests are in git.
ASSETS_MANIFEST = REPO / "assets" / "manifest.json"
CORPUS_MANIFEST = SERVICES / "bench" / "corpus" / "corpus.json"
# The bundled, rights-cleared examples the first example reads (orchestrator/examples.py): in the checkout, read-only,
# shipped with the app (never a save).
EXAMPLES = REPO / "assets" / "examples"


def assets_root(environ: Mapping[str, str] = os.environ) -> Path:
    """MICROMIND_ASSETS, or ~/MicroscopeMind."""
    return Path(environ.get("MICROMIND_ASSETS") or Path.home() / "MicroscopeMind").expanduser()

# The three homes: what kind of save each path is (datasets, performance logs, the user's assets), and
# judgments people made (votes, verdicts) that are kept for good though they sit with the logs.
KINDS = {
    "datasets": "dataset",
    "sessions": "asset", "research": "asset", "snapshots": "asset", "evidence": "asset", "results": "asset", "taught": "asset",
    "seen": "asset", "bookmarks": "asset", "identity": "asset", "models": "asset",
    "recordings": "asset", "library": "asset", "jobs": "asset",
    "interaction_log": "log", "conversation_log": "log", "checks": "log",
    "votes_log": "kept", "corrections_log": "kept", "verdicts": "kept",
    "keys_file": "secret",
}
LIFETIMES = {
    "dataset": "until the user deletes one (the Datasets tab moves it to the trash)",
    "asset": "until the user deletes it",
    "log": "temporary: kept until it has been used, then pruned (make logs-prune)",
    "kept": "kept for good: judgments people made",
    "secret": "not a save: outside every home, never copied",
}
MARKER = ".saves.json"  # in the saves root once `make saves-move` has moved the saves there


LAUNCH = "launch.json"  # the user's private launch settings in the assets root: home, keysFile (scripts/studio_launch.py)


def default_keys_file(environ: Mapping[str, str] = os.environ) -> Path:
    """Where the API keys are when MICROMIND_KEYS_FILE does not say: ~/Desktop/micromind.txt when it is there,
    else the keysFile of the private launch settings (`make start` exports it; a bench, a script or the app
    started any other way read the same file, 2026-10-11), else ~/Desktop/micromind.txt. Only a path is read
    here, never a key."""
    desktop = Path.home() / "Desktop" / "micromind.txt"
    if desktop.is_file():
        return desktop
    try:
        import json

        value = json.loads((assets_root(environ) / LAUNCH).read_text()).get("keysFile")
    except (OSError, ValueError, AttributeError):
        return desktop
    path = Path(value).expanduser() if isinstance(value, str) and value else None
    return path if path is not None and path.is_absolute() and path.is_file() else desktop


def saves_root(environ: Mapping[str, str] = os.environ) -> Path | None:
    """Where the three homes are, when they are in use: MICROMIND_SAVES, or the assets root once the
    saves were moved there (its marker); None means services/ as before."""
    if environ.get("MICROMIND_SAVES"):
        return Path(environ["MICROMIND_SAVES"]).expanduser()
    root = assets_root(environ)
    return root if (root / MARKER).is_file() else None


# The variables that move one path each (MICROMIND_HOME moves the first ten together).
OVERRIDES = {
    "sessions": "MICROMIND_SESSIONS_DIR",
    "research": "MICROMIND_RESEARCH_DIR",
    "conversation_log": "MICROMIND_CONVERSATION_LOG",
    "interaction_log": "MICROMIND_INTERACTION_LOG",
    "votes_log": "MICROMIND_VOTES_LOG",
    "corrections_log": "MICROMIND_CORRECTIONS_LOG",
    "snapshots": "MICROMIND_SNAPSHOTS_DIR",
    "evidence": "MICROMIND_EVIDENCE_DIR",
    "results": "MICROMIND_RESULTS_DIR",
    "taught": "MICROMIND_TAUGHT_DIR",
    "seen": "MICROMIND_SEEN_DIR",
    "bookmarks": "MICROMIND_BOOKMARKS_DIR",
    "identity": "MICROMIND_IDENTITY_DIR",
    "models": "MICROMIND_MODELS_DIR",
    "verdicts": "MICROMIND_VERDICTS_LOG",
    "checks": "MICROMIND_CHECKS_LOG",
    "datasets": "MICROMIND_DATASETS_DIR",
    "recordings": "MICROMIND_RECORDINGS_DIR",
    "library": "MICROMIND_LIBRARY",
    "jobs": "MICROMIND_JOBS_DIR",
    "keys_file": "MICROMIND_KEYS_FILE",
}


@dataclass(frozen=True)
class Paths:
    home: Path
    sessions: Path  # the day journals (day/journal.py)
    research: Path  # researchers' work, the findings ledger and descriptions
    conversation_log: Path  # the chat as shown, replayed after a restart
    interaction_log: Path  # every action with its options, for A/B comparison
    votes_log: Path  # blind A/B/C votes
    # The exported corrections (`make export-corrections`): judgments people made, kept for good beside the votes,
    # appended and never rebuilt from the assets (M3.4 review B2).
    corrections_log: Path
    snapshots: Path  # pictures of the view the user asked to keep (snapshot tool), per day
    evidence: Path  # what operations the user's requests made found, with their views and masks, per day (evidence.py)
    results: Path  # the pictures of what tools measured or made (resources.Results), per day; the records are in the journal
    taught: Path  # the examples of labels the user taught (taught.py), per day
    seen: Path  # the outlines named today and their embeddings (seen.py), per day
    bookmarks: Path  # places on the slide the user asked to keep (bookmarks.py), per day
    identity: Path  # individuals, links and observations of things followed over time (identity.py), per day
    models: Path  # what loads measured (loads.json); fine-tuned weights made before the catalog kept them (finetuned.adopt_old)
    verdicts: Path  # the user's verdicts on capabilities not yet in git (capabilities.py; ADR 0007)
    # The app's own check of each model in the capabilities list (model_checks.py): one line a check, the latest per
    # model counts; made again by "Check now" or `make capability-check`, so it is a log, never a judgment.
    checks: Path
    datasets: Path  # what Record collects, for later analysis (datasets/; docs/datasets.md)
    recordings: Path  # the studio's own recordings
    # The library's ids and the user's edits of its assets (library.py; ADR 0022): which recording or picture is a7,
    # and its specimen, magnification, title and notes as the user gave them.
    library: Path
    # Volumes the user added and the jobs run on them (jobs.py; brainreg's registrations), per day: each volume as it was
    # added with what was said of it, each job's request, log, outputs and record.
    jobs: Path
    keys_file: Path  # API keys (never inside the repo)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Paths":
        keys_file = default_keys_file(environ)
        saves = None if environ.get("MICROMIND_HOME") else saves_root(environ)
        if saves is not None:
            home, defaults = saves, cls.saves(saves, keys_file=keys_file)
        else:
            home = Path(environ.get("MICROMIND_HOME") or SERVICES)
            defaults = cls._layout(home, recordings=REPO / "recordings", keys_file=keys_file)
        return cls(home=home, **{field: Path(environ[var]) if environ.get(var) else getattr(defaults, field)
                                 for field, var in OVERRIDES.items()})

    @classmethod
    def under(cls, root: str | os.PathLike[str]) -> "Paths":
        root = Path(root)
        return cls._layout(root, recordings=root / "recordings", keys_file=root / "micromind.txt")

    @classmethod
    def saves(cls, root: str | os.PathLike[str], keys_file: Path | None = None) -> "Paths":
        """The three homes under `root` (~/MicroscopeMind for the live app): datasets/, logs/ (and
        logs/kept/) and assets/."""
        root = Path(root)
        logs, kept, assets = root / "logs", root / "logs" / "kept", root / "assets"
        return cls(home=root, sessions=assets / "sessions", research=assets / "research",
                   conversation_log=logs / "conversation.jsonl", interaction_log=logs / "interactions.jsonl",
                   votes_log=kept / "votes.jsonl", corrections_log=kept / "corrections.jsonl",
                   snapshots=assets / "snapshots", evidence=assets / "evidence",
                   results=assets / "results",
                   taught=assets / "taught",
                   seen=assets / "seen", bookmarks=assets / "bookmarks", identity=assets / "identity", models=assets / "models", verdicts=kept / "verdicts.jsonl", checks=logs / "checks.jsonl", datasets=root / "datasets",
                   recordings=assets / "recordings", library=assets / "library.jsonl", jobs=assets / "jobs",
                   keys_file=keys_file or Path.home() / "Desktop" / "micromind.txt")

    @property
    def logs(self) -> Path:
        """The performance logs' folder: the interaction log, the crops, the servers' logs, saved logs."""
        return self.interaction_log.parent

    @classmethod
    def _layout(cls, home: Path, recordings: Path, keys_file: Path) -> "Paths":
        logs = home / "logs"
        return cls(home=home, sessions=home / "sessions", research=home / "research",
                   conversation_log=logs / "conversation.jsonl", interaction_log=logs / "interactions.jsonl",
                   votes_log=logs / "votes.jsonl", corrections_log=logs / "corrections.jsonl",
                   snapshots=home / "snapshots", evidence=home / "evidence",
                   results=home / "results",
                   taught=home / "taught", seen=home / "seen", bookmarks=home / "bookmarks", identity=home / "identity",
                   models=home / "models",
                   verdicts=home / "capabilities" / "verdicts.jsonl", checks=home / "capabilities" / "checks.jsonl",
                   datasets=home / "datasets",
                   recordings=recordings,
                   library=home / "library.jsonl",
                   jobs=home / "jobs",
                   keys_file=keys_file)


def finetuned(environ: Mapping[str, str] = os.environ) -> Path:
    """Where fine-tuned versions are kept (finetuned.py): the catalog's models folder, each in its entry's own folder
    beside downloaded revisions (the catalog audit's G3), unless MICROMIND_MODELS_DIR names one (the tests)."""
    if environ.get("MICROMIND_MODELS_DIR"):
        return Path(environ["MICROMIND_MODELS_DIR"])
    from gpu_worker.manifests import catalog_root

    return catalog_root(environ) / "models"


def envs(environ: Mapping[str, str] = os.environ) -> Path:
    """Where the environments the app makes live (envs.py): the catalog's envs/ folder, unless MICROMIND_ENVS names one
    (make catalog-proof points a scratch catalog at the real environments: they are built in place and can't be
    copied)."""
    if environ.get("MICROMIND_ENVS"):
        return Path(environ["MICROMIND_ENVS"])
    from gpu_worker.manifests import catalog_root

    return catalog_root(environ) / "envs"


def tools(environ: Mapping[str, str] = os.environ) -> Path:
    """Where the tools the app sets up live (Fiji's generations: fiji_generations.py): the catalog's tools/ folder, unless
    MICROMIND_TOOLS names one (make catalog-proof reads the real generations from its scratch catalog, as it does the
    environments)."""
    if environ.get("MICROMIND_TOOLS"):
        return Path(environ["MICROMIND_TOOLS"])
    from gpu_worker.manifests import catalog_root

    return catalog_root(environ) / "tools"


def clock_offset(environ: Mapping[str, str] = os.environ) -> Path | None:
    """The file that may move the session store's clock by its seconds (app.session_clock: the sim crosses midnight
    without waiting for it), only under MICROMIND_HOME (tests, the sim); None for the live app."""
    home = environ.get("MICROMIND_HOME")
    return Path(home) / "clock-offset" if home else None


def cli_environ(environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The environment the paths CLI answers from: as given, and when neither MICROMIND_HOME nor MICROMIND_SAVES is
    set, the private launch settings' `home` (what `make start` exports: scripts/studio_launch.py), so a script asking
    where the logs are finds the live app's (2026-10-11: `python -m orchestrator.paths logs` answered ~/MicroscopeMind/
    logs while the app wrote under its profile home). Only the CLI: the app, the benches, the sim and a second stack
    read their own environment (Paths.from_env), so they keep their own homes. A setting that can't be read, or a home
    that is not a folder, is left out."""
    env = dict(environ)
    if "MICROMIND_HOME" in env or "MICROMIND_SAVES" in env:
        return env
    try:
        import json

        home = json.loads((assets_root(env) / LAUNCH).read_text()).get("home")
    except (OSError, ValueError, AttributeError):
        return env
    path = Path(home).expanduser() if isinstance(home, str) and home else None
    if path is not None and path.is_absolute() and path.is_dir():
        env["MICROMIND_HOME"] = str(path)
    return env


def main(argv: list[str]) -> int:
    """For the shell scripts: `python -m orchestrator.paths logs` prints where the logs are now (any
    field, or `logs`, or `saves`: the saves root, empty while the saves are still in services/).
    Print absolute paths so shell/bridge consumers use this process's directory as the same base.
    The live app's home comes from the launch settings when nothing else says (cli_environ)."""
    paths = Paths.from_env(cli_environ())
    for name in argv or ["logs"]:
        if name == "saves":
            root = saves_root()
            print(root.absolute() if root is not None else "")
        elif name == "logs":
            print(paths.logs.absolute())
        elif name in {f.name for f in fields(Paths)}:
            print(getattr(paths, name).absolute())
        else:
            print(f"no such path {name!r}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
