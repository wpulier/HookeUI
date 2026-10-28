"""Capability maintenance (orchestrator/maintenance/; ADR 0025; ticket 59's MT1): an entry's later updates under the
policy a person recorded. No record is manual and nothing happens unattended; review prepares and proves beside the
active generation and waits; auto promotes a compatible proven update of reviewed code, keeps pinned work on its
generation, rolls back a failed first check and holds; anything only a person can accept stops at Needs review.

A scratch upstream repository as the reviewed channel and a stand-in uv (tests/test_channels, tests/test_envs)."""
import json
import os
import subprocess
import sys
import time

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_runner, envs
from orchestrator.maintenance import compat, discover, pins, policy, prepare, promote, status, store  # noqa: F401
from tests.test_channels import _git, home, publish  # noqa: F401 - `home` is the fixture
from tests.test_env_runner import ADAPTER, ENTRY, WRONG, install

KEY = "dark-spots"


@pytest.fixture(autouse=True)
def every_check_looks(monkeypatch):
    """Each step here publishes and checks within seconds: no shared look at the channel (discover.NEWEST_S)."""
    monkeypatch.setattr(discover, "NEWEST_S", 0)


def version(v: str, extra: str = "") -> str:
    return ENTRY.format().replace('"dark-spots 0.1"', f'"dark-spots {v}"') + extra


def turned_on(home, monkeypatch) -> catalog.Manifest:
    """v1 published, fetched and installed by the user's Install (ADR 0021's first activation)."""
    publish(home, KEY, version("0.1"))
    channels.update()
    assert install(home, monkeypatch)["state"] == "done"
    return catalog.get(KEY)


def fix(home, v: str, adapter: str = ADAPTER.replace("< 128", "< 129"), extra: str = "") -> str:
    """Upstream publishes a newer version: by default a compatible fix (the adapter changes, no declared fact does)."""
    return publish(home, KEY, version(v, extra), adapter)


def active_version() -> str:
    return env_runner.selected(catalog.get(KEY)).m.version


def test_no_record_is_manual_and_nothing_happens_unattended(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    pinned = next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"]
    newer = fix(home, "0.2")
    assert policy.get(KEY) == {"policy": "manual", "default": True}
    assert prepare.run(KEY)["says"] == "Its policy allows nothing unattended"
    assert next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"] == pinned, "nothing fetched"
    assert not prepare.path(KEY).exists() and not store.path("decisions.jsonl").exists()
    rec = discover.check(m)  # a person's Check under manual: the newer commit is said, nothing is fetched
    assert rec["found"] == {"commit": newer, "fetched": False}
    assert next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"] == pinned
    assert status.of(catalog.get(KEY))["state"] == "available"
    assert active_version() == "dark-spots 0.1"


def test_review_prepares_and_proves_beside_the_active_generation_then_waits(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "review", by="you", how="screen")
    fix(home, "0.2")
    row = prepare.run(KEY)
    assert row["state"] == "ready-to-apply" and row["says"] == "Ready to apply: dark-spots 0.2", row
    assert active_version() == "dark-spots 0.1", "review never promotes"
    now = status.of(catalog.get(KEY))
    assert now["state"] == "ready-to-apply" and now["next"] == "apply" and now["candidate"]["version"] == "dark-spots 0.2"
    assert now["active"]["version"] == "dark-spots 0.1" and now["policy"]["words"] == "Review updates"
    decision = promote.promote(catalog.get(KEY), now["candidate"]["generation"], by="user")
    assert decision["action"] == "promote" and decision["by"] == "user" and active_version() == "dark-spots 0.2"


def test_auto_promotes_a_compatible_fix_and_pinned_work_keeps_its_generation_across_a_restart(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    v1 = env_runner.selected(m).folder.name
    pins.add(KEY, v1, "recipe", "recipe-1")  # a saved recipe made on v1
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    row = prepare.run(KEY)
    assert row["state"] == "applied" and row["says"] == "Updated to dark-spots 0.2", row
    assert active_version() == "dark-spots 0.2"
    assert env_runner.generation(catalog.get(KEY), v1).m.version == "dark-spots 0.1", "pinned work still resolves v1"
    [decision] = promote.decisions(KEY, moves=True)
    assert decision["action"] == "promote" and decision["by"] == "maintenance:auto" and decision["from"] == v1
    assert decision["proof"]["ok"] is True and decision["policy"] == "auto"
    held = pins.holds(catalog.get(KEY))[v1]
    assert sorted(h.split(":")[0] for h in held) == ["recipe", "rollback"]
    now = status.of(catalog.get(KEY))
    assert now["state"] == "applied" and now["next"] == "undo" and now["says"].startswith("Updated to dark-spots 0.2 (")
    # A restart: a new process reads the same pins, decision and state from the profile.
    from orchestrator.paths import SERVICES

    out = subprocess.run([sys.executable, "-m", "orchestrator.maintenance", "status", KEY], cwd=str(SERVICES),
                         capture_output=True, text=True, env=dict(os.environ))
    assert out.returncode == 0, out.stderr
    again = json.loads(out.stdout)
    assert again["state"] == "applied" and {p["owner"] for p in again["held"]} == {"recipe-1", decision["to"]}


def test_a_changed_licence_needs_review_and_is_never_prepared_or_promoted(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    publish(home, KEY, version("0.2").replace('licence = "MIT"', 'licence = "GPL-3.0"'), ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    assert row["state"] == "needs-review", row
    assert row["says"] == "Needs review: component licence changed from MIT, open to GPL-3.0, open", row["says"]
    assert active_version() == "dark-spots 0.1"
    generation = row["target"]["generation"]
    assert generation and not (envs.root(catalog.get(KEY)) / generation).exists(), "nothing built for it"
    assert status.of(catalog.get(KEY))["state"] == "needs-review"


def test_a_failed_proof_leaves_the_active_generation_and_says_why(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2", adapter=WRONG)
    row = prepare.run(KEY)
    assert row["state"] == "failed" and row["says"].startswith("Update failed: ")
    assert row["says"].endswith("dark-spots 0.1 stays in use") and active_version() == "dark-spots 0.1"
    assert status.of(catalog.get(KEY))["state"] == "failed" and promote.decisions(KEY) == []


def test_a_failed_first_check_rolls_back_by_itself_and_holds_automatic_maintenance(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    real = env_runner.promote

    def damaged(m, name, by, environ=os.environ):  # the disk loses the promoted generation's Python right after
        found = real(m, name, by, environ)
        if by == "maintenance:auto":
            (found.folder / "bin" / "python").unlink()
        return found

    monkeypatch.setattr(env_runner, "promote", damaged)
    fix(home, "0.2")
    row = prepare.run(KEY)
    assert row["state"] == "failed" and "isn't ready and proven here any more" in row["says"], row
    assert active_version() == "dark-spots 0.1"
    assert [d["action"] for d in promote.decisions(KEY)] == ["promote", "checked", "rollback"]
    assert promote.decisions(KEY)[1]["ok"] is False, "the first check's result is recorded"
    assert promote.decisions(KEY)[-1]["by"] == "maintenance:auto:first-check"
    assert policy.effective(catalog.get(KEY))[0] == "review" and "failed its first check" in policy.get(KEY)["held"]


def test_undo_goes_back_and_the_next_update_waits_for_a_person(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    assert prepare.run(KEY)["state"] == "applied"
    decision = promote.undo(catalog.get(KEY))
    assert decision["action"] == "rollback" and decision["by"] == "user" and active_version() == "dark-spots 0.1"
    assert policy.effective(catalog.get(KEY)) == ("review", "you undid the update to dark-spots 0.2")
    with pytest.raises(promote.Failed, match="no earlier version"):
        promote.undo(catalog.get(KEY))
    fix(home, "0.3", adapter=ADAPTER.replace("< 128", "< 130"))
    assert prepare.run(KEY)["state"] == "ready-to-apply" and active_version() == "dark-spots 0.1"
    policy.record(catalog.get(KEY), "auto", by="you", how="screen")  # recording it again lifts the hold
    assert policy.effective(catalog.get(KEY)) == ("auto", "")


def test_going_back_by_hand_holds_automatic_maintenance(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    v1 = env_runner.selected(m).folder.name
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    assert prepare.run(KEY)["state"] == "applied"
    env_runner.choose(catalog.get(KEY), v1)  # the card's Go back
    assert policy.effective(catalog.get(KEY)) == ("review", "you went back to dark-spots 0.1")


def test_a_revoked_policy_ends_the_attempt_before_it_promotes(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    real = envs.build

    def revoked_meanwhile(m, environ=os.environ, say=lambda s: None, stop=lambda: False):
        d = real(m, environ, say=say, stop=stop)
        policy.record(m, "manual", by="you", how="screen")
        return d

    monkeypatch.setattr(envs, "build", revoked_meanwhile)
    monkeypatch.setattr(prepare, "LOOK_S", 0)  # every stop() reads the policy again
    fix(home, "0.2")
    row = prepare.run(KEY)
    assert row["state"] == "stopped", row
    assert active_version() == "dark-spots 0.1" and promote.decisions(KEY) == []


def test_pin_keeps_the_version_and_nothing_is_obtained(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    rec = policy.record(m, "pin", by="you", how="screen")
    assert rec["version"] == "dark-spots 0.1"
    pinned = next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"]
    fix(home, "0.2")
    assert prepare.run(KEY)["says"] == "Its policy allows nothing unattended"
    assert next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"] == pinned
    assert status.of(catalog.get(KEY))["says"] == "Pinned to dark-spots 0.1"


def test_code_nobody_reviewed_is_never_promoted_unattended(home, monkeypatch):
    lab = home / "lab"
    (lab / "catalog" / "models" / KEY).mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    folder = lab / "catalog" / "models" / KEY
    (folder / "model.toml").write_text(version("0.1"))
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    _git("add", ".", cwd=lab)
    _git("commit", "-q", "-m", "one", cwd=lab)
    channels.add(str(lab), name="lab")
    channels.trust("lab")
    m = catalog.get(f"lab/{KEY}")
    assert m is not None and not policy.reviewed(m)
    assert policy.effective(m) == ("manual", "")
    monkeypatch.setattr(env_runner, "selected", lambda m, environ=os.environ: object())
    policy.record(m, "auto", by="you", how="screen")
    assert policy.effective(m) == ("review", "its code isn't reviewed here, so you apply each update")


def test_a_policy_is_a_persons_and_covers_only_an_entry_already_turned_on(home, monkeypatch):
    publish(home, KEY, version("0.1"))
    channels.update()
    m = catalog.get(KEY)
    with pytest.raises(policy.NotRecorded, match="turn Dark spots on first"):
        policy.record(m, "auto", by="you", how="screen")
    with pytest.raises(policy.NotRecorded, match="recorded by a person"):
        policy.record(m, "auto", by="agent", how="agent-said-so")
    with pytest.raises(policy.NotRecorded, match="no maintenance policy"):
        policy.record(m, "sometimes", by="you", how="screen")
    assert policy.record(m, "manual", by="you", how="screen")["policy"] == "manual"
    log = store.lines(store.path("policies.jsonl"))
    assert [r["policy"] for r in log] == ["manual"] and log[0]["how"] == "screen"


def test_a_failed_check_backs_off_and_never_means_no_update(home, monkeypatch):
    m = turned_on(home, monkeypatch)

    def offline(url):
        raise channels.CantDo("there is no connection to its catalog")

    monkeypatch.setattr(channels, "newest", offline)
    first = discover.check(m)
    assert first["ok"] is False and first["failures"] == 1
    assert first["next"] - discover.RETRY_S == pytest.approx(time.time(), abs=5)
    second = discover.check(m)
    assert second["failures"] == 2 and second["next"] - 2 * discover.RETRY_S == pytest.approx(time.time(), abs=5)
    now = status.of(catalog.get(KEY))
    assert now["check"]["state"] == "failed" and now["state"] == "none" and now["active"]["version"] == "dark-spots 0.1"


def test_reading_status_starts_nothing(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "review", by="you", how="screen")
    fix(home, "0.2")

    def refused(*args, **kwargs):
        raise AssertionError("a status read started something")

    monkeypatch.setattr(subprocess, "Popen", refused)
    monkeypatch.setattr(channels, "newest", refused)
    monkeypatch.setattr(channels, "update", refused)
    monkeypatch.setattr(envs, "build", refused)
    assert status.of(catalog.get(KEY))["state"] == "none", "the newer commit isn't known until a check looks"


def test_compat_names_each_declared_fact_that_changed():
    base = catalog.Manifest(id="x", name="X", runs="cpu", version="1", licence=("x",))
    assert compat.compare(base, base)["compatible"] is True
    import dataclasses

    cases = {"licence": dict(licence=("y",)), "the service it calls": dict(service="fal"),
             "where it runs": dict(runs="cloud"), "its weights and files": dict(files=({"path": "w", "sha256": "b"},)),
             "what it needs to run": dict(needs_to_run=("key:FAL_KEY",)), "source": dict(channel="lab")}
    for what, change in cases.items():
        found = compat.compare(base, dataclasses.replace(base, **change))
        assert not found["compatible"] and [r["what"] for r in found["review"]] == [what], (what, found)
    assert compat.compare(base, dataclasses.replace(base, version="2"))["compatible"], "a version alone is the update"


def _wait(what, seconds=180.0):
    deadline = time.monotonic() + seconds  # completion wait, not a performance assertion
    while time.monotonic() < deadline:
        if what():
            return True
        time.sleep(0.05)
    return False


def test_the_maintainer_runs_attempts_in_its_own_process_and_stop_ends_only_the_attempt(home, monkeypatch):
    """The app's Maintainer: a person's Check starts the attempt in a low-priority process of its own; Stop during the
    build ends that process and everything it started, says Stopped, and leaves the active version; the next attempt
    finishes the update."""
    from orchestrator.maintenance import worker

    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    slow = home / "slowbin"
    slow.mkdir()
    gate = home / "slow-build"
    gate.write_text("")
    (slow / "uv").write_text(f'#!/bin/bash\nif [ "$1 $2" = "pip sync" ] && [ -e {gate} ]; then sleep 60; fi\n'
                             f'exec {home / "bin" / "uv"} "$@"\n')
    (slow / "uv").chmod(0o755)
    environ = {**os.environ, "PATH": f"{slow}:{os.environ['PATH']}"}
    seen = []
    # The app's command runs at the lowest priority the system has (taskpolicy -b, and the attempt lowers its own
    # thread), which a busy Mac may starve for minutes (load 100: the QoS call itself blocked 3 min, 2026-10-21): right
    # for the app, wrong for a timed test of the flow, which runs the same module at its own priority (--as-started).
    assert worker.command(KEY)[:2] == (["taskpolicy", "-b"] if sys.platform == "darwin" else ["nice", "-n"])
    assert worker.command(KEY)[-4:] == ["-m", "orchestrator.maintenance", "attempt", KEY]
    assert worker.command(KEY, True)[-1] == "--again", "a person's Check looks at the channel now"
    keeper = worker.Maintainer(changed=lambda: seen.append(1), environ=environ, first_after=0, every=3600,
                               command=lambda key, again=False: [sys.executable, "-m", "orchestrator.maintenance", "attempt", key,
                                                                  "--as-started"])
    try:
        _the_maintainer_flow(keeper, gate, seen)
    finally:
        keeper.stop()


def _the_maintainer_flow(keeper, gate, seen):
    keeper.now(KEY)
    assert _wait(lambda: prepare.last(KEY).get("says", "").startswith("Preparing update: ") and keeper.running() == KEY), \
        (prepare.last(KEY), keeper.running())
    pid = prepare.last(KEY)["pid"]
    assert keeper.cancel(KEY) is True
    assert prepare.last(KEY)["state"] == "stopped" and not prepare.alive({"pid": pid})
    assert status.of(catalog.get(KEY))["state"] != "preparing" and active_version() == "dark-spots 0.1"
    gate.unlink()
    keeper.now(KEY)
    assert _wait(lambda: prepare.last(KEY).get("state") == "applied" and not keeper.running()), prepare.last(KEY)
    assert active_version() == "dark-spots 0.2" and seen


def test_removal_keeps_what_is_pinned_and_releases_a_rollback_hold(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    v1 = env_runner.selected(m).folder.name
    pins.add(KEY, v1, "recipe", "recipe-1")
    with pytest.raises(envs.Failed, match=r"kept for what is pinned to it \(recipe recipe-1\)"):
        envs.remove(catalog.get(KEY))
    assert env_runner.generation(catalog.get(KEY), v1) is not None
    pins.remove(KEY, "recipe", "recipe-1")
    pins.add(KEY, v1, "rollback", "later")
    assert envs.remove(catalog.get(KEY)) is True and pins.of(KEY) == []


# --- review of #385 (2026-10-21): one regression test per finding ----------------------------------------------------
OTHER = "other-spots"


def other(v: str, licence: str = "MIT") -> str:
    return (version(v).replace('id = "dark-spots"', f'id = "{OTHER}"').replace('impl = "dark-spots"', f'impl = "{OTHER}"')
            .replace('"install:dark-spots"', f'"install:{OTHER}"').replace('licence = ["dark-spots"]', f'licence = ["{OTHER}"]')
            .replace('"dark-spots 0', '"other-spots 0').replace('licence = "MIT"', f'licence = "{licence}"'))


def test_one_entrys_policy_never_moves_the_shared_channel_or_its_siblings(home, monkeypatch):
    """Finding 1: the channel's pin is every entry's; one entry's update is fetched for it alone."""
    publish(home, OTHER, other("0.1"))
    m = turned_on(home, monkeypatch)
    pinned = next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"]
    policy.record(m, "auto", by="you", how="screen")  # other-spots stays manual
    publish(home, OTHER, other("9.9", licence="GPL-3.0"))
    fix(home, "0.2")
    assert prepare.run(KEY)["state"] == "applied" and active_version() == "dark-spots 0.2"
    assert next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"] == pinned, "the channel didn't move"
    sibling = catalog.get(OTHER)
    assert sibling.version == "other-spots 0.1" and sibling.component["licence"] == "MIT", "the manual sibling is as it was"
    assert catalog.get(KEY).version == "dark-spots 0.1", "the catalog is behind the entry, never ahead of it"
    now = status.of(catalog.get(KEY))
    assert now["state"] == "applied" and now["candidate"] is None, "the older catalog version is no update"
    assert env_runner.update_waiting(catalog.get(KEY)) == "", "and the card offers none"
    assert prepare.run(KEY)["says"] == "Up to date", "nor does the next check"


def test_a_stop_between_promotion_and_its_first_check_resumes_the_check(home, monkeypatch):
    """Finding 2: the first check is never lost; an attempt that died while applying runs it again and rolls back."""
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    real = promote.check_after
    monkeypatch.setattr(promote, "check_after", lambda *a, **k: (_ for _ in ()).throw(SystemExit("killed")))
    fix(home, "0.2")
    with pytest.raises(SystemExit):
        prepare.run(KEY)
    row = prepare.last(KEY)
    assert row["state"] == "applying" and active_version() == "dark-spots 0.2"
    row.update(pid=0)
    store.write(prepare.path(KEY), row)  # the process is gone
    now = status.of(catalog.get(KEY))
    assert now["state"] == "checking" and "didn't finish; it runs again" in now["says"]
    monkeypatch.setattr(promote, "check_after", real)
    (env_runner.selected(catalog.get(KEY)).folder / "bin" / "python").unlink()  # and the promoted version is broken
    resumed = prepare.run(KEY)
    assert resumed["state"] == "failed" and active_version() == "dark-spots 0.1", resumed
    assert [d["action"] for d in promote.decisions(KEY)] == ["promote", "checked", "rollback"]


def test_manual_apply_resumes_its_first_check_after_interruption(home, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.maintenance import commands as maintenance_commands

    m = turned_on(home, monkeypatch)
    policy.record(m, "review", by="you", how="screen")
    fix(home, "0.2")
    assert prepare.run(KEY)["state"] == "ready-to-apply"
    policy.record(m, "manual", by="you", how="screen")
    app = SimpleNamespace()
    monkeypatch.setattr(maintenance_commands, "_reply", lambda app, m, says: {"ok": True, "says": says})
    real_check = promote.check_after
    monkeypatch.setattr(promote, "check_after", lambda *a, **k: (_ for _ in ()).throw(SystemExit("interrupted")))
    with pytest.raises(SystemExit, match="interrupted"):
        asyncio.run(maintenance_commands.maintenance_apply(app, None, {"model": KEY}))
    row = prepare.last(KEY)
    assert row["state"] == "applying" and active_version() == "dark-spots 0.2"
    row["pid"] = 0  # The app that owned Apply exited; the new app must resume even under manual policy.
    store.write(prepare.path(KEY), row)
    assert KEY in prepare.unfinished()
    monkeypatch.setattr(promote, "check_after", real_check)
    (env_runner.selected(m).folder / "bin" / "python").unlink()
    resumed = prepare.run(KEY)
    assert resumed["state"] == "failed" and active_version() == "dark-spots 0.1"
    assert policy.get(KEY)["policy"] == "manual" and KEY not in prepare.unfinished()
    assert [d["action"] for d in promote.decisions(KEY)] == ["promote", "checked", "rollback"]


@pytest.mark.parametrize("after_rollback", [False, True])
def test_failed_first_check_resumes_rollback_without_claiming_applied(home, monkeypatch, after_rollback):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    monkeypatch.setattr(promote, "first_check", lambda *a, **k: "first check failed")
    real_rollback = promote.rollback

    def interrupted(*args, **kwargs):
        if after_rollback:
            real_rollback(*args, **kwargs)
        raise SystemExit("interrupted rollback")

    monkeypatch.setattr(promote, "rollback", interrupted)
    with pytest.raises(SystemExit, match="interrupted rollback"):
        prepare.run(KEY)
    assert any(d["action"] == "checked" and not d["ok"] for d in promote.decisions(KEY))
    row = prepare.last(KEY)
    assert row["state"] == "applying"
    row["pid"] = 0
    store.write(prepare.path(KEY), row)
    monkeypatch.setattr(promote, "rollback", real_rollback)
    monkeypatch.setattr(promote, "first_check", lambda *a, **k: pytest.fail("a retained failed check must not rerun"))
    resumed = prepare.run(KEY)
    assert resumed["state"] == "failed" and active_version() == "dark-spots 0.1"
    assert "first check failed" in resumed["why"] and policy.get(KEY).get("held")
    assert [d["action"] for d in promote.decisions(KEY)] == ["promote", "checked", "rollback"]
    assert KEY not in prepare.unfinished()


def test_a_pin_holds_its_version_and_only_a_persons_own_choice_ends_it(home, monkeypatch):
    """Findings 3 and 6: under pin nothing moves the pointer unattended; your own choice of another version ends the
    pin, recorded; the pin is a hold removal respects."""
    m = turned_on(home, monkeypatch)
    v1 = env_runner.selected(m).folder.name
    policy.record(m, "pin", by="you", how="screen")
    assert pins.of(KEY) == [{**pins.of(KEY)[0], "generation": v1, "kind": "user", "owner": "pin"}]
    with pytest.raises(envs.Failed, match="kept for what is pinned to it"):
        envs.remove(catalog.get(KEY))
    publish(home, KEY, version("0.2"), ADAPTER.replace("< 128", "< 129"))
    channels.update()
    assert install(home, monkeypatch)["state"] == "done"
    v2 = envs.folder(catalog.get(KEY)).name
    with pytest.raises(promote.Failed, match="pinned"):
        promote.promote(catalog.get(KEY), v2, by="maintenance:auto")
    assert status.of(catalog.get(KEY))["says"] == "Pinned to dark-spots 0.1"
    env_runner.choose(catalog.get(KEY), v2)  # your own choice
    rec = policy.get(KEY)
    assert rec["policy"] == "manual" and rec["why"] == "you chose another version, so it is no longer pinned"
    assert pins.of(KEY) == [] and status.of(catalog.get(KEY))["state"] != "pinned"


def test_apply_is_offered_only_for_a_version_ready_and_proven_here(home, monkeypatch):
    """Finding 4: under manual a catalog update not yet installed points to Install; Apply appears once it's proven."""
    turned_on(home, monkeypatch)
    publish(home, KEY, version("0.2"), ADAPTER.replace("< 128", "< 129"))
    channels.update()
    now = status.of(catalog.get(KEY))
    assert now["state"] == "available" and now["next"] is None and now["says"].endswith("Install checks it beside the one in use")
    assert install(home, monkeypatch)["state"] == "done"
    now = status.of(catalog.get(KEY))
    assert now["state"] == "ready-to-apply" and now["next"] == "apply" and now["candidate"]["proven"] is True


def test_a_build_in_another_process_is_never_deleted_or_raced(home, monkeypatch):
    """Finding 5: the generation's build lock is held across processes; Install and removal wait for it."""
    m = turned_on(home, monkeypatch)
    publish(home, KEY, version("0.2"), ADAPTER.replace("< 128", "< 129"))
    channels.update()
    d = envs.folder(catalog.get(KEY))
    d.mkdir(parents=True)  # half made, no receipt yet: what a build in progress looks like
    with envs.building(d, "Dark spots"):  # another process's build (flock: per open file, so this one counts)
        with pytest.raises(envs.Failed, match="being prepared by another process"):
            envs.build(catalog.get(KEY))
        assert d.is_dir(), "its folder is left alone"
        with pytest.raises(envs.Failed, match="being prepared by another process"):
            envs.remove(catalog.get(KEY))
    assert envs.build(catalog.get(KEY)) == d, "once it's free, the build goes on"
    assert m is not None


# --- review of #385, round 2 -----------------------------------------------------------------------------------------
def _stuck_applying(home, monkeypatch) -> None:
    """An auto update stopped after its promotion, before its first check (the process killed while applying)."""
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    real = promote.check_after
    monkeypatch.setattr(promote, "check_after", lambda *a, **k: (_ for _ in ()).throw(SystemExit("killed")))
    fix(home, "0.2")
    with pytest.raises(SystemExit):
        prepare.run(KEY)
    monkeypatch.setattr(promote, "check_after", real)


def test_stop_while_applying_keeps_the_first_check_to_run(home, monkeypatch):
    """Round 2, finding 1: Stop (or a policy change) during applying never turns it into 'stopped'; the first check
    still runs and rolls back a broken promotion."""
    from orchestrator.maintenance import worker

    _stuck_applying(home, monkeypatch)
    keeper = worker.Maintainer(environ=dict(os.environ), first_after=3600)
    keeper._asked.append(KEY)  # queued: what Stop or a policy change cancels
    assert keeper.cancel(KEY) is True
    row = prepare.last(KEY)
    assert row["state"] == "applying" and row["pid"] == 0 and keeper._asked == [KEY], "kept, and queued to resume"
    (env_runner.selected(catalog.get(KEY)).folder / "bin" / "python").unlink()
    resumed = prepare.run(KEY)
    assert resumed["state"] == "failed" and active_version() == "dark-spots 0.1"
    assert [d["action"] for d in promote.decisions(KEY)] == ["promote", "checked", "rollback"]


def test_an_unfinished_apply_is_resumed_at_once_under_any_policy(home, monkeypatch):
    """Round 2, finding 2: a quit while applying is resumed by the next tick, also under manual, not a day later."""
    from orchestrator.maintenance import worker

    _stuck_applying(home, monkeypatch)
    row = prepare.last(KEY)
    row.update(pid=0)
    store.write(prepare.path(KEY), row)
    policy.record(catalog.get(KEY), "manual", by="you", how="screen")
    keeper = worker.Maintainer(environ={**os.environ, "MICROMIND_CATALOG_AUTO": "0"}, first_after=3600)
    assert prepare.unfinished() == [KEY] and keeper._next() == (KEY, True), "taken now, whatever its policy and schedule"
    assert prepare.run(KEY)["state"] == "applied", "the resumed check passes and the update stands"
    assert prepare.unfinished() == []


def test_removal_waits_for_a_running_attempt_and_keeps_build_locks(home, monkeypatch):
    """Round 2, finding 3."""
    m = turned_on(home, monkeypatch)
    d = env_runner.selected(m).folder
    store.write(prepare.path(KEY), {"key": KEY, "state": "preparing", "pid": os.getpid()})
    with pytest.raises(envs.Failed, match="being prepared right now"):
        envs.remove(catalog.get(KEY))
    store.write(prepare.path(KEY), {"key": KEY, "state": "none", "pid": 0})
    with envs.building(d, "Dark spots"):
        pass
    assert envs.remove(catalog.get(KEY)) is True
    assert envs.lock_path(d).is_file(), "the lock file stays, outside the entry's folder: no second builder can race it"


def test_undo_under_a_pin_ends_the_pin(home, monkeypatch):
    """Round 2, finding 4: a person's Undo moves the pointer, so a pin to the version undone ends, recorded."""
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    assert prepare.run(KEY)["state"] == "applied"
    policy.record(catalog.get(KEY), "pin", by="you", how="screen")  # pinned to 0.2
    promote.undo(catalog.get(KEY))
    assert active_version() == "dark-spots 0.1"
    assert policy.get(KEY)["policy"] == "manual" and pins.of(KEY) == [] or all(p["kind"] != "user" for p in pins.of(KEY))


# --- review of #385, round 3 -----------------------------------------------------------------------------------------
def test_an_automatic_rollback_ends_a_pin_to_the_version_rolled_back_from(home, monkeypatch):
    """Round 3, finding 1: pinned while applying, then the first check fails: the pin ends, recorded with why."""
    _stuck_applying(home, monkeypatch)
    policy.record(catalog.get(KEY), "pin", by="you", how="screen")  # pinned to the version being applied
    row = prepare.last(KEY)
    row.update(pid=0)
    store.write(prepare.path(KEY), row)
    (env_runner.selected(catalog.get(KEY)).folder / "bin" / "python").unlink()
    assert prepare.run(KEY)["state"] == "failed" and active_version() == "dark-spots 0.1"
    rec = policy.get(KEY)
    assert rec["policy"] == "manual" and rec["how"] == "automatic-rollback" and "by itself" in rec["why"]
    assert status.of(catalog.get(KEY))["state"] != "pinned"


def test_a_local_only_day_still_resumes_an_unfinished_apply(home, monkeypatch):
    """Round 3, finding 2: the resumed first check needs no network, so local-only doesn't keep it from running."""
    from orchestrator.maintenance import worker

    _stuck_applying(home, monkeypatch)
    row = prepare.last(KEY)
    row.update(pid=0)
    store.write(prepare.path(KEY), row)
    keeper = worker.Maintainer(environ=dict(os.environ), first_after=3600, allowed=lambda: "this session is local only")
    assert keeper.runnable(KEY) is True, "its resume runs"
    assert keeper.runnable("another-entry") is False, "anything that would look online waits"


def test_no_stop_is_offered_while_applying(home, monkeypatch):
    """Round 3, finding 3."""
    m = turned_on(home, monkeypatch)
    store.write(prepare.path(KEY), {"key": KEY, "state": "applying", "pid": os.getpid(),
                                    "says": "Applying dark-spots 0.2: making it active, then checking it runs"})
    now = status.of(m)
    assert now["state"] == "applying" and now["next"] is None and now["says"].endswith("its first check follows")


def test_check_queues_an_unfinished_apply_under_manual_or_pin(home, monkeypatch):
    """Round 3, missing test: a person's Check reaches the resume whatever the policy."""
    import asyncio
    from types import SimpleNamespace

    from orchestrator.maintenance import commands as maintenance_commands

    _stuck_applying(home, monkeypatch)
    row = prepare.last(KEY)
    row.update(pid=0)
    store.write(prepare.path(KEY), row)
    for recorded in ("manual", "pin"):
        policy.record(catalog.get(KEY), recorded, by="you", how="screen")
        asked = []
        app = SimpleNamespace(maintainer=SimpleNamespace(now=asked.append), day=SimpleNamespace(local_only=False))
        monkeypatch.setattr(maintenance_commands, "_reply", lambda app, m, says: {"ok": True, "says": says})
        out = asyncio.run(maintenance_commands.maintenance_check(app, None, {"model": KEY}))
        assert asked == [KEY] and "first check after applying didn't finish" in out["says"], (recorded, out)


def test_a_pinned_move_to_a_version_not_ready_keeps_the_pin(home, monkeypatch):
    """Round 3, missing test: a person's move under a pin to a generation not here, ready and proven is refused, and
    the pin stays."""
    m = turned_on(home, monkeypatch)
    policy.record(m, "pin", by="you", how="screen")
    with pytest.raises(promote.Failed, match="no longer here, ready and proven"):
        promote.promote(catalog.get(KEY), "0" * 16, by="user")
    assert policy.get(KEY)["policy"] == "pin" and any(p["kind"] == "user" for p in pins.of(KEY))


# --- review of #385, round 4 -----------------------------------------------------------------------------------------
def test_a_rollback_after_your_apply_ends_a_pin_too(home, monkeypatch):
    """Round 4 (a): the first check after a person's Apply fails ("user:first-check"): a pin to the version rolled
    back from ends, recorded."""
    m = turned_on(home, monkeypatch)
    publish(home, KEY, version("0.2"), ADAPTER.replace("< 128", "< 129"))
    channels.update()
    assert install(home, monkeypatch)["state"] == "done"
    v2 = envs.folder(catalog.get(KEY)).name
    decision = promote.promote(catalog.get(KEY), v2, by="user", check=False)
    policy.record(catalog.get(KEY), "pin", by="you", how="screen")  # pinned to what was just applied
    (env_runner.selected(catalog.get(KEY)).folder / "bin" / "python").unlink()
    back = promote.check_after(catalog.get(KEY), decision, by="user")
    assert back["action"] == "rollback" and back["by"] == "user:first-check" and active_version() == "dark-spots 0.1"
    assert policy.get(KEY)["policy"] == "manual" and policy.get(KEY)["how"] == "automatic-rollback"


def test_stop_while_applying_says_the_first_check_still_runs(home, monkeypatch):
    """Round 4 (b)."""
    import asyncio
    from types import SimpleNamespace

    from orchestrator.maintenance import commands as maintenance_commands, worker

    _stuck_applying(home, monkeypatch)
    keeper = worker.Maintainer(environ=dict(os.environ), first_after=3600)
    keeper._asked.append(KEY)
    app = SimpleNamespace(maintainer=keeper, day=SimpleNamespace(local_only=False))
    monkeypatch.setattr(maintenance_commands, "_reply", lambda app, m, says: {"ok": True, "says": says})
    out = asyncio.run(maintenance_commands.maintenance_stop(app, None, {"model": KEY}))
    assert "its first check still runs" in out["says"] and not out["says"].startswith("Stopped"), out


def test_a_check_that_found_nothing_newer_says_up_to_date_and_when(home, monkeypatch):
    # Ticket 67, fix 5: Details said nothing after a check found nothing (only Activity said "up to date").
    m = turned_on(home, monkeypatch)
    assert status.of(m)["says"] == "", "no check yet: nothing to say"
    assert discover.check(m)["ok"] is True
    now = status.of(catalog.get(KEY))
    at = time.mktime(time.strptime(now["check"]["at"], "%Y-%m-%dT%H:%M:%S"))
    from orchestrator.library import when_words

    assert now["state"] == "none" and now["says"] == f"Up to date · checked {when_words(at)}"
    assert "T" not in now["says"].split("checked ")[1], "a readable time, never ISO"


def test_a_tool_from_a_shared_file_says_it_doesnt_update_itself_never_up_to_date(home, monkeypatch, tmp_path):
    """The thesis check on 0.5.2: a tool from a shared file said "Up to date", though no shared file has online revisions
    and a newer one is added as its own entry (channels.update refuses to move it)."""
    import asyncio
    from types import SimpleNamespace

    from orchestrator import mine, share
    from orchestrator.maintenance import commands as maintenance_commands
    from tests.test_share import yours

    path = share.export(yours(home), by="Agent check")
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "another-mac"))  # opened on another Mac
    catalog.manifests.cache_clear()
    m = catalog.get(mine.add(str(path))["model"])
    assert status.shared_file(m) and not status.shared_file(catalog.get("spot-shape"))
    assert discover.check(m)["ok"] is True  # a check finds nothing: still not "up to date"
    now = status.of(catalog.get(m.key))
    assert now["state"] == "none" and now["says"] == status.SHARED_FILE and now["sharedFile"] is True, now
    app = SimpleNamespace(day=SimpleNamespace(local_only=False))
    monkeypatch.setattr(maintenance_commands, "_reply", lambda app, m, says: {"ok": True, "says": says})
    said = asyncio.run(maintenance_commands.maintenance_check(app, None, {"model": m.key}))["says"]
    assert said.endswith("doesn't update itself. A newer shared file is added as its own entry"), said
    assert "up to date" not in said.lower()


def test_an_applied_update_says_when_in_readable_words(home, monkeypatch):
    import re

    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    assert prepare.run(KEY)["state"] == "applied"
    says = status.of(catalog.get(KEY))["says"]
    assert re.fullmatch(r"Updated to dark-spots 0\.2 \([A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} [ap]m\)", says), says


def test_a_built_in_entry_is_said_to_update_with_hooke(home, monkeypatch, tmp_path):
    # Ticket 67, fix 5: built-in Cellpose-SAM had no update state at all in its Details.
    from orchestrator import build_info

    packaged = tmp_path / "packaged.json"
    packaged.write_text(json.dumps({"app": "HookeUI", "version": "0.5.2"}))
    monkeypatch.setattr(build_info, "PACKAGED", packaged)
    wire = status.wire()
    assert wire["cellpose-sam"] == {"state": "none", "says": "Updated with Hooke 0.5.2", "next": None, "builtIn": True,
                                    "version": catalog.get("cellpose-sam").version}
    assert all(not catalog.get(k).runs == "cloud" for k, u in wire.items() if u.get("builtIn")), "a provider's model isn't"
    assert status.of(catalog.get("cellpose-sam")) == {}, "maintenance itself still covers only entries with their own Python"
    packaged.unlink()  # a source checkout: no version of its own to name
    assert status.wire()["cellpose-sam"]["says"] == "Updated with Hooke: it comes with this copy of the app"
