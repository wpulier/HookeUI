"""Capability maintenance beyond the first journey (ticket 59's MT2–MT4): accepting what needs review, conflicting
adaptations, revoked versions and missing sources, and many entries at once. A scratch upstream repository as the
reviewed channel and a stand-in uv, as tests/test_maintenance.py."""
import os
from pathlib import Path

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_runner, operations
from orchestrator.maintenance import discover, policy, prepare, revocations, status
from tests.test_channels import _git, home, publish  # noqa: F401 - `home` is the fixture
from tests.test_env_runner import ADAPTER, run_it
from tests.test_maintenance import KEY, active_version, fix, turned_on, version  # noqa: F401


@pytest.fixture(autouse=True)
def every_check_looks(monkeypatch):
    monkeypatch.setattr(discover, "NEWEST_S", 0)


def notices(home, text: str) -> str:
    """The catalog's maintenance.toml, committed upstream beside its entries."""
    (home / "upstream" / "catalog" / "models" / revocations.FILE).write_text(text)
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", "maintenance notices", cwd=home / "upstream")
    return _git("rev-parse", "HEAD", cwd=home / "upstream")


REVOKED = '''[[revoked]]
entry = "dark-spots"
version = "dark-spots {v}"
why = "it counts touching spots as one"
link = "https://example.org/issues/7"
severity = "{severity}"
'''


# --- MT3: revoked versions -------------------------------------------------------------------------------------------
def test_a_revoked_version_in_use_needs_attention_and_keeps_working_when_only_warned(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    notices(home, REVOKED.format(v="0.1", severity="warn"))
    channels.update()
    m = catalog.get(KEY)
    now = status.of(m)
    assert now["maintained"]["state"] == "attention" and "counts touching spots as one" in now["maintained"]["why"]
    assert now["defect"] == {"version": "dark-spots 0.1", "why": "it counts touching spots as one",
                             "link": "https://example.org/issues/7", "severity": "warn"}
    assert now["says"].startswith("Needs attention: dark-spots 0.1 was revoked by its maintainers")
    env_runner.register()
    assert len(run_it(m)["polygons"]) == 3, "a warning never stops its runs"


def test_a_version_revoked_with_block_refuses_its_runs_and_says_what_to_do(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    notices(home, REVOKED.format(v="0.1", severity="block"))
    channels.update()
    m = catalog.get(KEY)
    env_runner.register()
    with pytest.raises(operations.Unavailable, match="revoked by its maintainers: it counts touching spots as one") as e:
        run_it(m)
    assert e.value.next == "update it, or choose another version on its card"
    assert env_runner.selected(m) is not None, "the revoked version stays installed: nothing is deleted"


def test_a_revoked_update_is_never_prepared_or_promoted(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    fix(home, "0.2")
    notices(home, REVOKED.format(v="0.2", severity="warn"))
    row = prepare.run(KEY)
    assert row["state"] == "unavailable" and "0.2 was revoked by its maintainers" in row["says"], row
    assert active_version() == "dark-spots 0.1"
    assert status.of(catalog.get(KEY))["state"] == "unavailable"


# --- MT3: missing sources --------------------------------------------------------------------------------------------
def test_an_entry_removed_upstream_is_kept_and_the_channel_is_not_moved_by_itself(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    pinned = next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"]
    policy.record(m, "auto", by="you", how="screen")
    _git("rm", "-r", "-q", "catalog/models/dark-spots", cwd=home / "upstream")
    (home / "upstream" / "catalog" / "models" / "keep").mkdir(parents=True)
    (home / "upstream" / "catalog" / "models" / "keep" / "model.toml").write_text(version("1").replace(
        'id = "dark-spots"', 'id = "other-spots"'))
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", "dark-spots removed", cwd=home / "upstream")
    row = prepare.run(KEY)
    assert "no longer has it; it keeps working here" in row["says"], row
    assert next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"] == pinned, "not moved unattended"
    m = catalog.get(KEY)
    assert m is not None and active_version() == "dark-spots 0.1", "it keeps working here"
    now = status.of(m)
    assert now["maintained"]["state"] == "unmaintained" and "removed from its catalog" in now["maintained"]["why"]


def test_a_source_unreachable_for_days_is_said_as_such_never_as_unmaintained(home, monkeypatch):
    """#402 review, item 7: a Mac offline for a while is not an abandoned package. Three failed checks within minutes
    say nothing; after UNREACHABLE_S the entry needs attention: "couldn't reach its source since T"."""
    from orchestrator.maintenance import store

    m = turned_on(home, monkeypatch)

    def gone(url):
        raise channels.CantDo("there is no connection to its catalog")

    monkeypatch.setattr(channels, "newest", gone)
    for _ in range(discover.UNREACHABLE):
        discover.check(m)
    assert status.of(catalog.get(KEY))["maintained"] is None, "minutes offline say nothing"
    checks = store.read(store.path("checks.json"), {})
    checks[KEY]["failingSince"] = "2026-10-01T09:00:00"  # offline since then
    store.write(store.path("checks.json"), checks)
    now = status.of(catalog.get(KEY))
    assert now["maintained"]["state"] == "attention"
    assert now["maintained"]["why"].startswith("couldn't reach its source since 2026-10-01T09:00:00")
    assert "Needs attention: couldn't reach its source" in now["says"], "the card and the agent say it too"
    assert now["active"]["version"] == "dark-spots 0.1"


def test_a_catalog_can_say_nobody_maintains_an_entry(home, monkeypatch):
    turned_on(home, monkeypatch)
    notices(home, '[[unmaintained]]\nentry = "dark-spots"\nsince = "2026-10-01"\nwhy = "its maintainers stopped"\n')
    channels.update()
    assert status.of(catalog.get(KEY))["maintained"] == {"state": "unmaintained", "why": "its maintainers stopped",
                                                         "since": "2026-10-01"}


def test_the_daily_look_takes_the_notices_too(home, monkeypatch):
    turned_on(home, monkeypatch)
    notices(home, REVOKED.format(v="0.1", severity="warn"))
    looked = channels.peek()
    assert (os.path.isfile(os.path.join(looked["folder"], revocations.FILE)))
    assert status.of(catalog.get(KEY))["maintained"]["state"] == "attention", "said before anything is fetched"


# --- MT2: accepting what an update changes ---------------------------------------------------------------------------
LICENCE = 'licence = "MIT"'


def test_a_person_accepts_exactly_what_changed_and_then_auto_promotes_it(home, monkeypatch):
    from orchestrator.maintenance import promote

    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    publish(home, KEY, version("0.2").replace(LICENCE, 'licence = "Apache-2.0"'), ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    assert row["state"] == "needs-review" and row["fingerprint"], row
    now = status.of(catalog.get(KEY))
    assert now["next"] == "accept" and now["review"][0]["what"] == "component licence"
    from orchestrator.maintenance import review

    review.accept(catalog.get(KEY), row["fingerprint"], row["review"], by="you", how="screen")
    assert prepare.run(KEY)["state"] == "applied" and active_version() == "dark-spots 0.2"
    assert [d["action"] for d in promote.decisions(KEY)] == ["accept", "promote", "checked"]
    publish(home, KEY, version("0.3").replace(LICENCE, 'licence = "GPL-3.0"'), ADAPTER.replace("< 128", "< 130"))
    assert prepare.run(KEY)["state"] == "needs-review", "another change needs another acceptance"
    assert active_version() == "dark-spots 0.2"


def test_a_change_in_what_it_computes_is_prepared_first_and_accepting_only_promotes(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    # It gives masks now, not labelled cells: what its output means changed (science), no permission or cost.
    publish(home, KEY, version("0.2").replace('output = "labels.instance.2d"', 'output = "masks.2d"'),
            ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    assert row["state"] == "needs-review" and row.get("prepared") is True, row
    assert row["says"].endswith("(prepared and proven here)") and active_version() == "dark-spots 0.1"
    from orchestrator import envs
    from orchestrator.maintenance import review

    assert envs.python(catalog.get(KEY)) is not None, "built and proven before the person looks"
    review.accept(catalog.get(KEY), row["fingerprint"], row["review"], by="you", how="screen")
    assert prepare.run(KEY)["state"] == "applied" and active_version() == "dark-spots 0.2"


def test_apply_cannot_skip_a_prepared_candidates_changed_output_review(home, monkeypatch):
    """A stale Apply request must not substitute for accepting the exact scientific change."""
    import asyncio
    from types import SimpleNamespace

    from orchestrator.brain.registry import ToolFailed
    from orchestrator.maintenance import commands as maintenance_commands, promote

    m = turned_on(home, monkeypatch)
    policy.record(m, "review", by="you", how="screen")
    publish(home, KEY, version("0.2").replace('output = "labels.instance.2d"', 'output = "masks.2d"'),
            ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    now = status.of(catalog.get(KEY))
    assert row["state"] == "needs-review" and now["candidate"]["proven"]
    app = SimpleNamespace(maintainer=SimpleNamespace(now=lambda key: None))
    monkeypatch.setattr(maintenance_commands, "_reply", lambda app, m, says: {"ok": True, "says": says})
    with pytest.raises(ToolFailed, match="Needs review"):
        asyncio.run(maintenance_commands.maintenance_apply(app, None, {"model": KEY}))
    assert active_version() == "dark-spots 0.1" and promote.decisions(KEY) == []
    # The exact existing Accept transition clears the gate; no new preparation or special route is required.
    asyncio.run(maintenance_commands.maintenance_accept(app, None,
                {"model": KEY, "fingerprint": row["fingerprint"]}))
    assert prepare.run(KEY)["state"] == "ready-to-apply"
    asyncio.run(maintenance_commands.maintenance_apply(app, None, {"model": KEY}))
    assert active_version() == "dark-spots 0.2"
    assert [decision["action"] for decision in promote.decisions(KEY)] == ["accept", "promote", "checked"]


def test_promotion_rechecks_the_transition_after_a_concurrent_undo(home, monkeypatch):
    """Undo between admission and the pointer lock must invalidate an earlier compatible transition."""
    from contextlib import contextmanager
    from orchestrator.maintenance import promote, review, store

    m = turned_on(home, monkeypatch)
    policy.record(m, "review", by="you", how="screen")
    publish(home, KEY, version("0.2").replace('output = "labels.instance.2d"', 'output = "masks.2d"'),
            ADAPTER.replace("< 128", "< 129"))
    candidate = prepare.run(KEY)
    review.accept(m, candidate["fingerprint"], candidate["review"], by="you", how="screen")
    promote.promote(m, candidate["target"]["generation"], by="user", check=False)
    publish(home, KEY, version("0.3").replace('output = "labels.instance.2d"', 'output = "masks.2d"'),
            ADAPTER.replace("< 128", "< 130"))
    candidate = prepare.run(KEY)
    assert candidate["state"] == "ready-to-apply"
    real_lock = store.locked
    interleaved = False

    @contextmanager
    def undo_before_lock(environ=os.environ):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            promote.undo(m, environ)
        with real_lock(environ):
            yield

    monkeypatch.setattr(store, "locked", undo_before_lock)
    with pytest.raises(promote.Failed, match="Needs review"):
        promote.promote(m, candidate["target"]["generation"], by="user", check=False)
    assert interleaved and active_version() == "dark-spots 0.1"
    assert promote.decisions(KEY)[-1]["action"] == "rollback"


def test_acceptance_is_not_setup_a_new_key_still_comes_first(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setenv("MICROMIND_KEYS_FILE", str(home / "no-keys.txt"))
    publish(home, KEY, version("0.2").replace('needs_to_run = ["install:dark-spots"]',
                                              'needs_to_run = ["install:dark-spots", "key:FAL_KEY"]'),
            ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    assert row["state"] == "needs-review"
    from orchestrator.maintenance import review

    review.accept(catalog.get(KEY), row["fingerprint"], row["review"], by="you", how="screen")
    row = prepare.run(KEY)
    assert row["state"] == "needs-review" and row["setup"].startswith("Add your") and "First: Add your" in row["says"], row
    assert active_version() == "dark-spots 0.1", "never promoted into a version that can't run here"
    assert status.of(catalog.get(KEY))["next"] is None


# --- MT2: your version when what it was made from changes ------------------------------------------------------------
SPOTS = "test-spots"


def spots(home, change: str = "", adapter_change: tuple[str, str] | None = None) -> str:
    """spot-shape (format 2) published upstream as `test-spots`, optionally changed."""
    import shutil

    from orchestrator.paths import REPO

    folder = home / "upstream" / "catalog" / "models" / SPOTS
    shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(REPO / "catalog" / "models" / "spot-shape", folder)
    text = "\n".join(line for line in (folder / "model.toml").read_text().splitlines() if not line.startswith(("derived_from", "#")))
    text = (text.replace('id = "spot-shape"', f'id = "{SPOTS}"').replace('licence = ["spot-shape"]', f'licence = ["{SPOTS}"]')
            .replace('"install:spot-shape"', f'"install:{SPOTS}"')) + "\n" + change
    (folder / "model.toml").write_text(text)
    if adapter_change:
        a = folder / "adapter.py"
        a.write_text(a.read_text().replace(*adapter_change))
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", f"{SPOTS}", cwd=home / "upstream")
    channels.update()
    return _git("rev-parse", "HEAD", cwd=home / "upstream")


def yours(home, edit: tuple[str, str] | None = None) -> catalog.Manifest:
    from orchestrator import contribute

    folder = contribute.derive(SPOTS, "my-spots")
    if edit:
        a = folder / "adapter.py"
        a.write_text(a.read_text().replace(*edit))
    catalog.manifests.cache_clear()
    return catalog.get("mine/my-spots")


def test_your_version_is_merged_onto_the_update_and_applied_with_your_files_kept(home, monkeypatch):
    from orchestrator.maintenance import adapt

    spots(home)
    mine = yours(home, ('grey = picture.mean(axis=2)', 'grey = picture.mean(axis=2)  # yours: the mean of R, G and B'))
    assert adapt.candidate(mine) is None, "nothing to merge while its base is as it was"
    spots(home, adapter_change=('"definition": "pixels in the spot"', '"definition": "the pixels in the spot"'))
    rec = discover.check(catalog.get("mine/my-spots"))
    assert rec["found"]["derived"], rec
    report = adapt.candidate(catalog.get("mine/my-spots"))
    assert report["base"]["found"] and report["conflicts"] == [] and report["files"]["adapter.py"] == "merged", report
    now = status.of(catalog.get("mine/my-spots"))
    assert now["state"] == "ready-to-apply" and now["next"] == "merge" and "merged without conflicts" in now["says"]
    row = adapt.apply(catalog.get("mine/my-spots"))
    text = (catalog.mine_root() / "my-spots" / "adapter.py").read_text()
    assert "# yours: the mean of R, G and B" in text and '"the pixels in the spot"' in text, "both changes kept"
    assert '"the pixels in the spot"' not in (Path(row["kept"]) / "adapter.py").read_text(), "your previous files kept"
    catalog.manifests.cache_clear()
    again = catalog.get("mine/my-spots")
    assert again.version == "2" and adapt.stale(again) is None, "yours now builds on the newest base"


def test_a_conflict_keeps_both_sides_and_nothing_is_applied_until_it_is_resolved(home, monkeypatch):
    from orchestrator.maintenance import adapt

    spots(home)
    mine = yours(home, ('grey < params["threshold"]', 'grey < params["threshold"] - 1'))
    assert os.access(Path(catalog.entry_folder(mine)) / "model.toml", os.W_OK), "your version's files are yours to edit"
    spots(home, adapter_change=('grey < params["threshold"]', 'grey <= params["threshold"]'))
    report = adapt.candidate(catalog.get("mine/my-spots"))
    assert report["conflicts"] == ["adapter.py"], report
    folder = Path(report["folder"])
    assert (folder / "adapter.py.yours").is_file() and (folder / "adapter.py.update").is_file()
    assert "<<<<<<< yours" in (folder / "adapter.py").read_text()
    now = status.of(catalog.get("mine/my-spots"))
    assert now["state"] == "needs-review" and "both changed adapter.py; both are kept" in now["says"]
    before = (catalog.mine_root() / "my-spots" / "adapter.py").read_text()
    with pytest.raises(ValueError, match="resolve adapter.py"):
        adapt.apply(catalog.get("mine/my-spots"))
    assert (catalog.mine_root() / "my-spots" / "adapter.py").read_text() == before, "yours untouched"
    (folder / "adapter.py").write_text((folder / "adapter.py.update").read_text())  # the person resolves it
    adapt.apply(catalog.get("mine/my-spots"))
    assert "<= params" in (catalog.mine_root() / "my-spots" / "adapter.py").read_text()


def test_a_base_not_kept_here_merges_once_you_resolved_each_file(home, monkeypatch):
    """#402 review round 2, item 3: without the base as you derived it, every file both sides have differently is kept
    both ways; once you resolved those, it merges, and only what is still unresolved is ever named."""
    from orchestrator.maintenance import adapt

    spots(home)
    yours(home, ('grey < params["threshold"]', 'grey < params["threshold"] - 1'))
    spots(home, adapter_change=('"definition": "pixels in the spot"', '"definition": "the pixels in the spot"'))
    monkeypatch.setattr(adapt, "base_then", lambda m, environ=os.environ: None)  # the base then isn't kept here
    report = adapt.candidate(catalog.get("mine/my-spots"))
    assert report["base"]["found"] is False and "adapter.py" in report["conflicts"], report
    folder = Path(report["folder"])
    now = status.of(catalog.get("mine/my-spots"))
    assert now["state"] == "needs-review" and "isn't kept here" in now["says"] and "resolve " in now["says"]
    for name in report["conflicts"]:  # the person resolves each, one at a time
        others = [n for n in report["conflicts"] if n != name and n in adapt.unresolved(folder, report)]
        (folder / name).write_text((folder / (name + ".update")).read_text())
        if others:
            with pytest.raises(ValueError) as e:
                adapt.apply(catalog.get("mine/my-spots"))
            assert name not in str(e.value).split(" in ")[0], "an already-resolved file isn't named"
    now = status.of(catalog.get("mine/my-spots"))
    assert now["state"] == "ready-to-apply" and now["next"] == "merge", now
    assert adapt.words(report, []).startswith("Ready to apply: your version on "), "nothing left: no kept-both-ways words"
    monkeypatch.setattr(policy, "effective", lambda m, environ=os.environ: ("review", ""))  # yours isn't turned on here
    row = prepare.run("mine/my-spots")  # prepare's own words agree with the card (prepare.py's derived branch)
    assert row["state"] == "ready-to-apply" and row["says"].startswith("Ready to apply"), row
    adapt.apply(catalog.get("mine/my-spots"))
    assert '"the pixels in the spot"' in (catalog.mine_root() / "my-spots" / "adapter.py").read_text()


def test_readiness_carries_the_update_state_only_when_there_is_one(home, monkeypatch):
    from orchestrator import readiness

    m = turned_on(home, monkeypatch)
    assert "update" not in readiness.model(KEY, {}).wire(), "nothing recorded, nothing to say: the wire is as before"
    policy.record(m, "review", by="you", how="screen")
    fix(home, "0.2")
    prepare.run(KEY)
    w = readiness.model(KEY, {}).wire()
    assert w["status"] == "ready" and w["update"]["state"] == "ready-to-apply", "can run now; its next version waits"


# --- review of #402 (MT2–MT4) ----------------------------------------------------------------------------------------
def test_a_field_both_changed_keeps_both_and_waits_for_you(home, monkeypatch):
    """Item 1: a field both sides changed is never dropped: yours in the file, the update's beside it and in merge.json;
    no merge is offered until you change the file; your added setup steps stay."""
    from orchestrator.maintenance import adapt

    spots(home)
    mine = yours(home)
    import tomllib

    from orchestrator.contribute import _manifest_text

    manifest = Path(catalog.entry_folder(mine)) / "model.toml"
    fields = tomllib.loads(manifest.read_text())
    fields["what"] = "Yours: " + fields["what"]
    fields["needs_to_run"] = [*fields["needs_to_run"], "key:FAL_KEY"]
    manifest.write_text(_manifest_text(fields))
    catalog.manifests.cache_clear()
    up = home / "upstream" / "catalog" / "models" / SPOTS / "model.toml"
    up.write_text(up.read_text().replace('\nwhat = "', '\nwhat = "Update: ', 1))
    _git("commit", "-q", "-am", "the update changes what", cwd=home / "upstream")
    channels.update()
    report = adapt.candidate(catalog.get("mine/my-spots"))
    assert report["fields"] and report["fields"][0]["key"] == "what" and report["fields"][0]["update"].startswith("Update: ")
    folder = Path(report["folder"])
    assert (folder / "model.toml.update").is_file() and "Update: " in (folder / "model.toml").read_text(), "both said"
    now = status.of(catalog.get("mine/my-spots"))
    assert now["state"] == "needs-review" and now["next"] is None, "no merge offered while it waits for you"
    with pytest.raises(ValueError, match="resolve model.toml"):
        adapt.apply(catalog.get("mine/my-spots"))
    text = (folder / "model.toml").read_text()
    assert "key:FAL_KEY" in text, "your added setup step stays"
    (folder / "model.toml").write_text(text.replace("Yours: ", "Both: "))  # you choose
    assert status.of(catalog.get("mine/my-spots"))["next"] == "merge"
    adapt.apply(catalog.get("mine/my-spots"))


def test_a_file_you_removed_that_the_update_changed_is_kept_both_ways(home, monkeypatch):
    """Item 1: removed on one side, changed on the other: kept, both sides said, and nothing applied until you choose."""
    from orchestrator.maintenance import adapt

    spots(home)
    mine = yours(home)
    (Path(catalog.entry_folder(mine)) / "requirements.in").unlink()  # you removed it
    catalog.manifests.cache_clear()
    upstream = home / "upstream" / "catalog" / "models" / SPOTS / "requirements.in"
    upstream.write_text(upstream.read_text() + "# pinned again\n")  # the update changed it
    _git("commit", "-q", "-am", "requirements changed", cwd=home / "upstream")
    channels.update()
    report = adapt.candidate(catalog.get("mine/my-spots"))
    folder = Path(report["folder"])
    assert "requirements.in" in report["conflicts"]
    assert (folder / "requirements.in.update").is_file() and (folder / "requirements.in.yours").is_file()
    with pytest.raises(ValueError, match="resolve"):
        adapt.apply(catalog.get("mine/my-spots"))
    (folder / "requirements.in").unlink()  # you keep it removed
    adapt.apply(catalog.get("mine/my-spots"))
    assert not (Path(catalog.entry_folder(catalog.get("mine/my-spots"))) / "requirements.in").exists()


def test_an_edit_after_the_check_is_never_overwritten(home, monkeypatch):
    """Item 2: the merge is made for your files as they were; you edit them since: Apply refuses until a new check."""
    from orchestrator.maintenance import adapt

    spots(home)
    yours(home)
    spots(home, adapter_change=('"definition": "pixels in the spot"', '"definition": "the pixels in the spot"'))
    assert adapt.candidate(catalog.get("mine/my-spots"))["conflicts"] == []
    a = Path(catalog.entry_folder(catalog.get("mine/my-spots"))) / "adapter.py"
    a.write_text(a.read_text() + "\n# an edit of yours after the check\n")
    with pytest.raises(ValueError, match="changed since its merge was prepared"):
        adapt.apply(catalog.get("mine/my-spots"))
    assert a.read_text().endswith("# an edit of yours after the check\n"), "yours untouched"
    report = adapt.candidate(catalog.get("mine/my-spots"))  # Check again: a new merge, with your edit
    assert "an edit of yours after the check" in (Path(report["folder"]) / "adapter.py").read_text()


def test_a_revoked_update_is_never_offered_or_applied(home, monkeypatch):
    """Item 3: proven here by Install, then revoked: status says unavailable, and promotion refuses whoever asks."""
    from orchestrator import envs
    from orchestrator.maintenance import promote
    from tests.test_env_runner import install

    turned_on(home, monkeypatch)
    publish(home, KEY, version("0.2"), ADAPTER.replace("< 128", "< 129"))
    channels.update()
    assert install(home, monkeypatch)["state"] == "done"
    notices(home, REVOKED.format(v="0.2", severity="warn"))
    channels.update()
    now = status.of(catalog.get(KEY))
    assert now["state"] == "unavailable" and now["next"] is None and "revoked" in now["says"]
    with pytest.raises(promote.Failed, match="revoked by its maintainers"):
        promote.promote(catalog.get(KEY), envs.folder(catalog.get(KEY)).name, by="user")
    assert active_version() == "dark-spots 0.1"


def test_accept_needs_exactly_the_fingerprint_shown(home, monkeypatch):
    """Item 4."""
    import asyncio
    from types import SimpleNamespace

    from orchestrator.brain.registry import ToolFailed
    from orchestrator.maintenance import commands as maintenance_commands

    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    publish(home, KEY, version("0.2").replace(LICENCE, 'licence = "Apache-2.0"'), ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    assert status.of(catalog.get(KEY))["fingerprint"] == row["fingerprint"], "the card carries what it shows"
    app = SimpleNamespace(maintainer=SimpleNamespace(now=lambda key: None), day=SimpleNamespace(local_only=False))
    monkeypatch.setattr(maintenance_commands, "_reply", lambda app, m, says: {"ok": True, "says": says})
    for wrong in ({"model": KEY}, {"model": KEY, "fingerprint": "something else"}):
        with pytest.raises(ToolFailed, match="different from what was shown"):
            asyncio.run(maintenance_commands.maintenance_accept(app, None, wrong))
    asyncio.run(maintenance_commands.maintenance_accept(app, None, {"model": KEY, "fingerprint": row["fingerprint"]}))
    from orchestrator.maintenance import promote, store
    accepted = promote.decisions(KEY)[-1]
    assert accepted["action"] == "accept"
    assert accepted["version"] == row["target"]["version"] != catalog.get(KEY).version
    assert accepted["generation"] == row["target"]["generation"]
    decisions = promote.decisions(KEY)
    # An attempt cannot replace the target between acceptance's read and append.
    with prepare._entry_lock(KEY, os.environ) as acquired:
        assert acquired
        with pytest.raises(ToolFailed, match="update is changing"):
            asyncio.run(maintenance_commands.maintenance_accept(app, None, {"model": KEY, "fingerprint": row["fingerprint"]}))
    # A matching displayed fingerprint alone cannot certify changed target metadata.
    row["target"]["version"] = "a different candidate"
    store.write(prepare.path(KEY), row)
    with pytest.raises(ToolFailed, match="different from what was shown"):
        asyncio.run(maintenance_commands.maintenance_accept(app, None, {"model": KEY, "fingerprint": row["fingerprint"]}))
    assert promote.decisions(KEY) == decisions


def test_a_new_licence_is_a_setup_step_acceptance_cant_skip(home, monkeypatch):
    """Item 5: a licence (account access) the candidate newly needs, not granted here, is named like a key is."""
    from orchestrator.maintenance import review

    m = turned_on(home, monkeypatch)
    candidate = __import__("dataclasses").replace(
        m, needs_to_get=(*m.needs_to_get, "licence:https://huggingface.co/someone/model"))
    assert review.missing_setup(m, candidate) == "Its licence: not confirmed yet", "never asked: not confirmed"
    from orchestrator import observed

    observed.record("access", KEY, "yes", "Hugging Face: your account has access", "asked")
    assert review.missing_setup(m, candidate) == "Its licence: not confirmed yet", "the pinned entry's answer isn't it"
    from orchestrator.maintenance.discover import update_target

    observed.record("access", update_target(KEY, candidate.version), "no", "not given access yet", "asked")
    assert review.missing_setup(m, candidate) == "Accept its licence on Hugging Face"
    observed.record("access", update_target(KEY, candidate.version), "yes", "Hugging Face: your account has access", "asked")
    assert review.missing_setup(m, candidate) == "", "a granted observation of the candidate clears it"
    later = __import__("dataclasses").replace(candidate, version="dark-spots 0.3")
    assert review.missing_setup(m, later) == "Its licence: not confirmed yet", "a later candidate is asked again"


def test_a_licence_only_the_candidate_needs_is_asked_of_the_candidates_source(home, monkeypatch):
    """#402 review round 2, item 2: observed.targets and probe_access read the candidate, not only the pinned entry."""
    import httpx

    from orchestrator import observed
    from orchestrator.maintenance import discover

    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    from orchestrator.maintenance import review

    gated = ('needs_to_get = ["licence:https://huggingface.co/someone/gated"]\n'
             'files = [{ path = "w.bin", bytes = 3, sha256 = "' + "a" * 64 + '", url = "https://huggingface.co/x" }]\n'
             '\n[[implements]]')
    text = version("0.2").replace('source = { url = "https://example.org/dark-spots" }',
                                  'source = { hf = "someone/gated", revision = "main" }').replace("\n[[implements]]", gated, 1)
    publish(home, KEY, text, ADAPTER.replace("< 128", "< 129"))
    row = prepare.run(KEY)
    assert row["state"] == "needs-review", row
    review.accept(catalog.get(KEY), row["fingerprint"], row["review"], by="you", how="screen")
    row = prepare.run(KEY)
    assert row["state"] == "needs-review" and row.get("setup") == "Its licence: not confirmed yet", row
    target = discover.update_target(KEY, "dark-spots 0.2")
    with_key = {**os.environ, "HF_TOKEN": "t"}
    assert ("access", target) in observed.targets(with_key)
    without = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
    assert ("access", target) not in observed.targets(without), "without a key nothing is sent"
    assert ("access", target) in observed.Observer(environ=with_key).for_model(KEY), "Check again on its card asks it"
    asked = []

    def head(url, **kw):
        asked.append(url)
        return httpx.Response(200, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx, "head", head)
    monkeypatch.setattr("research_worker.api_usage.require_provider", lambda: None)
    got = observed.probe_access(target, with_key)
    assert got["state"] == "yes" and asked and "someone/gated" in asked[0], (got, asked)
    assert observed.probe_access(discover.update_target(KEY, "dark-spots 0.1"), with_key)["state"] == "unknown", \
        "an observation is of one candidate: not the one waiting, nothing is asked"
    import asyncio
    from types import SimpleNamespace

    from orchestrator import commands

    app = SimpleNamespace(hub=SimpleNamespace(broadcast=lambda wire: None), capabilities_wire=lambda: {})
    asyncio.run(commands.observe_confirm(app, None, {"model": KEY, "update": True}))  # your word, for its update
    assert observed.latest("access", target)["how"] == "you" and observed.latest("access", KEY) is None


def test_status_is_computed_once_per_wire(home, monkeypatch):
    """Item 8: inside one display scope, every surface reads the same computed status."""
    from orchestrator import env_observations

    m = turned_on(home, monkeypatch)
    calls = []
    real = status._of
    monkeypatch.setattr(status, "_of", lambda *a, **k: calls.append(1) or real(*a, **k))
    with env_observations.display({}):
        first, second = status.of(m), status.of(m)
    assert first is second and len(calls) == 1
    status.of(m)
    assert len(calls) == 2, "outside a display scope it is read again"


def test_nothing_is_said_when_there_is_nothing_to_say(home, monkeypatch):
    """Item 9: a successful check doesn't make an entry "maintained": that would claim someone maintains it."""
    m = turned_on(home, monkeypatch)
    discover.check(m)
    assert status.of(catalog.get(KEY))["maintained"] is None


def test_use_the_newest_never_makes_a_revoked_update_active_nor_offers_it(home, monkeypatch):
    """#402 review round 2, item 1: a person's Install made 0.2 beside 0.1; 0.2 is then revoked."""
    from tests.test_env_runner import install

    turned_on(home, monkeypatch)
    fix(home, "0.2")
    channels.update()
    v1 = env_runner.selected(catalog.get(KEY)).folder
    assert install(home, monkeypatch)["state"] == "done" and active_version() == "dark-spots 0.1"
    notices(home, REVOKED.format(v="0.2", severity="warn"))
    channels.update()
    m = catalog.get(KEY)
    assert [v["version"] for v in env_runner.versions(m)] == ["dark-spots 0.1"], "0.2 isn't offered"
    waiting = env_runner.update_waiting(m)
    assert waiting.startswith("Update unavailable here: dark-spots 0.2 was revoked by its maintainers"), waiting
    assert "Use the newest" not in waiting
    with pytest.raises(env_runner.Revoked, match="was revoked by its maintainers"):
        env_runner.choose(m, None)
    assert env_runner.selected(catalog.get(KEY)).folder == v1, "0.1 stays in use"
    env_runner.choose(m, v1.name)  # choosing what runs, not revoked, still works
    env_runner.turn_off(m)  # round 3: turned off, nothing is in use, and Turn on again must not bring in 0.2
    with pytest.raises(env_runner.Revoked, match="was revoked by its maintainers"):
        env_runner.choose(m, None)
    env_runner.turn_on_again(m)
    assert env_runner.selected(catalog.get(KEY)).folder == v1 and active_version() == "dark-spots 0.1"
    import asyncio

    from orchestrator import commands
    from orchestrator.brain.registry import ToolFailed

    with pytest.raises(ToolFailed) as e:  # the card's Use the newest: nothing to install again
        asyncio.run(commands.model_version(None, None, {"model": KEY}))
    assert e.value.next == "keep the version in use, or choose another on its card"


def test_a_candidate_revoked_while_it_waits_for_review_offers_no_accept(home, monkeypatch):
    """#402 review round 2 (optional): the revocation is said before the waiting review, so Accept isn't offered."""
    m = turned_on(home, monkeypatch)
    policy.record(m, "auto", by="you", how="screen")
    publish(home, KEY, version("0.2").replace(LICENCE, 'licence = "Apache-2.0"'), ADAPTER.replace("< 128", "< 129"))
    assert prepare.run(KEY)["state"] == "needs-review"
    assert status.of(catalog.get(KEY))["next"] == "accept"
    notices(home, REVOKED.format(v="0.2", severity="warn"))
    channels.peek()  # the daily look brings the notice (metadata only); the attempt still waits for review
    now = status.of(catalog.get(KEY))
    assert now["state"] == "unavailable" and now["next"] is None and "revoked" in now["says"], now


def test_a_bundle_members_run_reads_its_entrys_notices(home):
    """#402 CI (AA-01's headless runs): a member's generation carries bundle_contract.CapabilityView, which has no
    channel of its own. A notice names the entry, so a member is judged by its owner's: a block refuses its runs, and
    a member of an entry with no notice runs."""
    from types import SimpleNamespace

    from gpu_worker.bundle_contract import CapabilityView

    owner = SimpleNamespace(id="image-kit", key="image-kit", channel="", namespace="", version="1", name="Image kit")
    member = SimpleNamespace(id="invert", name="Invert image")
    view = CapabilityView(owner, member)
    assert revocations.blocked(SimpleNamespace(m=view)) == "", "no notice: it runs"
    (catalog.ROOT / revocations.FILE).write_text('[[revoked]]\nentry = "image-kit"\nversion = "1"\nwhy = "inverts twice"\n'
                                                 'severity = "block"\n')
    assert revocations.revoked(view, "1")["why"] == "inverts twice"
    assert revocations.blocked(SimpleNamespace(m=view)).startswith("Invert image 1 was revoked by its maintainers")
