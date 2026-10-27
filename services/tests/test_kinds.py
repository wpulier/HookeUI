"""New kinds of job without the app's code (orchestrator/kinds.py; C6): an entry with its own adapter declares an operation
of its own that takes the camera's view and gives a picture, a table, words or points. Proven by its declared output on a
real picture, it becomes an operation the runner knows (so Activity says what ran); taken away, the operation goes too.
What it gives is checked on the app's side as well as the worker's: anything not what it declares is refused."""
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, kinds, operations
from tests.test_env_runner import ENTRY, _picture, home, install  # noqa: F401 - a scratch catalog, a stand-in uv

IMPLEMENTS = 'operation = "cells"\nimpl = "dark-spots"\ninput = "image.2d.rgb8"\noutput = "labels.instance.2d"'

ADAPTERS = {
    "image.2d.rgb8": '''
import numpy as np

def segment(picture, params):
    return (255 - picture).astype(np.uint8)  # the view inverted: a picture of its own
''',
    "table.objects": '''
def segment(picture, params):
    return {"columns": [{"name": "row"}, {"name": "brightness", "unit": "0-255"}],
            "rows": [[i, float(picture[i].mean())] for i in range(3)]}
''',
    "text": '''
def segment(picture, params):
    return f"a {picture.shape[1]}x{picture.shape[0]} view"
''',
    "points.2d": '''
def segment(picture, params):
    return {"points": [[25, 25], [70, 30]]}
''',
    "tracks.graph": '''
def segment(frames, params):
    assert frames.ndim == 4 and len(params["times"]) == len(frames)
    return {"tracks": [{"points": [[i, 10.0 + 2 * i, 20.0] for i in range(len(frames))]}]}
''',
}


def kind_entry(home, output: str, adapter: str, operation: str = "spot-report", input_type: str | None = None):
    input_type = input_type or catalog.KIND_INPUT.get(output, "image.2d.rgb8")
    folder = home / "repo" / "models" / "dark-spots"
    folder.mkdir(exist_ok=True)
    (folder / "model.toml").write_text(ENTRY.format().replace(IMPLEMENTS, f'operation = "{operation}"\nimpl = "dark-spots"\n'
                                                                          f'input = "{input_type}"\noutput = "{output}"'))
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(adapter)
    catalog.manifests.cache_clear()
    return catalog.get("dark-spots")


@pytest.fixture
def clean_kinds():
    yield
    for op in list(kinds.registered()):
        operations.OPERATIONS.pop(op, None)
    kinds.registered().clear()


@pytest.mark.parametrize("output, made", [("image.2d.rgb8", "a picture 96x96"),
                                          ("table.objects", "a table of 3 rows (row, brightness)"),
                                          ("text", "12 characters of words"), ("points.2d", "2 points"),
                                          ("tracks.graph", "1 track")])
def test_a_new_kind_is_proven_by_its_output_and_becomes_an_operation_until_taken_away(home, monkeypatch, clean_kinds,
                                                                                      output, made):
    m = kind_entry(home, output, ADAPTERS[output])
    assert m is not None and kinds.is_kind(m), catalog.problems().get("dark-spots")
    assert "spot-report" not in operations.OPERATIONS, "nothing of it runs or is listed as runnable before it is proven"
    row = install(home, monkeypatch)
    assert row["state"] == "done", row
    p = env_runner.proof(m)
    assert p["ok"] and p["says"].startswith(f"made {made} from "), p
    assert env_runner.register()["added"] == ["dark-spots"]
    op = operations.OPERATIONS["spot-report"]
    assert [i.id for i in op.implementations] == ["dark-spots"] and op.selected_by is None
    assert "dark-spots" in [k["key"] for k in kinds.available()]
    given = _picture() if output != "tracks.graph" else np.stack([_picture()] * 3)
    out = env_runner.client(env_runner.in_use(m)).run(given, {"times": [0, 0.5, 1.0]}, output=output)
    assert kinds.made_words(output, out) == made
    if output == "tracks.graph":
        assert operations.OPERATIONS["spot-report"].implementations[0].accepts == ("clip",), "it takes a clip"
    envs.remove(m)
    assert env_runner.register()["removed"] == ["dark-spots"] and "spot-report" not in operations.OPERATIONS
    assert "dark-spots" not in [k["key"] for k in kinds.available()]


@pytest.mark.parametrize("output, adapter, why", [
    ("image.2d.rgb8", "import numpy as np\ndef segment(picture, params):\n    return np.zeros((96, 96), np.float32)\n",
     "8-bit RGB"),
    ("points.2d", "def segment(picture, params):\n    return {'points': [[500, 5]]}\n", "outside the picture"),
    ("table.objects", "def segment(picture, params):\n    return {'columns': ['a'], 'rows': [[1, 2]]}\n", "one cell per column"),
    ("text", "def segment(picture, params):\n    return 42\n", "words are a string"),
])
def test_what_isnt_its_declared_output_never_becomes_a_choice(home, monkeypatch, clean_kinds, output, adapter, why):
    m = kind_entry(home, output, adapter)
    install(home, monkeypatch)
    p = env_runner.proof(m)
    assert not p["ok"] and why in p["why"], p
    env_runner.register()
    assert "spot-report" not in operations.OPERATIONS and "dark-spots" not in [k["key"] for k in kinds.available()]


def test_the_app_checks_the_reply_itself_not_only_the_worker():
    with pytest.raises(env_runner.Failed, match="not the a picture it declares|it gave"):
        env_runner.kind_output("image.2d.rgb8", {"kind": "text"}, b"x", (8, 8, 3))
    with pytest.raises(env_runner.Failed, match="not the 8-bit RGB picture"):
        env_runner.kind_output("image.2d.rgb8", {"kind": "picture", "shape": [4, 4, 3]}, b"\0" * 10, (8, 8, 3))
    with pytest.raises(env_runner.Failed, match="not points on the picture"):
        env_runner.kind_output("points.2d", {"kind": "points"}, b"[[9, 1]]", (8, 8, 3))
    got = env_runner.kind_output("image.2d.rgb8", {"kind": "picture", "shape": [2, 2, 3]}, bytes(range(12)), (8, 8, 3))
    assert got["shape"] == [2, 2, 3] and got["picture"].dtype == np.uint8


def test_a_new_kind_says_what_it_takes_and_names_its_job_plainly(home):
    for operation, input_type, problem in (("spot-report", "image.2d.gray16", "takes image.2d.rgb8"),
                                           ("Spot Report!", "image.2d.rgb8", "short lowercase word")):
        kind_entry(home, "text", ADAPTERS["text"], operation=operation, input_type=input_type)
        assert any(problem in p for p in catalog.problems().get("dark-spots", [])), catalog.problems()
    assert kind_entry(home, "text", ADAPTERS["text"], operation="cells") is not None
    assert not kinds.is_kind(catalog.get("dark-spots")), "an operation of the app's own is never a new kind"


def test_a_reply_past_its_limit_is_refused_before_it_is_read():
    """The worker runs code nobody reviewed: the app reads its reply only within the declared output's limit."""
    import io
    import json
    import struct

    from env_worker.server import read_message

    header = json.dumps({"ok": True, "kind": "picture", "shape": [4096, 4096, 3]}).encode()

    class Endless(io.BytesIO):
        taken = 0

        def read(self, n=-1):
            got = super().read(n)
            Endless.taken += len(got)
            return got

    claim = struct.pack(">I", len(header)) + header + struct.pack(">I", 2 ** 31)  # 2 GB announced, none sent
    with pytest.raises(ValueError, match="past the"):
        read_message(Endless(claim), max_header=1 << 20,
                     max_payload=env_runner.reply_limit("image.2d.rgb8", (96, 96, 3)))
    assert Endless.taken == len(claim), "nothing of the payload was read"
    with pytest.raises(ValueError, match="header"):
        read_message(io.BytesIO(struct.pack(">I", 2 << 20) + b"{}"), max_header=1 << 20)


@pytest.mark.parametrize("points", [[[0.5, 1, 1], [1, 2, 2]], [[True, 1, 1], [1, 2, 2]], [[0, float("nan"), 1], [1, 2, 2]],
                                    [[0, 1], [1, 2, 2]], [{"f": 0}, [1, 2, 2]], [[0, "1", 1], [1, 2, 2]]])
def test_the_parent_refuses_tracks_whose_frame_is_not_an_integer_or_whose_place_is_not_two_finite_numbers(points):
    """Codex's diagnosis of 621d0721: 0.5 as a frame reached motiontracks and raised TypeError there."""
    import json

    data = json.dumps([{"points": points}], allow_nan=True).encode()
    with pytest.raises(env_runner.Failed, match="not tracks"):
        env_runner.kind_output("tracks.graph", {"kind": "tracks"}, data, (3, 8, 8, 3))
    good = env_runner.kind_output("tracks.graph", {"kind": "tracks"}, b'[{"points": [[0, 1, 1], [2, 2.5, 3]]}]', (3, 8, 8, 3))
    assert good == {"tracks": [{"points": [[0, 1.0, 1.0], [2, 2.5, 3.0]]}]}
    with pytest.raises(env_runner.Failed, match="not points"):
        env_runner.kind_output("points.2d", {"kind": "points"}, b'[[true, 1]]', (8, 8, 3))


def test_a_pictures_bytes_cross_the_pipe_as_they_are():
    import io

    from env_worker.server import read_message, write_message

    picture = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    pipe = io.BytesIO()
    write_message(pipe, {"op": "run"}, memoryview(picture))
    pipe.seek(0)
    header, data = read_message(pipe)
    assert header == {"op": "run"} and data == picture.tobytes()


def test_a_clips_scale_is_its_own_interval_and_never_one_set_after_it_began():
    from types import SimpleNamespace

    from orchestrator.motiontracks import clip_scale

    reading = SimpleNamespace(um_per_px=0.5)

    def hub(set_at):
        scale = SimpleNamespace(reading=lambda radius: reading, facts=lambda: {"objective": {"at": set_at}})
        return SimpleNamespace(scale=scale)

    clip = [(None, SimpleNamespace(epoch=1, wall_at=100.0 + i, fov=SimpleNamespace(radius=300))) for i in range(4)]
    assert clip_scale(hub(50.0), clip) == (reading, "")
    assert clip_scale(hub(101.5), clip)[0] is None, "set while its frames were taken: pixels"
    two_views = clip[:2] + [(None, SimpleNamespace(epoch=2, wall_at=103.0, fov=SimpleNamespace(radius=300)))]
    assert clip_scale(hub(50.0), two_views)[0] is None


def test_an_installed_kind_this_mac_cannot_run_is_not_offered_to_run(home, monkeypatch, clean_kinds):
    """L7b (ticket 62 row 13): a contributed capability's row (Actions, Try, the agent) follows the one resolver as its
    card does. Installed and proven is not enough when this Mac, the edition, a key or data it reads says otherwise."""
    from orchestrator import capabilities, readiness

    m = kind_entry(home, "text", ADAPTERS["text"])
    assert install(home, monkeypatch)["state"] == "done"
    env_runner.register()
    row = next(k for k in kinds.available() if k["key"] == "dark-spots")
    assert row["runnable"] and not row.get("said")
    real = readiness.misfit
    monkeypatch.setattr(readiness, "misfit", lambda e: "it needs 512 GB of memory (this Mac has 64 GB)"
                        if e.key == "dark-spots" else real(e))
    row = next(k for k in kinds.available() if k["key"] == "dark-spots")
    assert not row["runnable"] and "512 GB" in row["why"]
    assert row["said"] == "Unavailable here: Not on this Mac (it needs 512 GB of memory (this Mac has 64 GB))"
    listed = next(c for c in capabilities.contributed(None) if c["id"] == "catalog:dark-spots")
    assert listed["available"] is False and listed["said"].startswith("Unavailable here: Not on this Mac")


def test_the_catalogue_read_on_the_loop_survives_a_thread_registering_a_new_kind(home, monkeypatch, clean_kinds):
    """A capability run registers what is proven in a thread of its own (capability_runs._read_capability), which adds a
    new kind's operation, while the loop builds the catalogue, whose option_not_installed reads every operation: the
    operations being read are swapped, never changed in place (review of #413, 2026-10-21: "dictionary changed size
    during iteration"). The writer runs, whole, in the middle of the iteration: no sleep, no chance."""
    import sys
    import threading

    from orchestrator import options

    m = kind_entry(home, "text", ADAPTERS["text"])
    assert install(home, monkeypatch)["state"] == "done" and env_runner.proof(m)["ok"]
    assert "spot-report" not in operations.OPERATIONS
    ran = []

    class Hook:
        """An operation whose `selected_by` option_not_installed reads while it iterates the operations."""
        id, title, returns, implementations, fallbacks, output, empty_is_result = "test-hook", "", "", (), (), (), False

        def impl(self, impl_id):
            return None

        def selectable(self):
            return []

        @property
        def selected_by(self):
            if not ran and sys._getframe(1).f_code.co_name == "option_not_installed":
                ran.append(threading.Thread(target=kinds.register))  # capability_runs' thread
                ran[0].start()
                ran[0].join()
            return None

    monkeypatch.setitem(operations.OPERATIONS, "test-hook", Hook())
    options.catalogue({})
    assert ran, "the writer ran inside the iteration"
    assert "spot-report" in operations.OPERATIONS, "the next read sees the new kind"
