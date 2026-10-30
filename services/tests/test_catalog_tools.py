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
                       "enables": "The running look, quick decisions, the voice's close look and web lookups"},
    "ANTHROPIC_API_KEY": {"service": "anthropic", "name": "Anthropic API", "short": "Anthropic", "present": False, "sourceSays": "",
                          "getKey": "https://console.anthropic.com/settings/keys",
                          "enables": "Seeing, naming and pointing at things, the thinking behind the voice, and lessons written by Claude"},
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
    # The default stack (the live partner's): the voice is GPT Realtime 2.1 (OpenAI), the eyes Claude Opus 5.5 (Anthropic).
    assert "Talking: GPT Realtime 2.1 needs your OpenAI API key." in said, said
    assert "Seeing: Claude Opus 5.5 needs your Anthropic API key." in said, said
    assert "Outlining: EdgeTAM download 56 MB" in said, "a download is a step of its own"
    assert "Keys added: Google." in said and "OpenAI (Talking by voice" in said and "Anthropic (Seeing, naming" in said
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
    keys = {"GEMINI_API_KEY": True, "OPENAI_API_KEY": True}  # the default eyes and the voice's thinking are Claude's
    said = asyncio.run(registry(services(keys)).tools["models"].handler(ctx(), {}))
    assert said.startswith("Needs the user") and "Seeing: Claude Opus 5.5 needs your Anthropic API key." in said, said
    said = asyncio.run(registry(services({**keys, "ANTHROPIC_API_KEY": True})).tools["models"].handler(ctx(), {}))
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
    assert said.startswith("The Google key: added (from your keys file). It turns on: The running look, quick decisions, "
                           "the voice's close look")
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


# --- what Hooke has and could add for kinds of work (`models` does; Part 2.5) -------------------------------------------
# Live 2026-10-29 (journey at 9618e42): "What segmentation models do we have, and what others could we add?" reached
# `models` as the Outlining job alone, which lists only what is already a choice of it; the answer guessed the rest
# ("possibly other specialized tools like Cellpose-SAM or InstanSeg") and the judge failed it. The model names the kinds
# of work; the tool gives every catalog entry that does them, installed or not, with where each stands and what it needs.
def _row(id, name, does, status, *, kind="model", what="", face=None, steps=(), group="available", used=(), word=""):
    return {"id": id, "name": name, "runs": "this-mac", "where": "on this Mac", "what": what, "kind": kind, "does": list(does),
            "status": status, "word": word or (steps[0]["says"] if steps else "Ready"), "group": group,
            "next": steps[0] if steps else None, "steps": list(steps), "facts": [], "face": face or {},
            "usedFor": [{"role": r, "title": t, "current": c, "fixed": False, "options": [id]} for r, t, c in used]}


FIXTURE_ROWS = [
    _row("edgetam", "EdgeTAM", ["outline", "cells"], "ready", what="Outlines what you click or box", group="ready",
         face={"state": "on", "by": "use", "says": "Working here · last ran 14:02", "action": None},
         used=[("outline", "Outlining", True), ("cells", "Cells", False)]),
    _row("sam2.1-tiny", "SAM 2.1 tiny", ["outline"], "ready", what="Outlines what you click or box",
         face={"state": "unchecked", "says": "Ready to run; not checked on this Mac yet",
               "action": {"kind": "check", "says": "Check it here"}}, used=[("outline", "Outlining", False)]),
    _row("instanseg-brightfield", "InstanSeg (brightfield nuclei)", ["cells"], "needs-setup",
         what="Outlines every nucleus on a brightfield slide",
         steps=[{"kind": "download", "says": "Download 16 MB", "how": "Its Download button", "model": "instanseg-brightfield",
                 "bytes": 15792317},
                {"kind": "install", "says": "Install InstanSeg (brightfield nuclei)", "model": "instanseg-brightfield",
                 "how": "its own Python and 35 packages, each checked by its hash"}]),
    _row("sam3.1-local", "SAM 3.1 (this Mac)", ["outline"], "needs-setup", what="Outlines what you box",
         steps=[{"kind": "key", "says": "Add your Hugging Face key to download it", "how": "put HF_TOKEN=... in a file",
                 "service": "huggingface"},
                {"kind": "licence", "says": "Its licence: not confirmed yet", "url": "https://huggingface.co/facebook/sam3.1"},
                {"kind": "download", "says": "Download 3.5 GB", "model": "sam3.1-local", "bytes": 3502755717},
                {"kind": "install", "says": "Install SAM 3.1's code", "how": "not installed here (sam3)"}],
         used=[("outline", "Outlining", False)]),
    _row("cellpose-sam", "Cellpose-SAM", ["cells"], "unavailable", what="Outlines every cell in a view", group="unavailable",
         word="Not in this edition: its weights were trained on non-commercial data"),
    _row("dinov2-small", "DINOv2 small", ["looks"], "ready", group="ready",
         face={"state": "on", "says": "On · last check: works", "action": None}),
]
FIXTURE_NEWS = [
    {"key": "nucleus-finder", "name": "Nucleus finder (fixture)", "what": "Outlines nuclei on stained slides", "kind": "model",
     "does": ["cells"], "requirements": ["Runs on this Mac", "Download 5.0 MB", "Install and check in its own environment"],
     "badge": "reviewed", "fetched": False, "tags": []},
    {"key": "look-alike-fixture", "name": "Look-alike fixture", "what": "Compares how things look", "kind": "model",
     "does": ["looks"], "requirements": [], "badge": "reviewed", "fetched": False, "tags": []},
]


def fixture_services() -> SimpleNamespace:
    wire = {"models": [dict(r) for r in FIXTURE_ROWS],
            "roles": [{"id": "outline", "title": "Outlining"}, {"id": "cells", "title": "Cells"}],
            "setup": {"keys": {}, "news": {"new": FIXTURE_NEWS}}}
    return SimpleNamespace(hub=SimpleNamespace(), agent=None, capabilities=lambda: wire, downloads=Downloads(), open_page=None)


def _section(said: str, heading: str) -> list[str]:
    """The entries under one of the answer's headings, up to the next heading or the end of the catalog's facts."""
    body = said.split(heading + "\n", 1)[1].split("\n</catalog-facts>", 1)[0]
    lines = []
    for line in body.split("\n"):
        if not line.startswith("- "):
            break
        lines.append(line[2:])
    return lines


ANSWER_WINDOW = 1940  # what the round that answers from a query reads of its text (brain/claims.py answer_facts)


def test_what_could_we_add_lists_every_catalog_entry_for_the_work_with_where_it_stands_and_what_it_needs():
    reg = registry(fixture_services())
    words = "What segmentation models do we have, and what others could we add?"
    said = asyncio.run(reg.tools["models"].handler(ctx(words), {"does": ["outline", "cells"]}))
    assert said.startswith("Hooke's catalog for outline, cells: 5 entries, installed or not, and 1 new; nothing else in "
                           "it does this. On this Mac unless it says cloud."), said
    # Plainly split, in this order: working here, installed but not run yet, can be added (where, every step), can't run.
    order = [said.index(title + ":") for _, title in catalog_view.STANDING if title + ":" in said]
    assert order == sorted(order) and len(order) == 4, said
    assert said.index("Models:\n") < order[0], "every entry here is a model"
    assert _section(said, "Working here (ran or passed its check on this Mac):") == [
        "EdgeTAM (outline, cells; chosen for Outlining)"]
    assert _section(said, "Installed here, not run yet (nothing to get or install):") == ["SAM 2.1 tiny (outline)"]
    assert _section(said, "Can be added (where it runs; every step it needs):") == [
        "InstanSeg (brightfield nuclei) (cells): on this Mac; Download 16 MB; Install InstanSeg (brightfield nuclei) (its "
        "own Python and 35 packages)",
        "SAM 3.1 (this Mac) (outline): on this Mac; Add your Hugging Face key to download it; Its licence: not confirmed "
        "yet (https://huggingface.co/facebook/sam3.1); Download 3.5 GB; Install SAM 3.1's code (not installed here (sam3))"]
    assert _section(said, "Can't run on this Mac:") == [
        "Cellpose-SAM (cells): Not in this edition: its weights were trained on non-commercial data"]
    assert "HF_TOKEN" not in said, "a key's how stays on its card"
    # Nothing of other work in the list; the other kinds are named so a request meaning them too can ask.
    assert "DINOv2 small" not in said and "Look-alike fixture" not in said
    assert "Other kinds of work in the catalog (does): looks (1)." in said
    # What the catalog has that isn't on this Mac yet, with what it needs, and how one not in the catalog is added.
    assert _section(said, "New in the catalog, not on this Mac yet (New for you's Get it fetches it):") == [
        "Nucleus finder (fixture): Outlines nuclei on stained slides. Needs: Runs on this Mac; Download 5.0 MB; Install and "
        "check in its own environment"]
    assert ("Adding one not in the catalog (the user, on the Toolbox's Add a capability): from a link to a 2D BioImage "
            "Model Zoo model with TorchScript or ONNX weights, a Hugging Face transformers segmentation model or a Cellpose "
            "model file of the user's, each checked here first; for any other link, Have an AI build it drafts an adapter, "
            "used once the user reviews it.") in said
    # The facts the answer needs are inside what the answering round reads; what it may lose is the offers' last lines.
    assert said.index("Other kinds of work") < ANSWER_WINDOW, len(said)
    # Its next download is offered as any named model's is; a key is the user's.
    assert "get_model with its name (or 'all')" in said and "Never ask them to say or type a key" in said
    started = asyncio.run(reg.tools["get_model"].handler(ctx("yes, add InstanSeg"),
                                                         {"model": "InstanSeg (brightfield nuclei)", "asked_with": "yes, add InstanSeg"}))
    assert started.startswith("started downloading InstanSeg (brightfield nuclei)")


def test_a_job_named_lists_every_entry_for_its_work_and_says_the_other_kinds():
    """The journey's own call ("outlining"): every catalog entry that outlines, not only its choices already here, and the
    catalog's other kinds of work by name, so a request that meant cells too can ask for them."""
    reg = registry(fixture_services())
    said = asyncio.run(reg.tools["models"].handler(ctx("what could we add for outlining?"), {"about": "outlining"}))
    assert said.startswith("Hooke's catalog for outline: 3 entries, installed or not; nothing else in it does this."), said
    assert "EdgeTAM (chosen for Outlining)" in said and "- SAM 2.1 tiny\n" in said
    assert "SAM 3.1 (this Mac): on this Mac; Add your" in said
    assert "InstanSeg" not in said and "Nucleus finder" not in said
    assert "Other kinds of work in the catalog (does): cells (3); looks (1)." in said
    assert "no reviewed code here runs one from a link for this work" in said, "the templates make cells, not outlines"
    cells = asyncio.run(reg.tools["models"].handler(ctx("what could we add for cells?"), {"about": "cells"}))
    assert "- InstanSeg (brightfield nuclei): on this Mac; Download 16 MB" in cells and "Nucleus finder (fixture)" in cells
    assert "SAM 2.1 tiny" not in cells
    # The kinds as one string (a json-actions answer isn't held to the schema; the live run passed "cells,outline", and
    # taken as one unknown kind it was answered "Cellpose-SAM is not in the catalog").
    joined = asyncio.run(reg.tools["models"].handler(ctx(), {"does": "cells,outline"}))
    assert joined == asyncio.run(reg.tools["models"].handler(ctx(), {"does": ["cells", "outline"]}))
    assert "5 entries, installed or not" in joined
    # A kind the catalog has none of is said as that, with the kinds it has; never a guess.
    none = asyncio.run(reg.tools["models"].handler(ctx(), {"does": ["denoise"]}))
    assert none.startswith("no entry in Hooke's catalog does denoise. The kinds of work it has: outline = ")


def test_the_real_catalog_gives_every_entry_that_outlines_or_segments_installed_or_not(nothing_downloaded):
    """Over the real rows (an isolated profile, nothing downloaded): each entry whose manifest declares outline or cells
    is listed once with its steps, read from its manifest (catalog_view.operations_of), never by its name; and the whole
    list, with how to add one, fits in what the answering round reads."""
    from gpu_worker import manifests as catalog

    svc = services({"GEMINI_API_KEY": True})
    reg = registry(svc)
    rows = svc.capabilities()["models"]
    assert next(r for r in rows if r["id"] == "instanseg-brightfield")["does"] == ["cells"]
    expected = {k for k, m in catalog.manifests().items()
                if {"outline", "cells", "find-like"} & set(catalog_view.operations_of(m))}
    assert {"edgetam", "micro-sam-lm", "sam2.1-tiny", "sam3.1-local", "cellpose-sam", "instanseg-brightfield"} <= expected
    said = asyncio.run(reg.tools["models"].handler(ctx(), {"does": ["outline", "cells", "find-like"]}))
    listed = [line[2:] for line in said.split("\n") if line.startswith("- ")]
    for r in rows:
        mine = [line for line in listed if line.startswith((catalog_view._short_name(r) + " (", r["name"] + " ("))]
        assert len(mine) == (r["id"] in expected), (r["id"], said)
    assert ("- InstanSeg (brightfield nuclei) (cells): on this Mac; Download 16 MB; Install InstanSeg (brightfield nuclei) "
            "(its own Python") in said
    assert "- SAM 3.1 (this Mac) (outline): on this Mac; Add your Hugging Face key to download it;" in said
    assert "Download 3.5 GB" in said
    # Models apart from tools, by each manifest's kind: Fiji's Analyze Particles is a tool, never among the models.
    models, tools = said.split("Tools, not models", 1)
    assert "Analyze Particles (Fiji)" in tools and "Analyze Particles" not in models
    assert said.index("Other kinds of work") < ANSWER_WINDOW, len(said)
    tool = reg.tools["models"]
    assert {"outline", "cells", "find-like"} <= set(tool.params["properties"]["does"]["items"]["enum"])
    assert "cells = Segment every cell in the view" in tool.params["description"]


def test_capabilities_by_operation_says_what_the_catalog_has_that_isnt_set_up():
    """capabilities(operation=) lists the implementations registered here; an entry with its own adapter is one only once
    installed and proven, so the catalog's others are said beside them, with their next step."""
    said = catalog_view.elsewhere(FIXTURE_ROWS, "cells", {"edgetam", "cellpose-sam"})
    assert said == ("Also in Hooke's catalog, not set up here: InstanSeg (brightfield nuclei) (on this Mac): Download 16 MB. "
                    "models with does ['cells'] says every step each needs, and how one not in the catalog is added.")
    assert catalog_view.elsewhere(FIXTURE_ROWS, "outline", {"edgetam", "sam2.1-tiny", "sam3.1-local"}) == ""


def test_new_for_you_says_what_each_new_entry_does():
    from orchestrator import catalog_news

    assert catalog_news.does({"implements": [{"operation": "cells"}, {"operation": "outline"}]}) == ["cells", "outline"]
    assert catalog_news.does({"id": "spot-shape", "outputs": [{"type": "table.objects"}]}) == ["spot-shape"]
    assert catalog_news.does({"id": "x", "operation": "cells", "outputs": [{"type": "labels.instance.2d"}]}) == ["cells"]
    assert catalog_news.does({"members": [{"id": "a", "operation": "cells"}, {"id": "b"}]}) == ["cells", "b"]
    assert catalog_news.does({"id": "notes"}) == []


# --- will-workspace "models", measure-c4acc90 (all runs 2/3) -----------------------------------------------------------
# "What segmentation models do we have, and what others could we add?": the judge read "blurred runnable versus available
# models", "incorrectly groups Fiji Analyze Particles with models rather than identifying it as a tool" and "omits
# download sizes, key or setup requirements" for additions. The tool's facts listed models and tools in one list by where
# each stood (the tool marked only by a tag), never where an addition would run, and a download whose manifest gives no
# size could only be said as readiness words it ("Download 1 MB"). The rows below are the run's facts.
def _workspace_rows():
    key = {"kind": "key", "ok": True, "says": "Your fal key: from your keys file", "service": "fal"}
    working = {"state": "on", "by": "use", "says": "Working here · last ran 03:13", "action": None}
    here = {"state": "unchecked", "says": "Ready to run; not checked on this Mac yet", "action": {"kind": "check"}}
    rows = [
        _row("fiji-analyze-particles", "Analyze Particles (Fiji)", ["cells"], "ready", kind="tool-function",
             group="ready", face=working),
        _row("edgetam", "EdgeTAM", ["outline", "cells"], "ready", group="ready", face=working,
             used=[("outline", "Outlining", True)]),
        _row("instanseg-brightfield", "InstanSeg (brightfield nuclei)", ["cells"], "ready", group="ready", face=working),
        _row("basic-bright-objects", "Bright objects", ["cells"], "ready", kind="tool-function", face=here),
        _row("micro-sam-lm", "micro-sam (light microscopy)", ["outline", "cells"], "ready", face=here),
        _row("sam3.1-cloud", "SAM 3.1 (cloud)", ["outline"], "ready", kind="cloud-model", face=here),
        _row("cellpose-sam-tuned", "Cellpose-SAM fine-tuned on your corrections", ["cells"], "needs-setup",
             steps=[{"kind": "weights", "says": "Make its weights here", "model": "cellpose-sam-tuned",
                     "how": "make finetune-cells makes them from your corrected cells"}]),
        _row("sam3.1-local", "SAM 3.1 (this Mac)", ["outline"], "needs-setup",
             steps=[{"kind": "download", "says": "Download 3.5 GB", "model": "sam3.1-local", "bytes": 3502755717},
                    {"kind": "install", "says": "Install SAM 3.1's code", "how": "not installed here (sam3)"}]),
        # A manifest that gives no file sizes: readiness says "Download 1 MB" (its size words' floor), a size never known.
        _row("nuclei-net", "Nuclei net", ["cells"], "needs-setup",
             steps=[{"kind": "download", "says": "Download 1 MB", "model": "nuclei-net"}]),
        _row("cells-cloud", "Cells in the cloud", ["cells"], "needs-setup", kind="cloud-model",
             steps=[{"kind": "key", "says": "Add your Replicate key", "how": "put REPLICATE_API_TOKEN=... in a file",
                     "service": "replicate"}]),
    ]
    for r in rows:
        if r["kind"] == "cloud-model":
            r["runs"], r["where"] = "cloud", "in the cloud"
    rows[5]["facts"] = [key]
    return rows


def test_what_segmentation_models_we_have_keeps_models_and_tools_apart_with_each_additions_needs():
    wire = {"models": _workspace_rows(), "roles": [{"id": "outline", "title": "Outlining"}, {"id": "cells", "title": "Cells"}],
            "setup": {"keys": {}, "news": {"new": []}}}
    svc = SimpleNamespace(hub=SimpleNamespace(), agent=None, capabilities=lambda: wire, downloads=Downloads(), open_page=None)
    said = asyncio.run(registry(svc).tools["models"].handler(
        ctx("What segmentation models do we have, and what others could we add?"), {"does": ["outline", "cells"]}))
    facts = said.split("<catalog-facts", 1)[1].split("</catalog-facts>", 1)[0]
    models, tools = facts.split("Tools, not models", 1)
    # Models apart from tools: Analyze Particles and Bright objects are tools, never among the models.
    assert "Analyze Particles" not in models and "Bright objects" not in models
    assert "- Analyze Particles (Fiji) (cells)" in tools and "- Bright objects (cells)" in tools
    assert "EdgeTAM" not in tools
    # Each model's state in plain words, in three groups: working here, installed but not run here, can be added.
    assert _section(models, "Working here (ran or passed its check on this Mac):") == [
        "EdgeTAM (outline, cells; chosen for Outlining)", "InstanSeg (brightfield nuclei) (cells)"]
    assert _section(models, "Installed here, not run yet (nothing to get or install):") == [
        "micro-sam (light microscopy) (outline, cells)", "SAM 3.1 (cloud) (outline; with your fal key)"]
    assert _section(models, "Can be added (where it runs; every step it needs):") == [
        "Cellpose-SAM fine-tuned on your corrections (cells): on this Mac; Make its weights here (make finetune-cells "
        "makes them from your corrected cells)",
        "SAM 3.1 (this Mac) (outline): on this Mac; Download 3.5 GB; Install SAM 3.1's code (not installed here (sam3))",
        "Nuclei net (cells): on this Mac; Download (its size is not in its manifest)",
        "Cells in the cloud (cells): in the cloud; Add your Replicate key"]
    assert "1 MB" not in said, "a size the manifest does not give is never said"
    assert _section(tools, "Working here (ran or passed its check on this Mac):") == ["Analyze Particles (Fiji) (cells)"]
    assert "REPLICATE_API_TOKEN" not in said, "a key's how stays on its card"
    # The answer keeps the groups: said once, first, with the facts, all inside what the answering round reads.
    assert said.split("\n", 1)[0].endswith("Answer with models and tools apart, as grouped.")
    assert said.index("</catalog-facts>") < ANSWER_WINDOW
