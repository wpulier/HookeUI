"""The app's ids never reach the words the user reads at the simple level (live 2026-10-11: 16 of 67 answers showed
them, as "(o1)", "(d8)", "(s2, lower centre)", "(outline o12)"). Formatting, enforced in code: a word in parentheses is
taken out only when it is one of the app's ids now, with the piece that names it; ordinary parentheses, and words that
only look like ids ("p53"), stay exactly as they were."""
import pytest

from orchestrator.brain.loop import without_app_ids

APP_IDS = {"o1", "d8", "s2", "o12", "m1", "w-3019b540-ba65-4f2a", "g1", "c926"}


def ours(word: str) -> bool:
    return word in APP_IDS


@pytest.mark.parametrize("said,shown", [
    ("I outlined the stoma (o1).", "I outlined the stoma."),
    ("I have reopened your snapshot from 11:23 (d8).", "I have reopened your snapshot from 11:23."),
    ("I highlighted the other stoma (s2, lower centre).", "I highlighted the other stoma (lower centre)."),
    ("I have highlighted it for you (outline o12).", "I have highlighted it for you."),
    ("I tracked motion for about 4 seconds (Motion m1).", "I tracked motion for about 4 seconds."),
    ("I closed the photo (w-3019b540-ba65-4f2a). The original stays.", "I closed the photo. The original stays."),
    ("Group 1 (g1): 183 cells", "Group 1: 183 cells"),
    ("The biggest cell (c926, largest) is outlined.", "The biggest cell (largest) is outlined."),
])
def test_the_apps_ids_in_parentheses_never_reach_the_answer(said, shown):
    assert without_app_ids(said, ours) == shown


@pytest.mark.parametrize("text", [
    "Stomata (the pores) open in light (see the guard cells), and cell c926 is the largest.",
    "The tumour suppressor (p53) and the cell cycle phase (g2) are not visible at this magnification.",
    "First:\n\n- the pore (open) .\n- the guard cells",
])
def test_ordinary_parentheses_words_that_only_look_like_ids_and_layout_stay(text):
    assert without_app_ids(text, ours) == text  # an id in the running text is the answer prompt's to avoid


def test_only_the_parenthesis_with_an_id_changes_and_the_layout_stays():
    text = "Two things:\n\n- the stoma (o1)\n- the pore (open) ."
    assert without_app_ids(text, ours) == "Two things:\n\n- the stoma\n- the pore (open) ."


def test_the_brain_knows_its_ids_from_the_board_the_look_and_the_index():
    from types import SimpleNamespace

    from tests.test_sight import make_brain
    from tests.test_brain import obj

    brain, hub, agent = make_brain({"intent": "question"}, {"answer": "A stoma.", "targets": []})
    stoma = obj(hub, "stoma")
    brain.sight = SimpleNamespace(view=SimpleNamespace(things=[SimpleNamespace(id="s2")]))
    hub.resources = SimpleNamespace(get=lambda word: object() if word == "d8" else None)
    assert brain.is_app_id(stoma.id) and brain.is_app_id("s2") and brain.is_app_id("d8")
    assert brain.is_app_id("w-3019b540-ba65-4f2a")
    assert not brain.is_app_id("p53") and not brain.is_app_id("s3")
