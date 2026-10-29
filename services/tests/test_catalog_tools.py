"""The agent relays the Models page (brain/toolbox/catalog.py; P4 of the open catalog): it says what the user's choices
need in plain words, offers a download and starts it only on the user's yes, opens a model's card once the studio says
it shows it, and never takes, asks for or repeats a key. Over the real rows (catalog_view.py) in an isolated profile."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import capabilities, catalog_view, options
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox import catalog as tools_catalog
from orchestrator.brain.toolbox import catalogue

KEY_FACTS = {
    "GEMINI_API_KEY": {"service": "gemini", "name": "Google Gemini API", "short": "Google", "present": True,
                       "sourceSays": "from your keys file", "getKey": "https://aistudio.google.com/apikey",
                       "enables": "Seeing, naming and pointing at things, the running look, quick decisions and web lookups"},
    "OPENAI_API_KEY": {"service": "openai", "name": "OpenAI API", "short": "OpenAI", "present": False, "sourceSays": "",
                       "getKey": "https://platform.openai.com/api-keys", "enables": "Talking by voice, research, and the pictures researchers make"},
    "HF_TOKEN": {"service": "huggingface", "name": "Hugging Face", "short": "Hugging Face", "present": False, "sourceSays": "",
                 "getKey": "https://huggingface.co/settings/tokens", "enables": "Downloading the models whose makers ask you to accept a licence first"},
}


class Downloads:
    def __init__(self):
        self.started = []

    def start(self, model, by="you"):
        self.started.append((model, by))
        return {"model": model, "state": "queued", "total": 156_000_000}


def services(keys: dict, opened=None, answer=None):
    wire = capabilities.wire(options.Settings(), keys)
    wire["setup"]["keys"] = {k: {**v, "present": bool(keys.get(k))} for k, v in KEY_FACTS.items()}
    wire["models"] = catalog_view.rows(wire["roles"], wire["setup"]["keys"], keys, [])

    async def open_page(client, page):
        opened.append(page)
        return answer

    return SimpleNamespace(hub=SimpleNamespace(), agent=None, capabilities=lambda: wire, downloads=Downloads(),
                           open_page=open_page if opened is not None else None)


def registry(svc) -> Registry:
    r = Registry()
    tools_catalog.add_catalog_tools(r, svc)
    return r


def ctx(words="what do I need to set up?"):
    return ToolContext("chat", 1, None, words)


@pytest.fixture
def nothing_downloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))


def test_what_do_i_need_says_the_users_choices_and_keys_in_plain_words(nothing_downloaded):
    svc = services({"GEMINI_API_KEY": True})
    reg = registry(svc)
    said = asyncio.run(reg.tools["models"].handler(ctx(), {}))
    assert said.startswith("Needs the user (their current choices use these and they can't run yet):")
    assert "Talking: GPT-Live needs your OpenAI API key." in said, said
    assert "Outlining: EdgeTAM download 56 MB" in said, "a download is a step of its own"
    assert "Keys added: Google." in said and "OpenAI (Talking by voice" in said
    assert "Offered (p" in said and "get_model with model 'EdgeTAM'" in said, "each download the choices wait on is offered"
    assert "Setup and checks" in said
    available = [row for row in svc.capabilities()["models"] if row["group"] == "available"]
    for row in available[:12]:
        assert catalog_view.named(row) in said
    if len(available) > 12:
        assert f"and {len(available) - 12} more on the Models page" in said
    # The bounded overview may omit DINO as the catalog grows; named discovery
    # must still describe its actual readiness, regardless of its position.
    detail = asyncio.run(reg.tools["models"].handler(ctx("What does DINOv3 need?"), {"about": "DINOv3 small"}))
    dino = next(row for row in svc.capabilities()["models"] if row["name"] == "DINOv3 small")
    assert "DINOv3 small" in detail and catalog_view.state_words(dino) in detail


def test_all_set_is_said_only_when_every_choice_can_run(monkeypatch):
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")
    said = asyncio.run(registry(services({"GEMINI_API_KEY": True, "OPENAI_API_KEY": True})).tools["models"].handler(ctx(), {}))
    assert said.startswith("All set: every model the user's current choices use can run here.")


def test_why_cant_you_use_sam31_offers_the_download_then_a_yes_starts_it(nothing_downloaded, monkeypatch):
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")  # every model here but the one asked about
    svc = services({"GEMINI_API_KEY": True})
    svc.capabilities()["models"] = [{**r, **({"status": "needs-setup", "word": "Download 156 MB", "group": "available",
                                               "next": {"kind": "download", "says": "Download 156 MB", "model": "sam2.1-tiny"}}
                                              if r["id"] == "sam2.1-tiny" else {})} for r in svc.capabilities()["models"]]
    reg = registry(svc)
    said = asyncio.run(reg.tools["models"].handler(ctx("why can't you use SAM 2.1?"), {"about": "SAM 2.1"}))
    assert "SAM 2.1 tiny (on this Mac): Download 156 MB." in said
    pid = said.split("Offered (")[1].split(")")[0]
    with pytest.raises(ToolFailed, match="user's own yes"):  # the model's own words never count as a yes
        asyncio.run(reg.tools["get_model"].handler(ctx("why can't you use SAM 2.1?"), {"confirms": pid, "asked_with": "yes please"}))
    assert svc.downloads.started == []
    started = asyncio.run(reg.tools["get_model"].handler(ctx("yes please"), {"confirms": pid, "asked_with": "yes please"}))
    assert svc.downloads.started == [("sam2.1-tiny", "you, by asking")]
    assert started.startswith("started downloading SAM 2.1 tiny (156 MB)") and "never that it is done" in started
    with pytest.raises(ToolFailed, match="not offered just now"):  # one yes, one download
        asyncio.run(reg.tools["get_model"].handler(ctx("yes please"), {"confirms": pid, "asked_with": "yes please"}))


def test_the_yes_names_what_was_offered_or_all_of_it(nothing_downloaded):
    # The next turn sees the conversation, not the tool's result: the user's yes is matched by the model's name, or all.
    svc = services({"GEMINI_API_KEY": True, "OPENAI_API_KEY": True})
    reg = registry(svc)
    asyncio.run(reg.tools["models"].handler(ctx(), {}))  # offers what the choices wait on
    started = asyncio.run(reg.tools["get_model"].handler(ctx("yes, get EdgeTAM"), {"model": "EdgeTAM", "asked_with": "yes, get EdgeTAM"}))
    assert svc.downloads.started == [("edgetam", "you, by asking")] and started.startswith("started downloading EdgeTAM")
    asyncio.run(reg.tools["get_model"].handler(ctx("yes please, download them"), {"model": "all", "asked_with": "yes please, download them"}))
    assert {m for m, _ in svc.downloads.started} == {"edgetam", "cellpose-sam", "dinov2-small"}, svc.downloads.started
    with pytest.raises(ToolFailed, match="not offered just now"):
        asyncio.run(reg.tools["get_model"].handler(ctx("yes"), {"model": "Molmo 2 4B", "asked_with": "yes"}))


def test_asking_for_a_download_by_name_is_the_yes(nothing_downloaded):
    """Live 2026-10-12: "please download DINOv3 again" was answered "Would you like me to download it now?". Their own
    words naming it are the yes; still quoted and checked, and only a download its card offers."""
    svc = services({"GEMINI_API_KEY": True, "OPENAI_API_KEY": True})
    reg = registry(svc)
    words = "please download Molmo 2 4B"
    started = asyncio.run(reg.tools["get_model"].handler(ctx(words), {"model": "Molmo 2 4B", "asked_with": words}))
    assert svc.downloads.started == [("molmo2-4b", "you, by asking")] and started.startswith("started downloading Molmo")
    # Live 2026-10-12 ("please download DINOv3 again"): the model passed the id, the user said the family's word.
    words = "please download SAM 2.1 again"
    asyncio.run(reg.tools["get_model"].handler(ctx(words), {"model": "sam2.1-tiny", "asked_with": words}))
    assert svc.downloads.started[-1] == ("sam2.1-tiny", "you, by asking")
    with pytest.raises(ToolFailed, match="own yes"):  # words the user never said
        asyncio.run(reg.tools["get_model"].handler(ctx("what is that?"), {"model": "EdgeTAM", "asked_with": "download EdgeTAM"}))


def test_an_offer_expires(nothing_downloaded, monkeypatch):
    svc = services({})
    reg = registry(svc)
    said = asyncio.run(reg.tools["models"].handler(ctx(), {"about": "EdgeTAM"}))
    pid = said.split("Offered (")[1].split(")")[0]
    later = tools_catalog.time.time() + tools_catalog.PROPOSAL_S + 1
    monkeypatch.setattr(tools_catalog.time, "time", lambda: later)
    with pytest.raises(ToolFailed, match="not offered just now"):
        asyncio.run(reg.tools["get_model"].handler(ctx("yes"), {"confirms": pid, "asked_with": "yes"}))


def test_a_key_is_the_users_to_add_and_what_it_turns_on_is_the_catalogs(nothing_downloaded):
    reg = registry(services({"GEMINI_API_KEY": True}))
    said = asyncio.run(reg.tools["models"].handler(ctx("what does my Google key turn on?"), {"about": "my Google key"}))
    assert said.startswith("The Google key: added (from your keys file). It turns on: Seeing, naming")
    assert "Gemini 3.8 Flash" in said and "It turns on only what it lists" in said
    assert "Never ask them to say or type a key" in said
    gated = asyncio.run(reg.tools["models"].handler(ctx(), {"about": "SAM 3.1 (this Mac)"}))
    assert "needs a key first" in gated and "open_models shows its card with Add key" in gated


def test_no_tool_takes_a_key():
    """No tool the agent has can be handed a key's value (a key goes only through the studio's Add key)."""
    words = ("key", "token", "secret", "password", "apikey", "api_key", "credential")
    for tool in catalogue().tools.values():
        for name in tool.params.get("properties", {}):
            assert not any(w == name.lower() or name.lower().endswith("_" + w) for w in words), (tool.name, name)


def test_open_models_is_done_once_the_studio_shows_it(nothing_downloaded):
    opened = []
    svc = services({}, opened, {"shown": True, "visible": True})
    reg = registry(svc)
    said = asyncio.run(reg.tools["open_models"].handler(ctx("add my OpenAI key"), {"model": "OpenAI key"}))
    card = opened[0].removeprefix("#models/")
    row = next(r for r in svc.capabilities()["models"] if r["id"] == card)
    assert row["group"] == "needs-you" and row["next"]["service"] == "openai", "a key named: a card that waits on it"
    assert said == f"the studio shows {row['name']}'s card on the Models page; its Add key is there; the OpenAI key is not added yet"
    # A key already added, which nothing waits on: the Toolbox's Keys band, said as added (live 2026-10-12: "add my fal key").
    opened.clear()
    svc = services({"GEMINI_API_KEY": True}, opened, {"shown": True, "visible": True})
    said = asyncio.run(registry(svc).tools["open_models"].handler(ctx("add my Google key"), {"model": "my Google key"}))
    assert opened == ["#models/keys"]
    assert said == "the studio shows the keys on the Models page; the Google key is already added (from your keys file), and Change is there"
    reg = registry(services({}, [], None))
    with pytest.raises(ToolFailed, match="did not say it shows"):
        asyncio.run(reg.tools["open_models"].handler(ctx(), {"model": "EdgeTAM"}))
    reg = registry(services({}, [], {"page": "#models/edgetam", "shown": True, "visible": False}))
    assert "when the user looks at the studio again" in asyncio.run(reg.tools["open_models"].handler(ctx(), {"model": "EdgeTAM"}))


def test_the_tools_are_in_the_list_the_brain_and_the_voice_are_given():
    names = set(catalogue().tools)
    assert {"models", "get_model", "open_models"} <= names
    assert catalogue().tools["models"].kind == "query" and catalogue().tools["get_model"].kind == "effect"


def test_the_real_app_gives_the_tools_its_download_queue_and_its_studio(tmp_path):
    """App.build wires the queue and the page opener the tools use (2026-10-12, live: the queue was wired before the app
    existed, and the user's yes failed with "something went wrong")."""
    from tests.standin_workers import build_app

    app = build_app(tmp_path)
    get_model = app.brain.registry.tools["get_model"].handler
    services = get_model.__closure__ and next(c.cell_contents for c in get_model.__closure__
                                              if hasattr(c.cell_contents, "downloads"))
    assert services.downloads.wire() == [] and services.downloads.rows == {}
    assert asyncio.run(services.open_page(None, "#models")) is None, "no studio connected: nothing says it shows it"


def test_an_install_is_offered_and_started_on_the_users_yes(nothing_downloaded, monkeypatch):
    """A model whose code is a Python of its own the app makes (envs.py): offered like a download, and Install starts on
    the user's own words."""
    from orchestrator import operations

    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")
    monkeypatch.setitem(operations.INSTALLED, "micro-sam", lambda: "not installed here (micro-sam's own Python)")

    class Installs:
        started = []

        def start(self, model, by="you"):
            self.started.append((model, by))
            return {"model": model, "state": "queued", "packages": 187}

    svc = services({"GEMINI_API_KEY": True, "OPENAI_API_KEY": True})
    svc.installs = Installs()
    reg = registry(svc)
    said = asyncio.run(reg.tools["models"].handler(ctx("can you use micro-sam?"), {"about": "micro-sam"}))
    assert "Install micro-sam" in said and "install micro-sam (light microscopy) (its own Python" in said
    words = "yes, install micro-sam"
    started = asyncio.run(reg.tools["get_model"].handler(ctx(words), {"model": "micro-sam (light microscopy)",
                                                                        "asked_with": words}))
    assert svc.installs.started == [("micro-sam-lm", "you, by asking")]
    assert started.startswith("started installing micro-sam (light microscopy)'s own Python (187 packages)")


@pytest.mark.parametrize("model, action", [("fiji", "Set up Fiji"),
                                          ("fiji-analyze-particles", "Prove it on Fiji")])
def test_the_agent_offers_the_fiji_cards_actual_setup_action(nothing_downloaded, monkeypatch, model, action):
    """Fiji's generation/proof action is not a Python environment install; relay the same step as its card."""
    import json

    from orchestrator import fiji_generations

    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")
    if model != "fiji":
        folder = fiji_generations.root() / "1"
        folder.mkdir(parents=True)
        (folder / "receipt.json").write_text(json.dumps({"proofs": {"smoke": "1.54p"}, "sites": [], "files": []}))
        (folder.parent / "current").write_text("1\n")

    class Installs:
        def __init__(self):
            self.started = []

        def start(self, model, by="you"):
            self.started.append((model, by))
            return {"model": model, "state": "queued", "packages": 0}

    svc = services({})
    svc.installs = Installs()
    reg = registry(svc)
    said = asyncio.run(reg.tools["models"].handler(ctx(), {"about": model}))
    assert action in said and "Offered (p" in said
    assert "Python" not in said and "lockfile" not in said
    pid = said.split("Offered (")[1].split(")")[0]
    started = asyncio.run(reg.tools["get_model"].handler(ctx("yes please"),
                                                       {"confirms": pid, "asked_with": "yes please"}))
    assert svc.installs.started == [(model, "you, by asking")]
    assert action in started and "never that it is done" in started
    assert "Python" not in started and "packages" not in started
