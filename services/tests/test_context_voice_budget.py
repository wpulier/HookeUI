"""Synthetic I6 input-budget regressions through the real Moment renderers.

No model or capture runs here. The golden fixture builds real app/conversation
state; the compact Moment uses clearly synthetic qualified metadata. These tests
check information reaching GPT-Live, never a canned reply or provider attachment.
Only the parent serial gate executes this module.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from orchestrator.brain import context
from tests.test_golden import build
from tests.test_workspace_context import _capture, _report, _world


READERS = ("view_note", "voice_recap")
LIMITS = {"view_note": 1800, "voice_recap": 1900}


def _render(reader, moment):
    text = getattr(context, reader)(moment)
    assert len(text) <= LIMITS[reader], (reader, len(text))
    return text


def _whole_json_when_present(text):
    """A JSON projection may become plain facts, but cannot be sliced mid-value."""
    for line in text.splitlines():
        if "App facts" not in line:
            continue
        start = line.find("{")
        if start >= 0:
            decoded, _ = json.JSONDecoder().raw_decode(line[start:])
            assert isinstance(decoded, dict)


def _compact_moment(fidelity, *, optional_noise=False):
    brain, _, screen, _, _ = _world()
    _report(screen)
    screen.workspace_capture = _capture(fidelity=fidelity, verified=fidelity == "actual-tab-capture")
    moment = brain.moment()
    awareness = deepcopy(moment.awareness)
    # Synthetic completed-occurrence boundary metadata, not guessed Sight aliases.
    awareness["outputRefs"] = [{"id": "o7", "run": "run-A", "frame": 7,
                                "epoch": 0, "created_at": 999.0, "turn_id": 12,
                                "available": True, "why": ""}]
    awareness["resources"] = ["e3"]
    if optional_noise:
        awareness["capabilities"].update({
            f"optional-{n}": {"available": False, "tool": f"optional_tool_{n}",
                              "why": "optional adapter not installed " * 4}
            for n in range(40)})
    return replace(moment,
                   outlined=('o7 "guard cell" (selected; in the magnifier)',),
                   outlined_plain=('o7 "guard cell"',), selected="o7",
                   seen="Specimen: lucerne leaf. s2 guard cell (left).",
                   screen="magnifier o7 guard cell; lesson r1 Guard cells, step q2; o7 selected",
                   screen_voice="magnifier o7 guard cell; lesson r1 Guard cells, step q2; o7 selected",
                   researchers="Researcher 1: Guard cells, done; Researcher 3: leaf section, running",
                   roster="Researcher 1: Guard cells, done; Researcher 3: leaf section, running",
                   history="User: compare the two guard cells",
                   recap_lines=("User: compare the two guard cells", "You: I will compare them."),
                   title="Leaf stomata", summary="We looked at guard cells.", thread_tag="",
                   digest="Earlier today: blood smear, platelet looked green.",
                   notes=("The user typed: keep the pore visible",), user_leads=True,
                   learner="technical; hints first", awareness=awareness)


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("fidelity", ("approximate-composition", "actual-tab-capture"))
def test_compact_voice_input_preserves_current_user_source_and_output_identity_together(reader, fidelity):
    moment = _compact_moment(fidelity)
    before = deepcopy(moment.awareness)
    text = _render(reader, moment)
    # These short facts can coexist under either unchanged budget. Require whole
    # source/selection and work facts, not a prefix such as 'magnifier sources ('.
    for fact in ("lucerne leaf", "o7", "guard cell", "selected", "magnifier", "r1", "q2",
                 "Researcher 1", "Guard cells", "done", "Researcher 3", "leaf section", "running",

                 "user is leading", "technical", "hints first", "raw-camera", "structured-report",
                 fidelity, "capture-A", "run-A", "context-only"):
        assert fact.lower() in text.lower(), (reader, fact, text)
    # Actual availability is not pixels included in this call or fulfilled action.
    assert "not pixels" in text
    if fidelity == "approximate-composition":
        for missing in ("DOM-text", "controls", "occlusion"):
            assert missing in text, (reader, missing, text)
    else:
        assert "rendered-tab" in text
    _whole_json_when_present(text)
    assert moment.awareness == before  # one reader must not consume the other's facts


@pytest.mark.parametrize("reader", READERS)
def test_optional_catalogue_omissions_do_not_displace_voice_request_and_source(reader):
    moment = _compact_moment("approximate-composition", optional_noise=True)
    text = _render(reader, moment)
    for fact in ("lucerne leaf",
                 "o7", "run-A", "capture-A", "approximate-composition", "controls",
                 "Researcher 3", "leaf section", "running", "context-only"):
        assert fact in text, (reader, fact, text)
    assert "omit" in text.lower(), "excess optional facts must be explicitly omitted"
    _whole_json_when_present(text)


@pytest.mark.parametrize("reader", READERS)
def test_real_golden_state_keeps_current_selection_shown_work_and_research_roster(reader, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    state = build(tmp_path, monkeypatch)
    moment = context.capture(state.brain, notes=tuple(state.brain.voice_notes))
    text = _render(reader, moment)
    for fact in ("lucerne leaf", "stoma", "o1", "selected", "magnifier", "live zoom",
                 "Researcher 1", "lesson", "step 2", "Researcher 2", "done",
                 "Researcher 3", "leaf section", "running", "technical", "Hints first",
                 "user is leading", "raw-camera", "structured-report", "context-only"):
        assert fact.lower() in text.lower(), (reader, fact, text)
    _whole_json_when_present(text)


def test_real_golden_recap_keeps_latest_user_exchange_while_off_event_and_day(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    state = build(tmp_path, monkeypatch)
    moment = context.capture(state.brain, notes=tuple(state.brain.voice_notes))
    text = _render("voice_recap", moment)
    for fact in ("question 9 about the guard cells", "what is a stoma?", "blood smear", "platelet"):
        assert fact in text, (fact, text)
    _whole_json_when_present(text)


LONG_SCENE = ("A person seated in a room, facing the camera. Specimen: not a slide: person in room. Things in view: "
              "s1 left eye (centre right); s2 right eye (centre left); s3 nose (centre); s4 mouth (lower centre); "
              "s5 cabinet door (left); s6 office chair (behind); s7 corkboard (right); s8 water bottle (lower left). "
              "Focus: sharp.")


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("crowding", ("none", "qualified-receipts"))
def test_the_whole_scene_reaches_the_voice_with_the_current_request(reader, crowding):
    """Live 2026-10-10: provenance lines filled the note and only "Specimen: X." of the running look
    survived, so GPT-Live did not know what was in view. The scene is whole, and so is the request."""
    moment = replace(_compact_moment("approximate-composition"), seen=LONG_SCENE)
    if crowding != "none":
        awareness = deepcopy(moment.awareness)
        awareness["outputRefs"] = [
            {"id": "o" + str(n) + "x" * 76, "run": "run-" + str(n) + "r" * 120, "frame": n, "epoch": 0,
             "created_at": 999.0, "turn_id": n, "available": False, "why": "exact original outline gone " * 2}
            for n in range(3)]
        moment = replace(moment, awareness=awareness)
    text = _render(reader, moment)
    for sentence in ("A person seated in a room, facing the camera.", "s1 left eye (centre right)",
                     "s8 water bottle (lower left)", "Focus: sharp."):
        assert sentence in text, (reader, crowding, sentence, text)
    if reader == "voice_recap":
        assert "compare the two guard cells" in text and "keep the pore visible" in text
    assert "raw-camera" in text and "structured-report" in text and "not pixels" in text


def test_the_golden_view_note_carries_what_the_running_look_saw(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    state = build(tmp_path, monkeypatch)
    text = _render("view_note", context.capture(state.brain, notes=tuple(state.brain.voice_notes)))
    assert "A leaf epidermis with stomata." in text and "s1 stoma (centre); s2 guard cell (left)" in text, text


@pytest.mark.parametrize("reader", READERS)
def test_empty_awareness_remains_a_compatible_bounded_input(reader):
    moment = replace(_compact_moment("approximate-composition"), awareness={})
    text = _render(reader, moment)
    assert "magnifier" in text and "Researcher 3" in text
    assert "App facts" not in text


@pytest.mark.parametrize("reader", READERS)
def test_optional_older_exchanges_cannot_look_like_a_new_request_after_the_latest_user(reader):
    moment = replace(_compact_moment("approximate-composition"),
        recap_lines=("User: close the older window", "You: The older window closed.",
                     "User: keep both current windows open"))
    text = _render(reader, moment)
    if reader == "view_note":
        assert "User:" not in text and "You:" not in text
        return
    current = text.index("User: keep both current windows open")
    after = text[current:].splitlines()[1:]
    # Older context may be omitted or identified explicitly as past. A plain
    # later User/You line would falsely make old instructions appear newest.
    assert not any(line.startswith(("User:", "You:")) for line in after), text


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("crowding", ("workspace-metadata", "qualified-receipts"))
def test_bounded_awareness_detail_cannot_displace_current_voice_request_and_work(reader, crowding):
    moment = _compact_moment("approximate-composition")
    awareness = deepcopy(moment.awareness)
    if crowding == "workspace-metadata":
        row = awareness["observations"]["workspace_report"]
        row["placements"] = {"window-" + str(n) + "x" * 48: "floating beside the live microscope view " * 2
                             for n in range(3)}
        row["sources"] = [{"id": "saved-" + str(n) + "s" * 70, "kind": "saved-image",
                           "run": "run-" + str(n) + "r" * 72, "frame": 7 + n}
                          for n in range(4)]
    else:
        awareness["outputRefs"] = [
            {"id": "o" + str(n) + "x" * 76, "run": "run-" + str(n) + "r" * 120,
             "frame": n, "epoch": 0, "created_at": 999.0, "turn_id": n,
             "available": False, "why": "exact original outline is no longer available " * 2}
            for n in range(3)]
    assert len(json.dumps(awareness)) < 8192, "the captured Moment is within its existing input bound"
    moment = replace(moment, awareness=awareness)
    before = deepcopy(awareness)
    text = _render(reader, moment)
    for fact in ("lucerne leaf",
                 "Selected: o7", "magnifier", "r1", "q2", "Researcher 1", "Guard cells",
                 "Researcher 3", "leaf section", "running",
                 "technical", "hints first", "user is leading", "raw-camera", "structured-report",
                 "approximate-composition", "context-only", "not pixels"):
        assert fact.lower() in text.lower(), (reader, crowding, fact, text)
    assert "omit" in text.lower(), "omitted source detail must remain explicitly unknown"
    assert moment.awareness == before
    _whole_json_when_present(text)


def test_a_note_is_not_new_because_a_report_counted_up_or_aged():
    # The voice journeys bench (2026-10-11): the workspace report's seq and age_s, nested in its source, made every
    # loop's key differ, so an unchanged note went to the voice every 3 s (and notes are not reliably silent).
    from types import SimpleNamespace

    from orchestrator.brain.context import _awareness_text

    def facts(seq, age):
        return SimpleNamespace(awareness={"observations": {"workspace_report": {
            "provenance": "structured-report", "current": True,
            "source": {"client": {"workspaceId": "w"}, "seq": seq, "age_s": age, "timing": "server-receive", "visible": True}},
            "workspace_image": {"provenance": "unavailable", "available": False}},
            "outputRefs": [], "capabilities": {}})

    assert _awareness_text(facts(4, 2.88), temporal=False) == _awareness_text(facts(5, 0.87), temporal=False)
    assert _awareness_text(facts(4, 2.88)) != _awareness_text(facts(5, 0.87))  # the voice's own text keeps them


@pytest.mark.parametrize("reader", READERS)
def test_voice_names_owning_project_separately_from_conversation(reader):
    moment = replace(_compact_moment('actual-tab-capture'), project_title='Plant leaf experiment', title='Counting cells')
    text = _render(reader, moment)
    assert 'Project: "Plant leaf experiment"' in text
    assert moment.project_title == 'Plant leaf experiment'


def test_passive_view_update_does_not_replay_an_already_heard_conversation():
    moment = _compact_moment('approximate-composition')
    note = context.view_note(moment)
    assert 'compare the two guard cells' not in note
    assert 'I will compare them' not in note
    assert 'keep the pore visible' not in note
    assert 'blood smear' not in note
    recap = context.voice_recap(moment)
    assert 'compare the two guard cells' in recap
    assert 'keep the pore visible' in recap


def test_auto_title_and_turn_count_do_not_change_the_live_view():
    moment = _compact_moment('approximate-composition')
    titled = replace(moment, title='New automatic title', thread_tag='New automatic title (4), 5 turns')
    assert context.view_key(moment) == context.view_key(titled)
    # A different conversation still changes ownership even if its picture matches.
    switched = replace(moment, conversation_id='another-conversation')
    assert context.view_key(moment) != context.view_key(switched)
