"""The user's settings are the user's, kept until changed (day/prefs.py): one file beside the day folders, not the
day's journal. A new session, a switch and a cold start keep them; only the session's own (the shelf) stay in its
journal."""
import json
import time
from types import SimpleNamespace

from orchestrator import defaults, presenter
from orchestrator.app import restore_prefs
from orchestrator.day import DayStore
from orchestrator.day.journal import Journal
from orchestrator.day.prefs import FILE, SESSION_KEYS, Prefs
from orchestrator.options import Settings
from orchestrator.scale import Scale


def noon(day: int) -> float:
    return time.mktime((2026, 10, day, 12, 0, 0, 0, 0, -1))


def lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def journal(root, day: str, events: list[dict]) -> None:
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_the_last_value_wins_and_a_torn_line_is_skipped(tmp_path):
    prefs = Prefs(tmp_path)
    prefs.set("level", "simple")
    prefs.set("level", "technical")
    prefs.set("userLeads", True)
    assert prefs.set("userLeads", True) is None  # unchanged: nothing written
    with prefs.path.open("a") as f:
        f.write('{"type": "preference", "key": "outlineCo')  # a crash mid-write
    again = Prefs(tmp_path)
    assert again.values == {"level": "technical", "userLeads": True}
    again.set("style", "classic")  # the next write starts on a line of its own
    assert Prefs(tmp_path).values == {"level": "technical", "userLeads": True, "style": "classic"}
    first = json.loads(prefs.path.read_text().splitlines()[0])
    assert first["type"] == "preferences-started" and first["from"] is None  # written once, before the first


def test_a_research_cap_set_to_none_is_kept(tmp_path):
    """No cap is a choice too: it was never recorded when no cap had been set before (key absent, value None), so a
    restart went back to MICROMIND_RESEARCH_CAP."""
    store = DayStore(root=tmp_path)
    store.set_pref("researchCap", None)
    assert [(e["key"], e["value"]) for e in lines(tmp_path / FILE) if e["type"] == "preference"] == [("researchCap", None)]
    settings = Settings()
    settings.research_cap = 5.0  # as the environment would set it
    restore_prefs(settings, DayStore(root=tmp_path))
    assert settings.research_cap is None


def test_a_cold_start_a_day_later_keeps_every_preference(tmp_path):
    """The second bug: a start on a new day read only that day's journal, and every setting was gone."""
    first = DayStore(root=tmp_path, clock=lambda: noon(11))
    first.set_pref("userLeads", True)
    first.set_pref("level", "technical")
    first.set_pref("hintsFirst", True)
    first.set_pref("outlineColor", "green")
    first.set_pref("style", "classic")
    first.set_pref("researchCap", 10.0)
    Scale(first).set_objective(40, via="voice")
    defaults.record(first, {"outline": "sam2.1-tiny"}, {"outline": "sam2.1-tiny"}, by="user", clock=lambda: noon(11))
    kept = dict(first.prefs)
    assert set(kept) == {"userLeads", "level", "hintsFirst", "outlineColor", "style", "researchCap", "scale", "defaults"}

    later = DayStore(root=tmp_path, clock=lambda: noon(12))  # the app stopped overnight and starts on the 12th
    assert later.day == "2026-10-12" and later.prefs == kept and list(later.prefs) == list(kept)
    assert Scale(later).objective == 40
    assert '"preference"' not in later.journal.path.read_text() and '"preference"' not in first.journal.path.read_text()


def test_the_session_keeps_its_shelf_in_its_journal(tmp_path):
    clock = {"now": noon(11)}
    store = DayStore(root=tmp_path, clock=lambda: clock["now"])
    shelf = presenter.Presenter(SimpleNamespace(broadcast=lambda msg: None), research=None,
                                prefs=(store.prefs.get, store.set_pref))  # as App.build binds it, once
    shelf.remove_from_shelf("r1")
    store.set_pref("userLeads", True)
    assert presenter.SHELF_REMOVED in SESSION_KEYS
    assert "shelfRemoved" not in (tmp_path / FILE).read_text()
    assert [e["key"] for e in lines(store.journal.path) if e["type"] == "preference"] == ["shelfRemoved"]
    assert DayStore(root=tmp_path, clock=lambda: clock["now"]).prefs == {"userLeads": True, "shelfRemoved": ["r1"]}
    # Midnight does nothing (ADR 0017): the session and its shelf go on; a new session has its own shelf, the
    # user's settings come with it.
    clock["now"] = noon(12)
    store.new()
    assert store.prefs == {"userLeads": True}
    # One dict for good: the presenter's getter (bound at build) sees the new session's shelf.
    shelf.remove_from_shelf("r2")
    assert shelf.removed() == ["r2"] and store.prefs["shelfRemoved"] == ["r2"]


def test_a_new_session_and_a_switch_keep_every_preference_of_the_users(tmp_path):
    clock = {"now": noon(11)}
    store = DayStore(root=tmp_path, clock=lambda: clock["now"])
    first = store.sid
    store.set_pref("researchCap", 10.0)
    store.set_pref("level", "technical")
    store.set_pref("shelfRemoved", ["r2"])
    heard = []
    store.listeners.append(lambda e: heard.append(e["type"]))
    clock["now"] = noon(12)
    store.new()
    assert store.prefs == {"researchCap": 10.0, "level": "technical"} and heard == ["session-opened"]
    store.switch_session(first)
    assert store.prefs == {"researchCap": 10.0, "level": "technical", "shelfRemoved": ["r2"]}
    assert heard == ["session-opened", "session-opened"]
    assert DayStore(root=tmp_path, clock=lambda: clock["now"], session=first).prefs == store.prefs
    written = [e for e in lines(tmp_path / FILE) if e["type"] == "preference"]
    assert [(e["key"], e["value"]) for e in written] == [("researchCap", 10.0), ("level", "technical")]  # never again


def test_listeners_hear_the_users_preferences_as_before(tmp_path):
    """The memory, the dataset collector and the sim listen for "preference" events: the same shape as when they were
    day journal events, once per change."""
    store = DayStore(root=tmp_path)
    heard = []
    store.listeners.append(heard.append)
    store.set_pref("scale", {"objective": 40, "fieldNumber": 18.0})
    store.set_pref("scale", {"objective": 40, "fieldNumber": 18.0})  # unchanged: nothing heard
    store.set_pref("shelfRemoved", ["r1"])
    assert [(e["type"], e["key"]) for e in heard] == [("preference", "scale"), ("preference", "shelfRemoved")]
    assert list(heard[0]) == ["at", "type", "key", "value"] and heard[0]["value"] == {"objective": 40, "fieldNumber": 18.0}


def test_the_first_start_seeds_the_file_once_from_the_newest_day_with_preferences(tmp_path):
    journal(tmp_path, "2026-10-09", [{"at": noon(9), "type": "preference", "key": "level", "value": "technical"},
                                     {"at": noon(9) + 1, "type": "preference", "key": "outlineColor", "value": "red"}])
    journal(tmp_path, "2026-10-10", [{"at": noon(10), "type": "preference", "key": "outlineColor", "value": "green"},
                                     {"at": noon(10) + 1, "type": "preference", "key": "userLeads", "value": True},
                                     {"at": noon(10) + 2, "type": "preference", "key": "outlineColor", "value": "blue"},
                                     {"at": noon(10) + 3, "type": "preference", "key": "shelfRemoved", "value": ["r1"]}])
    journal(tmp_path, "2026-10-11", [{"at": noon(11), "type": "preference", "key": "shelfRemoved", "value": ["r9"]}])
    journal(tmp_path, "2026-10-13", [{"at": noon(13), "type": "preference", "key": "level", "value": "simple"}])
    store = DayStore(root=tmp_path, clock=lambda: noon(11))
    written = lines(tmp_path / FILE)
    # Today has only the session's shelf, so the newest earlier day with the user's: the 10th, each at its last value.
    assert written[0]["type"] == "preferences-started" and written[0]["from"] == "2026-10-10"
    assert [(e["key"], e["value"], e["seededFrom"], e["setAt"]) for e in written[1:]] == [
        ("outlineColor", "blue", "2026-10-10", noon(10) + 2), ("userLeads", True, "2026-10-10", noon(10) + 1)]
    assert store.prefs == {"outlineColor": "blue", "userLeads": True, "shelfRemoved": ["r9"]}
    # An earlier day read again shows the user's settings as they are now, not that day's copy of them.
    assert DayStore(root=tmp_path, day="2026-10-09").prefs == {"outlineColor": "blue", "userLeads": True}
    # Never again: a second start reads the file, whatever the journals hold.
    text = (tmp_path / FILE).read_text()
    journal(tmp_path, "2026-10-11", [{"at": noon(11), "type": "preference", "key": "level", "value": "simple"}])
    assert DayStore(root=tmp_path, clock=lambda: noon(11)).prefs == {"outlineColor": "blue", "userLeads": True}
    assert (tmp_path / FILE).read_text() == text


def test_todays_journal_seeds_it_when_it_has_preferences(tmp_path):
    journal(tmp_path, "2026-10-10", [{"at": noon(10), "type": "preference", "key": "level", "value": "technical"}])
    journal(tmp_path, "2026-10-11", [{"at": noon(11), "type": "preference", "key": "userLeads", "value": True}])
    assert DayStore(root=tmp_path, clock=lambda: noon(11)).prefs == {"userLeads": True}
    assert lines(tmp_path / FILE)[0]["from"] == "2026-10-11"


def test_with_no_preferences_anywhere_nothing_is_written_until_the_first_one(tmp_path):
    store = DayStore(root=tmp_path)
    assert not (tmp_path / FILE).exists() and store.prefs == {}
    assert Journal(store.day, tmp_path).days() == [store.day]  # the file is no day
    store.set_pref("level", "technical")
    assert [e["type"] for e in lines(tmp_path / FILE)] == ["preferences-started", "preference"]
    assert Journal(store.day, tmp_path).days() == [store.day]


def test_a_choice_of_an_earlier_day_says_the_day(tmp_path):
    settings = Settings()
    at = time.mktime((2026, 10, 11, 2, 41, 0, 0, 0, -1))
    restored = [{"group": "outline", "option": "sam2.1-tiny", "at": at, "by": "user", "problem": ""}]
    assert "as you asked at 02:41." in defaults.words(restored, settings, clock=lambda: at + 3600)
    assert "as you asked on Oct 11 at 02:41." in defaults.words(restored, settings, clock=lambda: at + 86_400)


def test_the_datasets_read_a_scale_kept_only_in_the_users_file(tmp_path):
    """A scale set since the preferences left the day journals is only in preferences.jsonl: a dataset of the window
    still knows the objective at its start and the change inside it. A seeded copy is no change of its own."""
    from orchestrator.datasets.importer import gather
    from orchestrator.paths import Paths

    paths = Paths.under(tmp_path)
    journal(paths.sessions, "2026-10-11", [{"at": noon(11), "type": "preference", "key": "scale",
                                            "value": {"objective": 10, "fieldNumber": 18.0}}])
    store = DayStore(root=paths.sessions, clock=lambda: noon(12))  # seeds the file from the 11th, now
    seeded = lines(paths.sessions / FILE)[1]
    assert seeded["seededFrom"] == "2026-10-11" and seeded["value"]["objective"] == 10
    time.sleep(0.01)
    Scale(store).set_objective(40)
    changed = lines(paths.sessions / FILE)[-1]["at"]
    got = gather(paths, changed - 1, changed + 1, None)
    assert [(e["value"] or {}).get("objective") for e in got.scale_prefs] == [10, 40]  # in force since seeded, then 40
    assert [e["record"]["value"]["objective"] for e in got.events if e["record"].get("type") == "preference"] == [40]
    assert "preferences" in got.read
    # A window of the 11th, before any of this, reads as it did: its own journal's setting, once.
    old = gather(paths, noon(11) - 60, noon(11) + 60, None)
    assert [(e["value"] or {}).get("objective") for e in old.scale_prefs] == [10] and "preferences" not in old.read


def test_library_looks_is_kept_until_changed_and_no_tool_sets_it(tmp_path):
    """The review of cd1feb87: the Behaviour menu's Library looks went back to off at every restart. It is the user's
    choice to send pictures, so it is kept as they made it, and set_default (the agent's) never lists it."""
    from orchestrator.brain.toolbox.choosing import kept_groups
    from orchestrator.options import Settings

    first = DayStore(root=tmp_path, clock=lambda: noon(11))
    defaults.record(first, {"library_looks": "on"}, {"library_looks": "on"}, by="menu", clock=lambda: noon(11))
    settings = Settings()
    assert settings.library_looks == "off"
    restored = defaults.restore(settings, DayStore(root=tmp_path, clock=lambda: noon(12)))
    assert settings.library_looks == "on" and [r["group"] for r in restored] == ["library_looks"]
    assert "library_looks" not in kept_groups()
