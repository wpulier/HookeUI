"""Requirements asked of the outside world (orchestrator/observed.py; P6 of the open catalog): what Hugging Face says
about a gated model's access and what a service says about a key are what the cards, readiness and the agent say,
with when; an approval shows by itself; unknown is never a refusal. A stand-in Hugging Face (httpx patched) and a
temporary catalog: never this Mac's keys or answers."""
import asyncio

import httpx
import pytest

from orchestrator import catalog_view, capabilities, observed, options, readiness
from tests.waiting import until


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))  # nothing downloaded: the licence matters
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return tmp_path


def hugging_face(monkeypatch, status: int, seen: list | None = None):
    def head(url, headers=None, **kw):
        if seen is not None:
            seen.append((url, bool((headers or {}).get("Authorization"))))
        return httpx.Response(status, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx, "head", head)


def steps(model: str, keys: dict) -> list[str]:
    return [s.says for s in readiness.model(model, keys).steps]


def test_an_approval_on_hugging_face_is_what_the_card_says(home, monkeypatch):
    # Will, 2026-10-12: DINOv3's licence accepted and approved, and the card still said "Accept its licence".
    monkeypatch.setenv("HF_TOKEN", "test-token")
    seen = []
    hugging_face(monkeypatch, 200, seen)
    obs = observed.probe_access("dinov3-small")
    assert obs["state"] == "yes" and obs["says"] == "Hugging Face: your account has access"
    assert seen and seen[0][1] and "/resolve/" in seen[0][0], "asked with the user's key, for a pinned file"
    observed.store().put("access", "dinov3-small", obs)
    assert "Accept its licence on Hugging Face" not in steps("dinov3-small", {"HF_TOKEN": True})
    assert steps("dinov3-small", {"HF_TOKEN": True}) == ["Download 86 MB"], "granted: its next step is the download"
    wire = capabilities.wire(options.Settings(), {"HF_TOKEN": True})
    row = next(r for r in catalog_view.rows(wire["roles"], {}, {"HF_TOKEN": True}) if r["id"] == "dinov3-small")
    licence = next(f for f in row["facts"] if f["kind"] == "licence")
    assert licence["ok"] is True and licence["says"].startswith("Hugging Face: your account has access (checked ")
    assert licence["observed"] == "access"


def test_not_granted_yet_says_so_with_when_and_never_without_asking(home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-token")
    hugging_face(monkeypatch, 403)
    observed.store().put("access", "dinov3-small", observed.probe_access("dinov3-small"))
    step = readiness.model("dinov3-small", {"HF_TOKEN": True}).next
    assert step.says == "Accept its licence on Hugging Face" and "hasn't been given access yet" in step.how and "(checked " in step.how


def test_never_asked_or_unreachable_is_not_confirmed_not_refused(home, monkeypatch):
    assert readiness.model("dinov3-small", {"HF_TOKEN": True}).steps[0].says == "Its licence: not confirmed yet"

    def offline(url, **kw):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "head", offline)
    obs = observed.probe_access("dinov3-small")
    assert obs["state"] == "unknown" and "couldn't reach Hugging Face" in obs["says"]


def test_in_the_example_project_an_unasked_licence_says_why_and_what_to_do(home, monkeypatch):
    """F-L8-4: the local-only Example project asks Hugging Face nothing (api_usage.require_provider), so a gated model's
    licence was never confirmed there; its step stayed "not confirmed yet · Needs you" ("asked in a moment") and its setup
    waited for ever with nothing said. Now the step, its card and the setup's card say why, and to open another project."""
    from orchestrator import capability_shelf
    from research_worker import api_usage

    monkeypatch.setenv("HF_TOKEN", "test-token")
    keys = {"HF_TOKEN": True}
    with api_usage.local_only(True):
        assert observed.asking_refused()
        step = readiness.model("dinov3-small", keys).steps[0]
        assert step.kind == "licence" and step.says == observed.NOT_ASKED_STEP
        assert "open another project" in step.says and "stays on this Mac" in step.how
        wire = capabilities.wire(options.Settings(), keys)
        row = next(r for r in catalog_view.rows(wire["roles"], {}, keys) if r["id"] == "dinov3-small")
        licence = next(f for f in row["facts"] if f["kind"] == "licence")
        assert licence["says"] == observed.NOT_ASKED_HERE and "in a moment" not in licence["says"]
        assert row["face"]["says"] == observed.NOT_ASKED_STEP
        role = capability_shelf.entry_role(row)
        card = capability_shelf.card(role, row["name"], "", "", [], [],
                                     turning={"state": "waiting", "option": row["id"], "says": step.says})
        assert card["word"].startswith("Waiting: its licence isn't asked in this example project")
    assert not observed.asking_refused()
    assert readiness.model("dinov3-small", keys).steps[0].says == "Its licence: not confirmed yet", "asked in other projects"


def test_without_a_key_it_says_it_cant_ask(home, monkeypatch):
    hugging_face(monkeypatch, 401)
    assert observed.probe_access("dinov3-small")["state"] == "no-key"


def test_a_key_its_service_refused_is_no_key(home, monkeypatch):
    from orchestrator import key_tests

    monkeypatch.setenv("GEMINI_API_KEY", "test-value")
    monkeypatch.setattr(key_tests, "check", lambda service, value, client=None: key_tests.Outcome("rejected"))
    observed.store().put("key", "GEMINI_API_KEY", observed.probe_key("GEMINI_API_KEY"))
    r = readiness.option("vision", "gemini-3.8-flash", keys={"GEMINI_API_KEY": True})  # a Gemini model, chosen by hand
    assert not r.available and r.next.says == "Replace your Google Gemini API key" and "refused it" in r.next.how
    monkeypatch.setattr(key_tests, "check", lambda service, value, client=None: key_tests.Outcome("unchecked", "timed out"))
    assert observed.probe_key("GEMINI_API_KEY")["state"] == "unknown", "a timeout is never a refusal"


def test_without_a_hugging_face_key_no_access_is_asked_on_its_own(home, monkeypatch):
    # The review of cd1feb87 (P2-a): a token-less HEAD every 5 minutes can only be told "can't say".
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert not [t for t in observed.targets() if t[0] == "access"]
    monkeypatch.setenv("HF_TOKEN", "test-token")
    assert ("access", "dinov3-small") in observed.targets()


def test_the_observer_asks_what_is_due_and_says_when_an_answer_changed(home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-token")
    status = [403]
    monkeypatch.setattr(httpx, "head", lambda url, **kw: httpx.Response(status[0], request=httpx.Request("HEAD", url)))
    from orchestrator import key_tests

    monkeypatch.setattr(key_tests, "check", lambda service, value, client=None: key_tests.Outcome("accepted"))
    changed = []
    o = observed.Observer(changed=lambda: changed.append(1))
    asked = asyncio.run(o.sweep())
    assert asked == len(observed.targets()) and changed, "everything asked at first; the answers are news"
    assert asyncio.run(o.sweep()) == 0, "nothing due a moment later"
    status[0] = 200  # approved on Hugging Face meanwhile
    changed.clear()
    asyncio.run(o.sweep(only=o.for_model("dinov3-small"), force=True))
    assert changed and observed.latest("access", "dinov3-small")["state"] == "yes"
    assert ("access", "dinov3-small") in o.for_model("dinov3-small")


def test_what_is_not_yet_granted_is_asked_again_soon_and_granted_rarely(home):
    s = observed.store()
    s.put("access", "x", {"state": "no", "says": "not yet"})
    assert not s.due("access", "x") and s.due("access", "x", now=s.get("access", "x")["t"] + observed.NOT_YET_S + 1)
    s.put("access", "y", {"state": "yes", "says": "granted"})
    assert not s.due("access", "y", now=s.get("access", "y")["t"] + observed.NOT_YET_S + 1)
    assert s.due("access", "y", now=s.get("access", "y")["t"] + observed.RECHECK_S + 1)


def test_a_download_records_what_it_ran_into(home, monkeypatch):
    from orchestrator import downloads

    queue = downloads.Downloads()
    monkeypatch.setenv("HF_TOKEN", "test-token")

    def refused(m, **kw):
        raise downloads.Failed("not granted", step="licence", url="https://huggingface.co/x")

    monkeypatch.setattr(downloads, "download", refused)
    queue.environ = dict(__import__("os").environ)
    queue.start("dinov3-small")
    until(lambda: queue.wire()[0]["state"] == "failed")
    assert observed.latest("access", "dinov3-small")["state"] == "no"


def test_answers_survive_a_restart(home):
    observed.store().put("access", "dinov3-small", {"state": "yes", "says": "Hugging Face: your account has access"})
    observed._stores.clear()  # a fresh process
    assert observed.latest("access", "dinov3-small")["state"] == "yes"


# --- the guarantees: nothing is a blocker because it is written down ---------------------------------------------------
LOCAL_FACTS = {"files", "code", "weights", "on", "check", "use", "runs", "edition", "entry"}  # use: a run here


def test_every_kind_of_requirement_has_a_source_of_truth():
    from gpu_worker import manifests as catalog

    assert set(catalog.NEED_KINDS) <= set(observed.SOURCES), "a requirement kind nothing reads or asks"
    for kind, sources in observed.SOURCES.items():
        for source in sources:
            assert source in ("local", "user") or (source.startswith("probe:") and source[6:] in observed.PROBES), (kind, source)
    for m in catalog.manifests().values():
        for need in (*m.needs_to_get, *m.needs_to_run):
            assert need.split(":", 1)[0] in observed.SOURCES, (m.id, need)


@pytest.mark.parametrize("keys", [{}, {"HF_TOKEN": True, "GEMINI_API_KEY": True}], ids=["no keys", "some keys"])
def test_no_card_shows_a_blocker_without_evidence(home, keys):
    """Every ✗ on every card is read on this Mac, or carries when the outside world said so: never a declaration."""
    wire = capabilities.wire(options.Settings(), keys)
    for row in catalog_view.rows(wire["roles"], {}, keys):
        for f in row["facts"]:
            if f["ok"] is False and f["kind"] not in LOCAL_FACTS:
                assert "(checked " in f["says"] or (f["kind"] == "key" and "isn't added yet" in f["says"]), (row["id"], f)
        for s in readiness.model(row["id"], keys).steps:
            if s.kind == "licence":
                assert "(checked " in s.how or s.says in ("Its licence: not confirmed yet", "Accept its licence"), (row["id"], s)


def test_a_real_download_overrides_what_was_asked(home, monkeypatch, tmp_path):
    import time as _time

    from orchestrator import downloads

    observed.record("access", "dinov3-small", "no", "not granted", "asked")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setattr(downloads, "download", lambda m, **kw: tmp_path / "rev")
    queue = downloads.Downloads()
    queue.environ = dict(__import__("os").environ)
    queue.start("dinov3-small")
    until(lambda: queue.wire()[0]["state"] == "done")
    assert observed.latest("access", "dinov3-small")["state"] == "yes", "it downloaded: the account has access"


def test_an_approval_relays_through_the_app_by_itself(home, monkeypatch):
    """Not granted, then granted on Hugging Face: the app's own wire (the Models page, the chip, the agent) says so on the
    observer's next sweep, with nobody pressing anything."""
    from tests.standin_workers import build_app

    monkeypatch.setenv("HF_TOKEN", "test-token")
    status = [403]
    monkeypatch.setattr(httpx, "head", lambda url, **kw: httpx.Response(status[0], request=httpx.Request("HEAD", url)))
    from orchestrator import key_tests

    monkeypatch.setattr(key_tests, "check", lambda service, value, client=None: key_tests.Outcome("accepted"))
    app = build_app(home / "app")

    def licence() -> dict:
        row = next(r for r in app.capabilities_wire()["models"] if r["id"] == "dinov3-small")
        return next(f for f in row["facts"] if f["kind"] == "licence")

    asyncio.run(app.observer.sweep())
    assert licence()["ok"] is False
    status[0] = 200  # approved meanwhile
    monkeypatch.setattr(observed, "NOT_YET_S", 0.0)  # its time to be asked again has come
    asyncio.run(app.observer.sweep())
    assert licence()["ok"] is True and licence()["says"].startswith("Hugging Face: your account has access (checked ")


def test_a_licence_no_probe_can_read_is_the_users_word(home, monkeypatch):
    from gpu_worker import manifests as catalog

    folder = catalog.mine_root() / "elsewhere"
    folder.mkdir(parents=True)
    (folder / "model.toml").write_text('''id = "elsewhere"
name = "Elsewhere"
runs = "this-mac"
version = "v1"
licence = ["cellpose-sam-yours"]
kind = "model"
what = "A model whose licence is on its own page"
runner = "cellpose-file"
source = { url = "https://example.org/model" }
files = [{ path = "w.pt", bytes = 5, sha256 = "''' + "b" * 64 + '''", url = "https://example.org/w.pt" }]
needs_to_get = ["licence:https://example.org/licence", "download"]
workers = ["analysis"]
host = "own"
documented_mb = 1
load_limit_s = 1
''')
    assert ("access", "mine/elsewhere") not in observed.targets(), "never asked of a page no probe reads"
    assert readiness.model("mine/elsewhere", {}).steps[0].says == "Accept its licence"
    observed.confirm("access", "mine/elsewhere")
    assert [s.kind for s in readiness.model("mine/elsewhere", {}).steps] == ["download"]
    wire = capabilities.wire(options.Settings(), {})
    row = next(r for r in catalog_view.rows(wire["roles"], {}, {}) if r["id"] == "mine/elsewhere")
    fact = next(f for f in row["facts"] if f["kind"] == "licence")
    assert fact["ok"] is True and fact["says"].startswith("You said you accepted it (checked ")
