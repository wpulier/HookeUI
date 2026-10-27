"""Session ids (orchestrator/day/sid.py): the only parser of them."""
import time

import pytest

from orchestrator.day import sid
from orchestrator.day.journal import Journal
from orchestrator.day.store import DayStore, SessionStore


@pytest.mark.parametrize("text, parsed", [
    ("2026-10-11", ("2026-10-11", 1)),
    ("2026-10-11.2", ("2026-10-11", 2)),
    ("2026-10-11.10", ("2026-10-11", 10)),
    ("2024-02-29.3", ("2024-02-29", 3)),  # a leap day is a date
])
def test_a_session_id_is_a_date_or_a_date_and_a_number_from_2(text, parsed):
    assert sid.parse(text) == parsed
    assert sid.is_sid(text)
    assert sid.date_of(text) == parsed[0]


@pytest.mark.parametrize("text", [
    "", "2026-10-11.1", "2026-10-11.0", "2026-10-11.02", "2026-10-11.", "2026-10-11.-2", "2026-10-11.2.3",
    "2026-10-11 ", " 2026-10-11", "2026-1-11", "26-10-11", "2026/10/11", "20261011", "2026-13-01", "2026-02-30",
    "2025-02-29", "2026-10-11T00:00", "today", "t1", "archive", "preferences.jsonl", "2026-10-11.x",
    "２０２６-10-11",  # digits of another script are not a date's
])
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError):
        sid.parse(text)
    assert not sid.is_sid(text)


@pytest.mark.parametrize("value", [None, 20261011, b"2026-10-11", ["2026-10-11"]])
def test_a_session_id_is_a_string(value):
    with pytest.raises(ValueError):
        sid.parse(value)
    assert not sid.is_sid(value)


def test_sessions_sort_by_date_then_number_not_as_text():
    ids = ["2026-10-11.10", "2026-10-12", "2026-10-11", "2026-10-11.2", "2026-09-30.3"]
    assert sorted(ids, key=sid.sort_key) == ["2026-09-30.3", "2026-10-11", "2026-10-11.2", "2026-10-11.10", "2026-10-12"]
    with pytest.raises(ValueError):
        sid.sort_key("archive")


def test_a_label_is_the_date_as_the_studio_says_it_with_the_number_after_the_first():
    assert sid.label("2026-10-11") == "Oct 11" == time.strftime("%b %-d", time.strptime("2026-10-11", "%Y-%m-%d"))
    assert sid.label("2026-10-05") == "Oct 5"
    assert sid.label("2026-10-11.2") == "Oct 11 (2)"
    assert sid.label("2026-10-11.12") == "Oct 11 (12)"


def test_the_first_session_of_a_date_is_the_date_itself(tmp_path):
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11"
    assert sid.next_sid(tmp_path / "not-made-yet", "2026-10-11") == "2026-10-11"  # no sessions folder yet


def test_the_dates_own_folder_counts_once_it_holds_a_journal(tmp_path):
    (tmp_path / "2026-10-11" / "workspaces").mkdir(parents=True)  # a studio's workspace before anything was said
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11"
    (tmp_path / "2026-10-11" / "journal.jsonl").write_text("")
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11.2"


def test_later_sessions_number_one_past_the_highest_used_and_never_reuse_one(tmp_path):
    (tmp_path / "2026-10-11").mkdir()
    (tmp_path / "2026-10-11" / "journal.jsonl").write_text("")
    (tmp_path / "2026-10-11.2").mkdir()
    (tmp_path / "2026-10-11.2" / "journal.jsonl").write_text("")
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11.3"
    (tmp_path / "2026-10-11.9").mkdir()  # a numbered folder without a journal is still used
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11.10"
    # Other dates, files, the archive and folders that are not sessions do not count.
    (tmp_path / "2026-10-12.4").mkdir()
    (tmp_path / "2026-10-11.11").write_text("")  # a file, not a session's folder
    (tmp_path / "2026-10-11.x").mkdir()
    (tmp_path / "archive" / "2026-10-11-cleared-120000").mkdir(parents=True)
    (tmp_path / "preferences.jsonl").write_text("")
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11.10"
    assert sid.next_sid(tmp_path, "2026-10-12") == "2026-10-12.5"
    assert sid.next_sid(tmp_path, "2026-10-13") == "2026-10-13"


def test_a_later_session_without_the_dates_own_journal_is_not_reused_either(tmp_path):
    (tmp_path / "2026-10-11.3").mkdir()  # the first was archived ("start today afresh"): its journal moved away
    assert sid.next_sid(tmp_path, "2026-10-11") == "2026-10-11.4"


def test_next_sid_takes_a_date_only(tmp_path):
    assert sid.next_sid(tmp_path, "2026-10-11.2") == "2026-10-11"  # the date of a sid
    with pytest.raises(ValueError):
        sid.next_sid(tmp_path, "yesterday")


def test_an_old_record_belongs_to_the_session_of_its_date_folder():
    assert sid.session_of({"type": "turn"}, "2026-10-11") == "2026-10-11"
    assert sid.session_of({"type": "turn", "session": None}, "2026-10-11") == "2026-10-11"
    assert sid.session_of({"type": "turn", "session": "2026-10-11.2"}, "2026-10-11") == "2026-10-11.2"


def test_a_session_store_names_its_session_and_its_date(tmp_path):
    noon = time.mktime((2026, 10, 11, 12, 0, 0, 0, 0, -1))
    clock = {"now": noon}
    store = SessionStore(root=tmp_path, session="2026-10-11", clock=lambda: clock["now"])
    assert store.sid == store.date == store.day == "2026-10-11"
    assert store.holds("2026-10-11") and not store.holds("2026-10-12") and not store.holds(None)
    assert store.here().session == store.here().day == "2026-10-11"
    assert store.journal.sid == store.journal.day == "2026-10-11"
    assert store.wire()["id"] == "2026-10-11" and store.wire()["label"] == "Oct 11"
    numbered = SessionStore(root=tmp_path, session="2026-10-11.2", clock=lambda: clock["now"])
    assert numbered.sid == "2026-10-11.2" and numbered.date == "2026-10-11"
    assert numbered.holds("2026-10-11") and numbered.wire()["label"] == "Oct 11 (2)"
    assert numbered.wire()["threads"][0]["key"] == "2026-10-11.2/t1"
    assert numbered.journal.path == tmp_path / "2026-10-11.2" / "journal.jsonl"
    # The date follows the clock: the session goes on past midnight and holds both days once it wrote on the second.
    clock["now"] = noon + 86400
    assert store.sid == "2026-10-11" and store.date == "2026-10-12" and store.holds("2026-10-12")
    assert store.days == ["2026-10-11"]
    store.add_turn("user", "chat", "the next morning")
    assert store.days == ["2026-10-11", "2026-10-12"]


def test_the_old_names_still_work(tmp_path):
    assert DayStore is SessionStore
    store = DayStore(root=tmp_path, day="2026-10-11")
    assert store.sid == "2026-10-11"
    with pytest.raises(AttributeError):
        store.day = "2026-10-12"  # read-only: the app reads `sid` or `date`
    assert Journal(day="2026-10-11", root=tmp_path).sid == "2026-10-11"


def test_open_reloads_quietly_and_says_session_opened_once(tmp_path):
    store = SessionStore(root=tmp_path, session="2026-10-11")
    store.add_turn("user", "chat", "first")
    store.set_pref("userLeads", True)
    events = []
    store.listeners.append(events.append)
    generation = store.generation
    store.open("2026-10-12", "switch")
    assert store.sid == "2026-10-12" and store.generation == generation + 1
    assert events == [{"type": "session-opened", "session": "2026-10-12", "previous": "2026-10-11", "why": "switch",
                       "restored": {"liveOutlines": False}}]
    assert store.add_turn("user", "chat", "second").id == 1  # turn ids are the session's own
    assert store.prefs["userLeads"] is True


def test_journals_list_their_sessions_newest_first(tmp_path):
    for name in ("2026-10-11", "2026-10-11.2", "2026-10-11.10", "2026-10-12", "not-a-session"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "journal.jsonl").write_text("")
    (tmp_path / "2026-10-13").mkdir()  # no journal: not a session yet
    journal = Journal("2026-10-12", tmp_path)
    assert journal.sessions() == ["2026-10-12", "2026-10-11.10", "2026-10-11.2", "2026-10-11"]
    assert journal.days() == journal.sessions()
    assert Journal("2026-10-12", tmp_path / "missing").sessions() == []
