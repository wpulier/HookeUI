"""Channels (orchestrator/channels.py; phase 3's E4b): entries merged into the reviewed catalog after this app was made are
listed at "Check for catalog updates", with no app update; a community channel's are listed as not reviewed here and run
nothing until the user allows its code; only entries with their own adapter come through either. A scratch upstream
repository, a stand-in uv (tests/test_envs.fake_uv)."""
import json
import os
import shutil
import subprocess
import time

import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import channels, env_runner, envs, operations, options, readiness
from tests.test_env_runner import ADAPTER, ENTRY, _picture, standin_uv

NEEDS_CODE = '''id = "needs-code"
name = "Needs code"
runs = "this-mac"
version = "x 1"
licence = ["edgetam"]
runner = "gpu-worker"
workers = ["analysis"]
documented_mb = 10
load_limit_s = 10
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "models"
    shutil.copytree(catalog.ROOT, root)
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.delenv("MICROMIND_ENVS", raising=False)
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    upstream = tmp_path / "upstream"
    (upstream / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=upstream)
    monkeypatch.setenv("MICROMIND_REVIEWED_CHANNEL", str(upstream))
    before = (dict(options.GROUPS["cells"]), dict(operations.OPERATIONS), dict(operations.ADAPTERS))
    catalog.manifests.cache_clear()
    yield tmp_path
    options.GROUPS["cells"].clear()
    options.GROUPS["cells"].update(before[0])
    operations.OPERATIONS.clear()
    operations.OPERATIONS.update(before[1])
    operations.ADAPTERS.clear()
    operations.ADAPTERS.update(before[2])
    for one in env_runner._clients.values():
        one.stop()
    env_runner._clients.clear()
    catalog.manifests.cache_clear()


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def publish(home, entry: str, manifest: str, adapter: str | None = ADAPTER) -> str:
    """What a merged pull request does upstream: the entry's folder committed. Returns the commit."""
    folder = home / "upstream" / "catalog" / "models" / entry
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.toml").write_text(manifest)
    if adapter is not None:
        (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
        (folder / "adapter.py").write_text(adapter)
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", f"Catalog: {entry}", cwd=home / "upstream")
    return _git("rev-parse", "HEAD", cwd=home / "upstream")


def test_an_entry_merged_after_the_app_was_made_is_listed_at_the_next_check(home):
    first = publish(home, "needs-code", NEEDS_CODE, adapter=None)
    publish(home, "dinov2-small", (catalog.ROOT / "dinov2-small" / "model.toml").read_text().replace(
        'name = "DINOv2 small"', 'name = "DINOv2 small, upstream"'), adapter=None)
    r = channels.update()
    assert r["added"] == [] and r["later"] == ["needs-code"] and r["entries"] == 2, "a bundled one is never new"
    assert catalog.get("dinov2-small").channel == "" and catalog.get("dinov2-small").name == "DINOv2 small", \
        "a bundled entry's update comes with the app's"
    assert "needs-code" not in catalog.manifests()
    said = catalog.problems()["reviewed/needs-code"]
    assert isinstance(said[0], catalog.Incompatible) and "comes with an app update" in said[0]

    commit = publish(home, "dark-spots", ENTRY.format())
    r = channels.update()
    assert r["commit"] == commit and r["was"] != first and r["added"] == ["dark-spots"]
    m = catalog.get("dark-spots")
    assert m.channel == "reviewed" and m.namespace == "" and catalog.entry_folder(m) == channels.snapshot_dir("reviewed", commit) / "dark-spots"
    assert not os.access(catalog.entry_folder(m) / "adapter.py", os.W_OK), "read-only, never written over"
    assert [s.kind for s in readiness.model("dark-spots", {}).steps] == ["install"], "reviewed: no Allow step"
    assert (channels.snapshot_dir("reviewed", r["was"])).is_dir(), "the earlier commit is kept"


def test_a_community_channel_runs_nothing_until_its_code_is_allowed(home, monkeypatch):
    lab = home / "lab"
    (lab / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    (home / "upstream").rename(home / "upstream-reviewed")
    lab.rename(home / "upstream")  # publish() writes to "upstream": here, the lab's repository
    commit = publish(home, "dark-spots", ENTRY.format().replace('"install:dark-spots"', '"install:upstream/dark-spots"'))
    r = channels.add(str(home / "upstream"))
    assert r == {"name": "upstream", "commit": commit, "entries": 1}
    m = catalog.get("upstream/dark-spots")
    assert m is not None and m.channel == "upstream" and m.namespace == "upstream" and "dark-spots" not in catalog.manifests()
    step = readiness.model("upstream/dark-spots", {}).steps[0]
    assert step.kind == "trust" and step.says == "Allow code from upstream" and "not reviewed here" in step.how
    with pytest.raises(envs.Failed, match="allow code from it first"):
        envs.Installs().start("upstream/dark-spots")
    standin_uv(home, monkeypatch)
    with pytest.raises(envs.Failed, match="allow code from it first"):
        envs.install(m)  # the CLI uses the same deliberate trust boundary as the Models page
    assert model_files.folder(m) == home / "catalog" / "models" / "upstream" / "dark-spots", "its files apart"

    channels.trust("upstream")
    assert [s.kind for s in readiness.model("upstream/dark-spots", {}).steps] == ["install"]
    standin_uv(home, monkeypatch)
    queue = envs.Installs()
    queue.start("upstream/dark-spots")
    for _ in range(3000):  # up to 60 s, only against a hang: on a loaded Mac the install took more than 6 s
        if queue.wire()[0]["state"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert queue.wire()[0]["state"] == "done", queue.wire()
    assert env_runner.register()["added"] == ["dark-spots"]
    from tests.test_env_runner import run_it

    out = run_it(m)  # its key is upstream/dark-spots: the run finds it by its id (live 2026-10-13: "no longer in the catalog")
    assert len(out["polygons"]) == 3 and out["version"] == "dark-spots 0.1"
    settings = options.Settings()
    settings.update({"cells": "dark-spots"})

    channels.trust("upstream", False)  # no longer allowed: its choice goes, a setting that chose it goes back
    assert env_runner.in_use(m) is None and "isn't allowed" in env_runner.ready(m)
    changed = env_runner.register(settings=settings)
    assert changed["removed"] == ["dark-spots"] and changed["reset"]
    channels.remove("upstream")
    assert catalog.get("upstream/dark-spots") is None and not (home / "catalog" / "channels" / "upstream").exists()


ATLAS_JOB = '''id = "{id}"
name = "{id}"
runs = "this-mac"
version = "{id} 0.1"
licence = ["brainreg"]
kind = "tool-function"
what = "Works on a volume its own way"
runner = "python-env"
source = {{ url = "https://example.org/{id}" }}
adapter = "adapter:work"
env = {{ python = "3.13.5", lock = "env.lock", imports = ["json"] }}
needs_to_run = ["install:upstream/{id}"]
host = "own"
documented_mb = 10
load_limit_s = 10

[[implements]]
operation = "{operation}"
impl = "{id}"
input = "volume.3d"
output = "{output}"
'''


def test_a_contributed_job_nothing_here_can_run_is_never_said_on(home):
    """Window C (2026-10-14): a community entry implementing register-atlas with output registration.atlas (the C7
    fixture c7-file-job) installed, proved and read "On", yet only brainreg's own entry registers a volume
    (registrations.start) and a contributed job runs only as a kind on a volume (registrations.start_kind). It is listed
    with why it can't be turned on, and no Turn on plans for it; it stays in the catalog, so its raw confinement proof
    (envs.install, jobs.run) still runs it. A contributed kind on a volume, and brainreg, are unchanged."""
    from orchestrator import capability_shelf, catalog_view, jobs

    lab = home / "lab"
    (lab / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    (home / "upstream").rename(home / "upstream-reviewed")
    lab.rename(home / "upstream")
    publish(home, "atlas-job", ATLAS_JOB.format(id="atlas-job", operation="register-atlas", output="registration.atlas"))
    publish(home, "spot-table", ATLAS_JOB.format(id="spot-table", operation="count-spots", output="table.objects"))
    channels.add(str(home / "upstream"))
    channels.trust("upstream")

    m = catalog.get("upstream/atlas-job")
    assert m is not None and "upstream/atlas-job" not in catalog.problems(), "listed, never a manifest problem"
    why = ("Can't be turned on here: no way to run a contributed 'register-atlas' job yet "
           "(its output registration.atlas isn't a volume or a table)")
    r = readiness.model(m.key, {})
    assert (r.status, r.word, r.why) == ("unavailable", "Can't be turned on here", why)
    assert [s.kind for s in r.steps] == ["route"] and r.steps[0].says == why
    face = catalog_view.face(m, r, [])
    assert face["state"] == "unavailable" and face["says"] == why, "never On, whatever its proof said"
    row = {"id": m.key, "name": m.name, **r.wire(), "functionalCheck": {"state": "works"},
           "proofStep": catalog_view.proof_step(m)}
    assert capability_shelf.plan(capability_shelf.entry_role(row)) is None, "no Turn on plans for it"
    listed = next(x for x in catalog_view.rows([], {}, {}) if x["id"] == m.key)
    assert listed["group"] == "unavailable" and listed["face"]["says"] == why and not any(s.get("plan") for s in listed["setups"])

    kind = catalog.get("upstream/spot-table")
    assert jobs.no_route(kind) == "" and [s.kind for s in readiness.model(kind.key, {}).steps] == ["install"], \
        "a contributed kind on a volume runs (start_kind)"
    assert jobs.no_route(catalog.get("brainreg")) == "", "the app's own registration entry runs (registrations.start)"


def test_a_channel_is_fetched_only_at_the_commit_pinned(home):
    commit = publish(home, "dark-spots", ENTRY.format())
    with pytest.raises(channels.CantDo, match="no full commit"):
        channels.fetch(str(home / "upstream"), "main", home / "x")
    with pytest.raises(channels.CantDo, match="has no nowhere folder"):
        channels.fetch(str(home / "upstream"), commit, home / "x", path="nowhere")
    assert not (home / "x").exists()
    with pytest.raises(channels.CantDo, match="can't name a channel"):
        channels.add(str(home / "upstream"), name="mine")
    with pytest.raises(channels.CantDo, match="https:// address"):
        channels.add("ssh://example.org/repo")
    assert channels.fetch(str(home / "upstream"), commit, home / "x") == 1
    assert channels.fetch("/nowhere", commit, home / "x") == 1, "a commit fetched is never fetched or written again"


def _community_installed(home, monkeypatch):
    """A community channel's entry, its code allowed, installed and proven: on, as C7 Window B's c7-image-q was."""
    lab = home / "lab"
    (lab / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    (home / "upstream").rename(home / "upstream-reviewed")
    lab.rename(home / "upstream")
    publish(home, "dark-spots", ENTRY.format().replace('"install:dark-spots"', '"install:upstream/dark-spots"'))
    channels.add(str(home / "upstream"))
    channels.trust("upstream")
    standin_uv(home, monkeypatch)
    queue = envs.Installs()
    queue.start("upstream/dark-spots")
    for _ in range(3000):  # up to 60 s, only against a hang: on a loaded Mac the install took more than 6 s
        if queue.wire()[0]["state"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert queue.wire()[0]["state"] == "done", queue.wire()
    return catalog.get("upstream/dark-spots")


class _Said:
    def __init__(self):
        from orchestrator import activity

        class Said(activity.Activity):
            def __init__(self):
                self.lines = []

            def add(self, kind, text, *, state="done", **kw):
                self.lines.append((state, text))

        self.activities = Said()


def _app():
    import asyncio
    from types import SimpleNamespace

    retired = []
    app = SimpleNamespace(hub=_Said(), installs=envs.Installs(), retired=retired,
                          catalog_changed=lambda: env_runner.register(retire=lambda c: (retired.append(c), c.stop())))
    from orchestrator.commands import COMMANDS

    app.run = lambda kind, **msg: asyncio.run(COMMANDS[kind](app, None, {"type": kind, **msg}))
    return app


def test_a_community_entry_turns_off_and_on_again_keeping_its_installed_version(home, monkeypatch):
    """C7 Window B, finding 5: Turn off for an installed community env entry takes its choice away and stops its worker,
    deleting nothing; Turn on makes it a choice again with the version it had, nothing installed again."""
    from orchestrator import catalog_view
    from tests.test_env_runner import run_it

    m = _community_installed(home, monkeypatch)
    app = _app()
    app.catalog_changed()
    assert len(run_it(m)["polygons"]) == 3
    worker = env_runner._clients[m.key]
    process = worker._process
    row = next(r for r in catalog_view.rows([], {}, {}) if r["id"] == m.key)
    assert row["env"] == {"on": True, "installed": True}
    kept = [d for d, _ in envs.generations(m)]

    assert app.run("model-deactivate", model=m.key) == {"ok": True}
    assert env_runner.in_use(m) is None and env_runner.turned_off(m)
    assert app.retired == [worker] and process.poll() is not None, "its worker stopped at Turn off"
    assert ("cells", m.id) not in operations.ADAPTERS
    assert m.id not in options.GROUPS["cells"], "no longer a choice"
    assert [d for d, _ in envs.generations(m)] == kept and all(d.is_dir() for d in kept), "nothing deleted"
    assert any("is off" in text for _, text in app.hub.activities.lines)
    row = next(r for r in catalog_view.rows([], {}, {}) if r["id"] == m.key)
    assert row["env"] == {"on": False, "installed": True}
    step = readiness.model(m.key, {}).steps[0]
    assert step.kind == "install" and step.says == "Turn Dark spots on" and "nothing is installed again" in step.how
    assert step.wire()["onAgain"] is True, "Turn on's sheet says nothing is installed (Window C, 2026-10-14)"

    turned = app.installs.start(m.key)  # its card's Turn on (an Install of it)
    assert turned["state"] == "done" and "on again" in turned["says"], turned
    assert not env_runner.turned_off(m) and env_runner.in_use(m).folder == kept[0]
    assert [d for d, _ in envs.generations(m)] == kept, "nothing built"
    app.catalog_changed()
    assert m.id in options.GROUPS["cells"]


@pytest.mark.parametrize("callback_first", [False, True], ids=["start-returns-first", "catalog-changed-first"])
def test_turning_a_kept_version_back_on_says_one_activity_line(home, monkeypatch, callback_first):
    """Window C (2026-10-14): Turn on (the press, capability_shelf.TurnOn) of a community entry turned off with its
    installed version kept wrote two Activity lines, "… is on again (…, as it was installed)" from the install queue and
    "… is on (…)" from the press. One line now, the more informative words, and the conversation's notice once.
    `catalog-changed-first` (the Spec review of 87ff1e59): the queue's `changed` advances the press before its start
    returns, as production's catalog_changed on the loop can while the press runs off it."""
    from types import SimpleNamespace

    from orchestrator import activity, capability_shelf, catalog_view
    from orchestrator.app import App

    m = _community_installed(home, monkeypatch)
    env_runner.turn_off(m)
    assert env_runner.in_use(m) is None

    class Lines(activity.Activity):
        def __init__(self):
            self.lines = {}  # by id: a later line with the same id replaces it in place

        def add(self, kind, text, *, id=None, state="done", **kw):
            self.lines[id or f"line{len(self.lines)}"] = (state, text)

    hub = SimpleNamespace(activities=Lines())
    notices = []
    said = SimpleNamespace(hub=hub, dialogue=SimpleNamespace(notice=lambda **kw: notices.append(kw["summary"])),
                           agent=SimpleNamespace(log=lambda record: None))
    advanced = []
    def changed():  # the queue's catalog change: the press advanced at once, before the queue's start returns
        if callback_first:
            advanced.append(len(turn.intents))
            turn.advance()
    installs = envs.Installs(hub=hub, changed=changed)
    app = SimpleNamespace(hub=hub, installs=installs, downloads=installs,
                          capabilities_wire=lambda counts=True: {"roles": [], "models": catalog_view.rows([], {}, {}),
                                                                 "setup": {"downloads": [], "installs": installs.wire()}},
                          turned_on=lambda job, says, **kw: App.turned_on(said, job, says, **kw))
    turn = capability_shelf.TurnOn(app)
    job = f"model:{m.key}"
    turn.start(job)
    assert advanced == ([1] if callback_first else []), "the catalog change came while the press was still open"
    turn.advance()
    assert not turn.wire(), "on"
    assert env_runner.in_use(m) is not None
    on = [text for state, text in hub.activities.lines.values() if " is on" in text]
    assert on == ["Dark spots is on again (dark-spots 0.1, as it was installed)"], hub.activities.lines
    assert notices == on, "the conversation's notice, once"


def test_a_community_entry_is_removed_from_its_card_and_its_worker_stops(home, monkeypatch):
    """C7 Window B, finding 5: Remove (model-uninstall) for an installed community env entry takes its installed versions
    away and retires its worker; the entry stays, and Install makes it again."""
    from orchestrator import catalog_view
    from tests.test_env_runner import run_it

    m = _community_installed(home, monkeypatch)
    app = _app()
    app.catalog_changed()
    run_it(m)
    worker = env_runner._clients[m.key]
    process = worker._process
    done = app.run("model-uninstall", model=m.key)
    assert done["ok"] and done["removed"] and done["filesBytes"] > 0 and done["freedBytes"] >= 0
    assert done["says"].startswith(f"Removed {m.name}'s installed versions. Their files took up to ")
    assert "free space" in done["says"] and "saved results are kept" in done["says"]
    assert not envs.generations(m) and env_runner.in_use(m) is None
    assert app.retired == [worker] and process.poll() is not None
    assert m.id not in options.GROUPS["cells"]
    assert catalog.get(m.key) is not None, "its entry stays"
    row = next(r for r in catalog_view.rows([], {}, {}) if r["id"] == m.key)
    assert row["env"] == {"on": False, "installed": False}
    assert any("Removed Dark spots" in text for _, text in app.hub.activities.lines)


def test_a_community_channel_is_checked_for_updates_from_its_row_and_the_cli_pins_a_commit(home, monkeypatch):
    """C7 Window B, finding 2: a community channel's row has Check for updates (channel-update: its newest commit, said),
    and the CLI's update honours --commit (it pinned the newest whatever was asked)."""
    lab = home / "lab"
    (lab / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    (home / "upstream").rename(home / "upstream-reviewed")
    lab.rename(home / "upstream")
    entry = ENTRY.format().replace('"install:dark-spots"', '"install:upstream/dark-spots"')
    first = publish(home, "dark-spots", entry)
    channels.add(str(home / "upstream"))
    channels.trust("upstream")
    second = publish(home, "dark-spots", entry.replace('"dark-spots 0.1"', '"dark-spots 0.2"'))

    app = _app()
    r = app.run("channel-update", name="upstream")
    assert r["ok"] and r["commit"] == second and r["was"] == first
    assert r["says"].startswith(f"Checked upstream: now at {second[:7]} (was {first[:7]})"), r["says"]
    assert catalog.get("upstream/dark-spots").version == "dark-spots 0.2"
    assert channels.trusted(catalog.get("upstream/dark-spots")), "whether its code is allowed is kept"
    assert any(text == r["says"] for _, text in app.hub.activities.lines)
    again = app.run("channel-update", name="upstream")
    assert again["says"].startswith(f"Checked upstream: already at its newest commit, {second[:7]}")
    from orchestrator.brain.registry import ToolFailed

    with pytest.raises(ToolFailed, match="no community channel called reviewed"):
        app.run("channel-update", name="reviewed")

    assert channels.main(["update", "upstream", "--commit", first]) == 0
    assert next(c for c in channels.wire() if c["name"] == "upstream")["commit"] == first, "the CLI pinned the commit asked"
    assert catalog.get("upstream/dark-spots").version == "dark-spots 0.1"


def test_a_channel_of_one_entry_is_said_as_one_entry(home, monkeypatch):
    """The thesis check on 0.5.2: "Added the community channel lab at d6c1022: 1 entries"."""
    lab = home / "lab"
    (lab / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    (home / "upstream").rename(home / "upstream-reviewed")
    lab.rename(home / "upstream")
    publish(home, "dark-spots", ENTRY.format().replace('"install:dark-spots"', '"install:upstream/dark-spots"'))
    app = _app()
    added = app.run("channel-add", url=str(home / "upstream"))
    assert ": 1 entry, not reviewed here" in added["says"] and "entries" not in added["says"], added["says"]
    again = app.run("channel-update", name="upstream")
    assert again["says"].endswith("(1 entry)"), again["says"]
    assert channels.entries_words(3) == "3 entries" and channels.entries_words(0) == "0 entries"


def test_uninstall_reports_owned_work_refusal_at_command_boundary(home, monkeypatch):
    from orchestrator import generation_leases
    from orchestrator.brain.registry import ToolFailed

    m = _community_installed(home, monkeypatch)
    app = _app()
    with generation_leases.use(env_runner.in_use(m).folder):
        with pytest.raises(ToolFailed, match='running work'):
            app.run('model-uninstall', model=m.key)
    assert env_runner.in_use(m) is not None


def test_turn_off_is_refused_while_a_run_uses_it_and_changes_nothing(home, monkeypatch):
    """L7b (ticket 62 row 12): Turn off retired the worker at once, ending a run that was using it without a word. While
    a run holds the version that is on, Turn off is refused and says so; once the run has ended it turns off as before."""
    from orchestrator import generation_leases

    m = _community_installed(home, monkeypatch)
    found = env_runner.in_use(m)
    with generation_leases.use(found.folder):  # a run of it is going
        with pytest.raises(env_runner.InUse, match="running now: let that run finish, or press Stop on it.*Nothing was changed"):
            env_runner.turn_off(m)
        assert not env_runner.turned_off(m) and env_runner.in_use(m).folder == found.folder
    env_runner.turn_off(m)
    assert env_runner.turned_off(m) and env_runner.in_use(m) is None
    with generation_leases.use(found.folder):
        pass  # the version itself is kept and usable again by Turn on: no withdrawal left behind


def test_a_reviewed_catalog_saved_at_its_former_address_is_read_at_the_current_one(tmp_path, monkeypatch):
    # A Mac that updated the reviewed catalog before the repository split (2026-10-27) saved the old address; reading
    # it there would fetch from a repository that no longer exists. Another channel at such an address is left alone.
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    saved = catalog.channels_root() / "channels.json"
    saved.parent.mkdir(parents=True)
    old = "https://github.com/wpulier/ObservationUI.git"
    saved.write_text(json.dumps([{"name": "reviewed", "url": old, "commit": "a" * 40, "reviewed": True},
                                 {"name": "someones", "url": old}]))
    found = {c["name"]: c for c in catalog.channels()}
    assert found["reviewed"]["url"] == channels.REVIEWED_URL == "https://github.com/wpulier/HookeUI.git"
    assert found["reviewed"]["commit"] == "a" * 40, "only the address changes"
    assert found["someones"]["url"] == old
