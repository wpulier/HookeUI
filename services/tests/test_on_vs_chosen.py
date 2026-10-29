"""On is not chosen (Part 2.5; orchestrator/capability_shelf.py, catalog_view.py). Live 2026-10-28: "I just enabled
BioCLIP 2, can you show me how it works and use it" was answered "I've set BioCLIP 2 as the model for comparing": turning
a capability on prepares it and offers it; the job's model (the setting) is unchanged until someone chooses it. Every
word the Assistant reads about it now says the two apart, and the setup offers "use it now" when the job's own choice
can't run."""
from orchestrator import capability_shelf as shelf
from tests.test_capability_shelf import App, READY, DOWNLOAD, EDITION, models, role  # noqa: F401 (models: a fixture)


def test_turning_on_an_option_the_job_does_not_use_says_on_not_chosen(models):
    off = {"role": role(("a", READY, "mac"), ("b", DOWNLOAD, "mac"), current="a")}
    on = {"role": role(("a", READY, "mac"), ("b", READY, "mac"), current="a")}
    app = App([off, on])
    turn = shelf.TurnOn(app)
    turn.start("cells", "b")
    turn.advance()
    assert app.said == ["B is on for Find every cell, not chosen: it still uses A. "
                        "Use it for one request, or make it the default?"]
    assert app.settings == {} and app.published == 0, "turning on never chooses"
    # What the Assistant reads of the job: its model, and what is on but not chosen.
    card = shelf.card(on["role"], "Find every cell", "", "", [], [])
    assert card["using"] == "A" and card["onNotChosen"] == ["B"] and card["offerUse"] is None
    said = shelf.words({"cards": [card]})
    assert "chosen (its model): A; also on, not chosen: B (use for one request, or set_default to choose)" in said


def test_the_first_option_on_for_a_job_whose_choice_cant_run_is_offered_to_use_now(models):
    on = {"role": role(("e", EDITION, "mac"), ("a", READY, "mac"), current="e")}
    app = App([{"role": role(("e", EDITION, "mac"), ("a", DOWNLOAD, "mac"), current="e")}, on])
    turn = shelf.TurnOn(app)
    turn.start("cells")
    turn.advance()
    assert app.said == ["A is on for Find every cell, not chosen yet: its choice, E, can't run here. Use A for Find every cell now?"]
    assert app.settings == {}
    card = shelf.card(on["role"], "Find every cell", "", "", [], [])
    assert card["offerUse"] == {"option": "a", "label": "A"} and card["using"] == ""


def test_the_jobs_own_choice_turned_on_is_said_as_its_model(models):
    app = App([{"role": role(("a", DOWNLOAD, "mac"), current="a")}, {"role": role(("a", READY, "mac"), current="a")}])
    turn = shelf.TurnOn(app)
    turn.start("cells")
    turn.advance()
    assert app.said == ["Find every cell is on: A is ready and is the model it uses"]


def test_a_catalog_entry_turned_on_from_its_card_names_the_jobs_that_keep_their_models():
    entry = {"id": "model:bioclip-2", "kind": "model", "title": "BioCLIP 2", "entry": "bioclip-2", "current": "bioclip-2",
             "options": [{"id": "bioclip-2", "label": "BioCLIP 2", "said": "BioCLIP 2", "runs": "mac", "readiness": READY,
                          "check": {"state": "works"}}]}
    wire = {"models": [{"id": "bioclip-2", "usedFor": [{"role": "embed", "option": "bioclip-2", "title": "Look-alikes",
                                                        "current": False, "fixed": False}]}]}
    said = shelf.on_words("BioCLIP 2", entry, entry["options"][0], wire)
    assert said == ("BioCLIP 2 is on (ready here), not chosen for any job: Look-alikes keep their own models. "
                    "Use it for one request, or make it the default?")
    from orchestrator.catalog_view import row_words

    row = {"name": "BioCLIP 2", "where": "this Mac", "status": "ready", "word": "Ready", "usedFor": wire["models"][0]["usedFor"]}
    assert "Used for: Look-alikes (not chosen: the job uses another model)." in row_words(row)
