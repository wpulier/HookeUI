"""Made with (orchestrator/made_with.py; C5): a snapshot names, inside its own PNG, the catalog entries behind the outlines
it shows (never a blind comparison's unvoted model, never anything of the user's), and a picture opened elsewhere says
where each stands there, with its one press when it isn't on."""
from types import SimpleNamespace

import cv2
import numpy as np

from gpu_worker import manifests as catalog
from orchestrator import made_with, operations


def _png() -> bytes:
    picture = np.zeros((24, 32, 3), np.uint8)
    picture[4:12, 6:20] = (40, 200, 90)
    ok, png = cv2.imencode(".png", picture)
    assert ok
    return png.tobytes()


def test_a_picture_carries_what_made_it_and_its_pixels_are_unchanged(tmp_path):
    impl, key = next(iter(sorted(operations.IMPL_MODEL.items())))
    m = catalog.get(key)
    made = made_with.of_objects([SimpleNamespace(made_by={"implementation": impl}, trial=None)])
    assert made and made[0]["key"] == m.key and made[0]["name"] == m.name and made[0]["from"] == "reviewed"
    png = _png()
    path = tmp_path / "snapshot.png"
    path.write_bytes(made_with.embed(png, made))
    assert made_with.read(path) == made
    assert np.array_equal(cv2.imread(str(path)), cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR))
    plain = tmp_path / "plain.png"
    plain.write_bytes(made_with.embed(png, []))
    assert plain.read_bytes() == png and made_with.read(plain) == []


def test_a_blind_comparisons_model_and_an_algorithm_are_never_named():
    impl = next(iter(sorted(operations.IMPL_MODEL)))
    assert made_with.of_objects([SimpleNamespace(made_by={"implementation": impl}, trial="t1")]) == []
    assert made_with.of_objects([SimpleNamespace(made_by={"implementation": "spots"}, trial=None),
                                 SimpleNamespace(made_by=None, trial=None)]) == []


def test_a_picture_opened_here_says_where_each_stands(tmp_path):
    impl, key = next(iter(sorted(operations.IMPL_MODEL.items())))
    path = tmp_path / "shared.png"
    path.write_bytes(made_with.embed(_png(), [
        *made_with.of_objects([SimpleNamespace(made_by={"implementation": impl}, trial=None)]),
        {"key": "mine/diatom-counter", "name": "Diatom counter", "version": "v1", "from": "yours",
         "link": "https://bioimage.io/#/?id=diatom-counter"}]))
    offers = made_with.offers(made_with.read(path))
    assert [o["key"] for o in offers] == [catalog.get(key).key, "mine/diatom-counter"]
    assert offers[0]["state"] in ("on", "one-press", "not-here") and offers[0]["model"] == catalog.get(key).key
    assert offers[1]["state"] == "unknown" and "ask for its share file" in offers[1]["says"]
    assert "bioimage.io" in offers[1]["says"]


def test_what_isnt_a_made_with_picture_reads_as_nothing(tmp_path):
    for name, data in {"a.jpg": b"\xff\xd8\xff not a png", "b.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 8,
                       "c.png": _png()}.items():
        (tmp_path / name).write_bytes(data)
        assert made_with.read(tmp_path / name) == []
    assert made_with._valid({"format": 1, "capabilities": [{"key": 3}, "x", {"key": "k", "name": "n", "path": "/x"}]}) \
        == [{"key": "k", "name": "n"}], "only an entry's own facts are read back, never anything else it holds"


# --- bound to what really ran, on every kind of shared thing (Codex's cross-check at abf4afc7) -----------------------
def _runner(*records):
    return SimpleNamespace(runner=SimpleNamespace(get={r.id: r for r in records}.get))


def _execution(eid, executed, version="v-then", generation="", trial=None, revealed=False):
    return SimpleNamespace(id=eid, executed=executed, version=version, trial=trial, revealed=revealed,
                           producer={"version": version, **({"generation": generation} if generation else {})})


def test_what_made_it_is_what_its_execution_ran_never_todays_catalog_meaning():
    impl, key = next(iter(sorted(operations.IMPL_MODEL.items())))
    hub = _runner(_execution("x1", impl, version="the version that ran", generation="g1"),
                  _execution("x2", impl, trial="t1"))
    made = made_with.of_executions(hub, ["x1", "x2", "gone"])
    assert made == [{**made[0], "key": catalog.get(key).key, "version": "the version that ran", "generation": "g1"}]
    assert made_with.of_executions(hub, ["x2"]) == [], "an unvoted blind arm is never named"
    named = made_with.of_objects([SimpleNamespace(made_by={"execution": "x1", "implementation": "spots"}, trial=None)], hub)
    assert [m["version"] for m in named] == ["the version that ran"], "its execution, not its record's id alone"


def test_a_kept_jpeg_carries_what_made_it(tmp_path):
    ok, jpeg = cv2.imencode(".jpg", np.zeros((16, 16, 3), np.uint8))
    made = [{"key": "dinov2-small", "name": "DINOv2 small", "version": "v1", "from": "reviewed", "generation": "g9"}]
    path = tmp_path / "result.jpg"
    path.write_bytes(made_with.embed_jpeg(jpeg.tobytes(), made))
    assert made_with.read(path) == made and cv2.imread(str(path)) is not None
    assert made_with.of_asset(path) == made


def test_a_recording_or_a_saved_session_names_what_ran_while_it_was_made(tmp_path):
    import json

    impl, key = next(iter(sorted(operations.IMPL_MODEL.items())))
    folder = tmp_path / "dataset-1"
    (folder / "video").mkdir(parents=True)
    lines = [{"t": 1, "run": 1, "stream": "interactions", "record": {"kind": "execution", "id": "x1", "executed": impl,
                                                                      "version": "v-then", "producer": {}}},
             {"t": 2, "run": 1, "stream": "interactions", "record": {"kind": "execution", "id": "x2", "executed": impl,
                                                                      "trial": "t1"}},
             {"t": 3, "run": 1, "stream": "interactions", "record": {"kind": "click"}}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    made = made_with.of_dataset(folder)
    assert [m["key"] for m in made] == [catalog.get(key).key] and made[0]["version"] == "v-then"
    (folder / "dataset.json").write_text(json.dumps({"madeWith": made}))
    (folder / "video" / "part-1.mp4").write_bytes(b"\0")
    assert made_with.of_asset(folder / "video" / "part-1.mp4") == made, "its recording's preview offers them"


def test_retained_generations_survive_catalog_removal_and_keep_unknown_history_unknown(tmp_path):
    import json

    old = {"key": "mine/retired", "name": "Retired method", "version": "1", "from": "yours", "generation": "g1"}
    new = {**old, "version": "2", "generation": "g2"}
    records = [operations.Execution("cells", "retired", "raw", "user", 1, "explicit", id="x1", executed="retired",
                                    version="1", producer={"catalog": old, "generation": "g1"}),
               operations.Execution("cells", "retired", "raw", "user", 2, "explicit", id="x2", executed="retired",
                                    version="2", producer={"catalog": new, "generation": "g2"})]
    assert made_with.of_executions(_runner(*records), ["x1", "x2", "x1"]) == [old, new]
    # A session has only retained execution records after restart; no current catalog entry is needed.
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps({"record": {"kind": "execution", **x.wire()}}) for x in records))
    assert made_with.of_dataset(tmp_path) == [old, new]
    legacy = made_with.of_objects([SimpleNamespace(made_by={"implementation": "edgetam"}, trial=None)])
    assert "version" not in legacy[0], "an old record did not retain a version: today's installed version is not evidence"


def test_runner_retains_producer_for_restart_and_reveals_blind_history_only_after_vote(tmp_path):
    import json
    from tests.test_operations import hub, view, CLICK, run

    events = []
    runner = operations.Runner(hub(), log=events.append)
    result = run(runner.run("outline", view(), CLICK, choice="edgetam", origin="user-chosen", trial="t1"))
    record = result.execution
    assert record.producer["catalog"]["key"] == "edgetam"
    assert record.made_by()["producer"]["catalog"]["version"] == "yonigozlan/EdgeTAM-hf"
    assert not any("producer" in e or "version" in e for e in events if e["kind"] == "execution")
    target = tmp_path / "events.jsonl"
    target.write_text("\n".join(json.dumps({"record": e}) for e in events))
    assert made_with.of_dataset(tmp_path) == []
    runner.reveal(record.id)
    target.write_text("\n".join(json.dumps({"record": e}) for e in events))
    made = made_with.of_dataset(tmp_path)
    assert len(made) == 1 and made[0]["version"] == "yonigozlan/EdgeTAM-hf"
    # A reopened object does not need the old in-memory runner.
    restored = SimpleNamespace(made_by=record.made_by(), trial=None)
    assert made_with.of_objects([restored]) == made


def test_followed_by_uses_its_retained_producer_without_inventing_a_version():
    from tests.test_objtrack import make
    import asyncio

    follower, hub = make()
    obj = hub.scene.objects["o1"]
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    assert obj.followed_by["producer"]["catalog"]["version"] == "yonigozlan/EdgeTAM-hf"
    named = made_with.of_objects([obj])
    assert named[0]["key"] == "edgetam-video" and named[0]["version"] == "yonigozlan/EdgeTAM-hf"
    obj.followed_by = {"implementation": "edgetam-video", "version": "older", "producer": {"generation": "g0"}}
    assert made_with.of_objects([obj])[0]["version"] == "older"


def test_a_refused_follower_is_not_credited_as_having_run():
    import asyncio
    import pytest
    from tests.test_objtrack import make, Heavy

    class Refused(Heavy):
        async def call(self, *args, **kwargs):
            raise RuntimeError("worker refused before running")

    follower, hub = make()
    hub.gpu_heavy = Refused()
    with pytest.raises(RuntimeError, match="refused"):
        asyncio.run(follower.step())
    assert made_with.of_objects(hub.scene.objects.values()) == []


def test_a_recording_retains_the_follower_that_actually_answered(tmp_path):
    import asyncio
    import json
    from orchestrator.objtrack import ObjectFollower
    from tests.test_objtrack import make

    _, hub = make()
    records = []
    follower = ObjectFollower(hub, log=records.append)
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps({"record": r}) for r in records))
    made = made_with.of_dataset(tmp_path)
    assert made and made[0]["key"] == "edgetam-video"
    assert made[0]["version"] == "yonigozlan/EdgeTAM-hf"
