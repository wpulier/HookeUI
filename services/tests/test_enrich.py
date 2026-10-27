import asyncio
import time
from types import SimpleNamespace

from orchestrator.enrich import Enricher
from orchestrator.labels import label_key
from orchestrator.research import ResearchManager
from orchestrator.scene import Scene, SceneObject
from tests.fakes import Hub as BaseHub


def test_label_keys_keep_different_things_apart():
    # an upper and a lower epidermis are different tissues; "(dark region)" is not a name
    assert label_key("upper epidermis") != label_key("lower epidermis")
    assert label_key("small intestine") != label_key("large intestine")
    assert label_key("Upper Epidermis (likely)") == "upper epidermis"
    assert label_key("Xylem (dark region)") == "xylem" and label_key("Cortex (dark region)") == "cortex"
    assert label_key("small stoma") == "stoma"


def test_label_keys_merge_the_same_thing():
    assert label_key("Stomata (likely)") == "stoma"
    assert label_key("oval structure (stoma)") == "stoma"
    assert label_key("central stoma") == "stoma"
    assert label_key("the nuclei") == "nucleus"
    assert label_key("Guard cells") == "guard cell"
    assert label_key("air bubbles") == "air bubble"
    assert label_key("cell (probable)") == "cell"
    assert label_key("chloroplasts?") == "chloroplast"


def Hub():
    """A hub for descriptions (tests/fakes.Hub)."""
    hub = BaseHub(specimen="leaf epidermis", research="fake", image="")
    hub.latest_info.wall_at = time.time()
    hub.infos = {}  # current view facts, no saved camera pixels in this synthetic fixture
    return hub


def outline(scene, oid, label):
    scene.add(SceneObject(id=oid, kind="highlight", polygons=[[[0, 0], [10, 0], [10, 10]]], anchor_frame=1,
                          anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label=label, status="likely"))


def test_one_description_per_label_asked_first(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    monkeypatch.setenv("MICROMIND_FAKE_RESEARCH_SECONDS", "0.3")

    async def go():
        hub = Hub()
        manager = ResearchManager(hub, root=tmp_path)
        sent = []
        enrich = Enricher(hub, manager, sent.append, root=tmp_path)
        for i in range(5):
            outline(hub.scene, f"o{i}", "stoma" if i else "Stomata (likely)")
        outline(hub.scene, "o9", "air bubble")
        enrich.mark_asked(["o9"])
        enrich.scan()
        assert set(enrich.queue) == {"stoma", "air bubble"}
        await enrich.pump()
        first = [s for s in manager.sessions.values()][0]
        end = time.monotonic() + 20
        while time.monotonic() < end and not all(e.get("status") == "ready" for e in enrich.entries.values()):
            await asyncio.sleep(0.05)
        manager.stop()
        return enrich, first, sent

    enrich, first, sent = asyncio.run(go())
    assert first.label == "air bubble"  # what the user asked about goes first
    assert {k: e["status"] for k, e in enrich.entries.items()} == {"stoma": "ready", "air bubble": "ready"}
    assert enrich.entries["stoma"]["description"]["summary"].startswith("Simulated research")
    assert all(m["type"] == "notes" for m in sent)
    assert (tmp_path / time.strftime("%Y-%m-%d") / "descriptions.json").exists()


def test_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")

    async def go():
        hub = Hub()
        manager = ResearchManager(hub, root=tmp_path)
        enrich = Enricher(hub, manager, lambda m: None, root=tmp_path)
        for i in range(9):
            outline(hub.scene, f"o{i}", f"thing number{i} x{i}")
        enrich.scan()
        await enrich.pump()
        started = len(manager.sessions)
        manager.stop()
        return started, len(enrich.queue)

    started, waiting = asyncio.run(go())
    assert started == 6 and waiting == 3


def test_label_keys_merge_common_synonyms_only():
    for label in ("phloem fiber cap", "Sclerenchyma fibre cap", "sclerenchyma cap", "fibre cap (likely)", "Fiber caps"):
        assert label_key(label) == "fiber cap", label
    for label in ("phloem fibres", "Sclerenchyma fibers", "sclerenchyma fiber"):
        assert label_key(label) == "sclerenchyma fiber", label
    for label in ("cell nucleus", "Plant cell nucleus", "nucleus", "nuclei (probable)"):
        assert label_key(label) == "nucleus", label
    assert label_key("Erythrocytes") == label_key("red blood cells") == "red blood cell"
    assert label_key("leukocyte") == label_key("leucocytes") == label_key("White blood cell") == "white blood cell"
    assert label_key("seta") == label_key("setae") == label_key("bristles") == "seta"
    assert label_key("socket") == label_key("bristle socket") == label_key("setal sockets") == "socket"
    assert label_key("central vacuole region") == label_key("Central vacuole (likely)") == "central vacuole"
    # Distinct things stay distinct.
    assert len({label_key(x) for x in ("fiber cap", "sclerenchyma fiber", "nucleus", "nucleolus", "vacuole", "central vacuole",
                                       "cell wall", "red blood cell", "white blood cell", "platelet", "seta", "socket",
                                       "nucleus & cell wall", "xylem vessel", "phloem")}) == 15
    assert label_key("central stoma") == "stoma"


def test_descriptions_are_redone_only_when_the_subject_changes(tmp_path):
    from orchestrator.labels import subject_key

    assert subject_key("blood smear, blurry") == subject_key("Blood smear, ~400x") == subject_key("blood smear (out of focus)") == "blood smear"
    assert subject_key("Onion epidermis at 40x-100x, unstained") == "onion epidermis"
    assert subject_key("stained cheek cells, close-up, high magnification") == "cheek cells"
    hub = Hub()
    manager = ResearchManager(hub, root=tmp_path)
    enrich = Enricher(hub, manager, lambda m: None, root=tmp_path)
    entry = {"status": "ready", "specimen": "blood smear, blurry"}
    for now, stale in (("blood smear, ~400x", False), ("Blood smear at 100x", False), ("onion epidermis", True)):
        hub.scene.context["specimen"] = now
        assert enrich._stale(entry) is stale, now


def test_descriptions_use_the_describe_effort(tmp_path, monkeypatch):
    from research_worker.budget import EFFORTS

    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    effort = EFFORTS["describe"]
    assert (effort.seconds, effort.cap_usd, effort.pool, effort.tools) == (25, 0.08, "instant", EFFORTS["instant"].tools)
    assert EFFORTS["instant"].seconds == 15

    async def go():
        hub = Hub()
        manager = ResearchManager(hub, root=tmp_path)
        enrich = Enricher(hub, manager, lambda m: None, root=tmp_path)
        outline(hub.scene, "o1", "guard cell")
        enrich.scan()
        await enrich.pump()
        manager.stop()
        return list(manager.sessions.values())

    sessions = asyncio.run(go())
    assert [s.effort for s in sessions] == ["describe"]


def test_dropped_or_other_epoch_outlines_do_not_start_automatic_lookup(tmp_path):
    async def go():
        hub = BaseHub(specimen="plant stem", research="fake", image="")
        manager = ResearchManager(hub, root=tmp_path)
        enrich = Enricher(hub, manager, lambda _: None, root=tmp_path)
        outline(hub.scene, "o1", "secondary xylem")
        hub.scene.update("o1", track="lost")
        outline(hub.scene, "o2", "cortex")
        hub.scene.objects["o2"].anchor_epoch = 9
        enrich.scan()
        assert not enrich.queue
        await enrich.pump()
        assert not manager.sessions
    asyncio.run(go())


def test_queued_description_is_not_rebound_after_source_or_label_change(tmp_path):
    async def go(change):
        hub = BaseHub(specimen="plant stem", research="fake", image="")
        manager = ResearchManager(hub, root=tmp_path / change)
        enrich = Enricher(hub, manager, lambda _: None, root=tmp_path / change)
        outline(hub.scene, "o1", "secondary xylem")
        enrich.scan()
        assert enrich.queue
        if change == "dropped":
            hub.scene.update("o1", track="lost")
        elif change == "epoch":
            hub.latest_info.epoch = 2
        elif change == "subject":
            hub.scene.context["specimen"] = "ciliated protist"
        else:
            hub.scene.update("o1", label="ciliate")
        await enrich.pump()
        assert not manager.sessions
    for change in ("dropped", "epoch", "subject", "label"):
        asyncio.run(go(change))


def test_description_completion_keeps_original_subject_and_level(tmp_path):
    hub = Hub()
    manager = ResearchManager(hub, root=tmp_path)
    enrich = Enricher(hub, manager, lambda _: None, root=tmp_path)
    enrich.entries["stoma"] = {"status": "pending", "level": "simple", "specimen": "leaf epidermis"}
    hub.scene.context["specimen"] = "ciliated protist"
    hub.settings.level = "expert"
    session = SimpleNamespace(purpose="description", label="stoma", result={"summary": "A pore in the leaf"},
                              status="done", rid="r1", specimen="leaf epidermis", spent=0.01)
    enrich._done(session)
    assert enrich.entries["stoma"]["specimen"] == "leaf epidermis"
    assert enrich.entries["stoma"]["level"] == "simple"


def test_stale_queue_discard_rescans_current_same_label_key(tmp_path):
    async def go():
        hub = Hub()
        manager = ResearchManager(hub, root=tmp_path)
        enrich = Enricher(hub, manager, lambda _: None, root=tmp_path)
        outline(hub.scene, "o1", "stoma")
        enrich.scan()
        old_revision = enrich.queue["stoma"]["revision"]
        hub.scene.update("o1", label="Stomata (likely)")
        enrich.scan()  # observes new scene version while the earlier key is queued
        await enrich.pump()
        assert not manager.sessions  # the stale queued work was refused
        enrich.scan()  # no additional scene mutation needed to admit the current revision
        assert enrich.queue["stoma"]["revision"] > old_revision
        assert enrich.queue["stoma"]["label"] == "Stomata (likely)"
    asyncio.run(go())
