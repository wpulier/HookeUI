"""The voice gets a new view note only when what it says changes (live demo 2026-10-06: while the
phone's autofocus hunted, the running look's "current or not" flipped every few seconds, each
flip was a new note, and GPT-Live spoke on them unprompted)."""
from dataclasses import replace
from types import SimpleNamespace

from orchestrator.brain.context import Moment, view_key


def moment(**kw):
    look = SimpleNamespace(specimen="plant stem", things=[SimpleNamespace(name="vascular bundle"), SimpleNamespace(name="pith")])
    base = Moment(outlined=(), outlined_plain=('o1 "plant cell" ',), selected=None, seen="A stem. Things in view: ...",
                  view=look, screen="just the live view", screen_voice="just the live view", researchers="", roster="",
                  history="", recap_lines=(), title="", summary="", thread_tag="", digest="", user_leads=False)
    return replace(base, **kw)


def test_only_a_change_in_what_the_note_says_makes_a_new_note():
    a = moment()
    assert view_key(a) == view_key(moment(seen="Before the slide last moved: A stem. The view has changed since; ..."))
    assert view_key(a) == view_key(moment(seen="A plant stem, reworded."))
    assert view_key(a) != view_key(moment(outlined_plain=('o1 "plant cell" ', 'o2 "nucleus" ')))
    assert view_key(a) != view_key(moment(screen_voice="the magnifier is open on o1 'plant cell'"))
    other = SimpleNamespace(specimen="blood smear", things=[SimpleNamespace(name="red cell")])
    assert view_key(a) != view_key(moment(view=other))


def test_voice_pin_delivery_identity_does_not_reannounce_unchanged_workspace():
    wire = {"sources": [], "items": [], "interaction": {"selected": []}}
    pin = SimpleNamespace(observation=SimpleNamespace(wire=lambda: wire))
    first = moment(request_snapshot=pin, screen_voice='snapshot=a, seq=1, age=10')
    later = replace(first, screen_voice='snapshot=b, seq=2, age=20')
    assert view_key(first) == view_key(later)
    assert view_key(first) != view_key(replace(later, project_title='Another experiment'))
    unchanged = view_key(first)
    wire["items"] = [{"id": "window", "kind": "image", "title": "New input", "open": True, "visible": True}]
    assert view_key(first) != unchanged
