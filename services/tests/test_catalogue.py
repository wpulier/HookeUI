"""The session list read from the journals (day/catalogue.py, ADR 0017): never a second store."""
from __future__ import annotations

import json
import time

from orchestrator.day import ids
from orchestrator.day.catalogue import Catalogue, _day_in, opened_sources, product_of

DAY1 = time.mktime((2026, 10, 11, 9, 0, 0, 0, 0, -1))  # Sunday Oct 11, 09:00 local
DAY2 = DAY1 + 86400


def write(root, sid, events):
    folder = root / sid
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "journal.jsonl").open("a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_an_old_day_journal_is_a_session_named_by_its_date(tmp_path):
    write(tmp_path, "2026-10-11", [
        {"type": "thread-opened", "thread": "t1", "n": 1, "title": "", "at": DAY1},
        {"type": "turn", "thread": "t1", "id": 1, "role": "user", "channel": "chat", "text": "what is this", "at": DAY1 + 5},
        {"type": "subject", "thread": "t1", "name": "leaf epidermis", "named_by": "look", "at": DAY1 + 6},
        {"type": "evidence-saved", "id": "e2", "path": "x", "thread": "t1", "revision": 0, "members": ["c1", "c9"], "at": DAY1 + 60},
        {"type": "research-linked", "thread": "t1", "rid": "r1", "at": DAY1 + 70},
    ])
    (tmp_path / "archive" / "2026-10-10-cleared-1").mkdir(parents=True)
    (tmp_path / "archive" / "2026-10-10-cleared-1" / "journal.jsonl").write_text("{}\n")
    (tmp_path / "preferences.jsonl").write_text('{"type":"preference","key":"level","value":"simple"}\n')
    (tmp_path / "2026-10-12" / "workspaces").mkdir(parents=True)  # a folder with no journal is no session
    [info] = Catalogue(tmp_path).list()
    assert (info.id, info.name, info.label, info.named) == ("2026-10-11", "", "Oct 11", False)
    assert info.created == DAY1 and info.last_active == DAY1 + 70 and info.last_user == DAY1 + 5
    assert info.days == ["2026-10-11"] and info.specimens == ["leaf epidermis"]
    assert info.counts == {"sources": 0, "made": 2, "researchers": 1, "conversations": 1}
    assert info.wire()["counts"] == info.counts


def test_a_named_session_reads_its_header_and_renames_and_lists_newest_first(tmp_path):
    write(tmp_path, "2026-10-11", [{"type": "thread-opened", "thread": "t1", "n": 1, "at": DAY1}])
    write(tmp_path, "2026-10-11.2", [
        {"type": "session-started", "session": "2026-10-11.2", "name": "", "created": DAY1 + 3600, "from": "2026-10-11",
         "by": "user", "at": DAY1 + 3600},
        {"type": "session-renamed", "name": "Onion skin", "auto": True, "at": DAY1 + 3700},
    ])
    cat = Catalogue(tmp_path)
    first, second = cat.list()
    assert first.id == "2026-10-11.2" and first.name == "Onion skin" and not first.named  # the app's name
    assert first.started_from == "2026-10-11" and second.id == "2026-10-11"
    write(tmp_path, "2026-10-11.2", [{"type": "session-renamed", "name": "Onion, stained", "at": DAY1 + 3800}])
    assert cat.get("2026-10-11.2").name == "Onion, stained" and cat.get("2026-10-11.2").named


def test_only_what_grew_is_read_and_a_replaced_journal_is_read_again(tmp_path):
    write(tmp_path, "2026-10-11", [{"type": "thread-opened", "thread": "t1", "at": DAY1}])
    cat = Catalogue(tmp_path)
    assert cat.get("2026-10-11").counts["conversations"] == 1
    write(tmp_path, "2026-10-11", [{"type": "thread-opened", "thread": "t2", "at": DAY1 + 1}])
    with (tmp_path / "2026-10-11" / "journal.jsonl").open("a") as f:
        f.write('{"type": "thread-opened", "thread": "t3"')  # a line still being written: not read yet
    assert cat.get("2026-10-11").counts["conversations"] == 2
    with (tmp_path / "2026-10-11" / "journal.jsonl").open("a") as f:
        f.write(', "at": 5}\n')
    assert cat.get("2026-10-11").counts["conversations"] == 3
    path = tmp_path / "2026-10-11" / "journal.jsonl"
    path.unlink()
    write(tmp_path, "2026-10-11", [{"type": "thread-opened", "thread": "t1", "at": DAY1}])
    assert cat.get("2026-10-11").counts["conversations"] == 1  # replaced: read from the start


def test_a_session_across_days_and_the_ids_each_day_has_given(tmp_path):
    write(tmp_path, "2026-10-11", [
        {"type": "evidence-made", "id": "e3", "lastMember": "c40", "at": DAY1},
        {"type": "tracks-made", "id": "m2", "at": DAY2, "day": "2026-10-12"},
        {"type": "made", "kind": "bookmark", "id": "b4", "at": DAY2},
    ])
    write(tmp_path, "2026-10-12", [{"type": "evidence-made", "id": "e1", "lastMember": "c7", "at": DAY2}])
    cat = Catalogue(tmp_path)
    assert cat.get("2026-10-11").days == ["2026-10-11", "2026-10-12"]
    assert cat.used("2026-10-12", "e") == 1 and cat.used("2026-10-11", "e") == 3
    assert cat.used("2026-10-12", "m") == 2 and cat.used("2026-10-12", "c") == 7 and cat.used("2026-10-12", "b") == 4

    class Store:  # what day/ids.py reads of a session store
        date, catalogue = "2026-10-12", cat
        ids_used = {"e": 3, "c": 40}

    assert ids.next_number(Store, "e") == 4  # above the session's own e3 (Oct 11) and Oct 12's e1
    assert ids.next_number(Store, "c") == 41 and ids.next_number(Store, "m") == 3
    assert ids.next_number(Store, "e", counter=9) == 10 and ids.next_number(None, "e") == 1


def test_products_are_counted_as_made_in_this_session_lists_them():
    assert product_of({"type": "evidence-saved", "id": "e1"}) == ("population", "e1")
    assert product_of({"type": "tracks-saved", "id": "m1"}) == ("tracks", "m1")
    assert product_of({"type": "research-linked", "rid": "r9"}) == ("research", "r9")
    assert product_of({"type": "result", "id": "d1", "tool": "reference_photo"}) == ("result", "d1")
    assert product_of({"type": "result", "id": "d2", "tool": "count"}) is None  # an answer, not a product
    assert product_of({"type": "made", "kind": "bookmark", "id": "b1"}) == ("bookmark", "b1")
    assert product_of({"type": "made", "kind": "recording", "id": "MicroscopeMind-x"}) == ("recording", "MicroscopeMind-x")
    assert product_of({"type": "made", "kind": "named-outline", "id": "o1"}) is None
    # The user's own pictures and recordings are the session's sources (ADR 0022, Q11), not products.
    assert product_of({"type": "result", "id": "d3", "tool": "enhance"}) is None
    assert product_of({"type": "made", "kind": "snapshot", "id": "snapshot-014500-abc-f12"}) is None
    assert product_of({"type": "made", "kind": "dataset", "id": "ds-20261012-014411"}) is None


def test_a_session_claims_what_it_made_whether_or_not_it_is_a_product(tmp_path):
    """A Record dataset and a snapshot are sources, not products, and still the session's: the index and the library
    know whose they are from its journal (`Info.claims`), while the list's "made" counts only products."""
    write(tmp_path, "2026-10-11", [
        {"type": "made", "kind": "dataset", "id": "ds-20261011-031500", "at": DAY1},
        {"type": "made", "kind": "snapshot", "id": "snapshot-090100-abc-f3", "at": DAY1 + 1},
        {"type": "made", "kind": "bookmark", "id": "b1", "at": DAY1 + 2},
        {"type": "made", "kind": "clip", "id": "k1", "kept": False, "at": DAY1 + 3},
    ])
    info = Catalogue(tmp_path).get("2026-10-11")
    assert set(info.claims) == {("dataset", "ds-20261011-031500"), ("snapshot", "snapshot-090100-abc-f3"),
                                ("bookmark", "b1"), ("clip", "k1")}
    assert set(info.made) == {("bookmark", "b1")} and info.counts["made"] == 1


def test_the_list_counts_the_sources_a_session_opened_and_kept(tmp_path):
    """The session row's "3 sources": the assets opened in it and not removed, never the live camera; the ones that
    joined on their own (its Record videos, snapshots) need the library's scan, so the list leaves them out."""
    write(tmp_path, "2026-10-11", [
        {"type": "source", "asset": "live", "op": "opened", "at": DAY1},
        {"type": "source", "asset": "a1", "op": "opened", "at": DAY1 + 1},
        {"type": "source", "asset": "a2", "op": "opened", "at": DAY1 + 2},
        {"type": "source", "asset": "a3", "op": "opened", "at": DAY1 + 3},
        {"type": "source", "asset": "a2", "op": "removed", "at": DAY1 + 4},
        {"type": "source", "asset": "a1", "op": "opened", "at": DAY1 + 5},  # again: one source, newest
    ])
    cat = Catalogue(tmp_path)
    info = cat.get("2026-10-11")
    assert opened_sources(info) == ["a1", "a3"] and info.counts["sources"] == 2
    write(tmp_path, "2026-10-11", [{"type": "source", "asset": "a2", "op": "restored", "at": DAY1 + 6}])
    assert cat.get("2026-10-11").counts["sources"] == 3 and info.wire()["counts"]["sources"] == 3


def test_a_session_row_has_a_picture_of_what_it_last_looked_at(tmp_path):
    """The session switcher's picture (session-menu.ts): what was on stage when it was last used, else the newest asset it
    added; a session that only ever looked live has none (the switcher draws the camera's icon)."""
    write(tmp_path, "2026-10-11", [{"type": "source", "asset": "live", "op": "opened", "at": DAY1}])
    write(tmp_path, "2026-10-11.2", [
        {"type": "source", "asset": "a1", "op": "opened", "at": DAY1 + 1},
        {"type": "source", "asset": "a3", "op": "opened", "at": DAY1 + 2},
        {"type": "source", "asset": "a1", "op": "opened", "at": DAY1 + 3},  # on stage again
        {"type": "source", "asset": "live", "op": "opened", "at": DAY1 + 4},
    ])
    cat = Catalogue(tmp_path)
    assert "thumb" not in cat.get("2026-10-11").wire()
    assert cat.get("2026-10-11.2").wire()["thumb"] == "/api/library/a1/thumbnail"  # live last: the newest it added
    write(tmp_path, "2026-10-11.2", [{"type": "source", "asset": "a3", "op": "opened", "at": DAY1 + 5},
                                     {"type": "source", "asset": "a3", "op": "removed", "at": DAY1 + 6}])
    assert cat.get("2026-10-11.2").wire()["thumb"] == "/api/library/a1/thumbnail"  # one taken out is never its picture


def test_finding_a_session_by_id_date_words_or_previous(tmp_path):
    write(tmp_path, "2026-10-11", [
        {"type": "thread-opened", "thread": "t1", "title": "Blood smear", "at": DAY1},
        {"type": "subject", "thread": "t1", "name": "blood smear", "at": DAY1},
    ])
    write(tmp_path, "2026-10-11.2", [
        {"type": "session-started", "session": "2026-10-11.2", "name": "Leaf epidermis", "by": "user", "at": DAY1 + 7200},
        {"type": "subject", "thread": "t1", "name": "onion", "at": DAY2},
    ])
    cat = Catalogue(tmp_path)
    now = DAY2 + 3600
    assert [i.id for i in cat.find("2026-10-11.2")] == ["2026-10-11.2"]
    assert [i.id for i in cat.find("the leaf session", now=now)] == ["2026-10-11.2"]
    assert [i.id for i in cat.find("blood smear", now=now)] == ["2026-10-11"]
    assert [i.id for i in cat.find("yesterday's blood smear session", now=now)] == ["2026-10-11"]
    assert [i.id for i in cat.find("previous", current="2026-10-11.2")] == ["2026-10-11"]
    assert {i.id for i in cat.find("yesterday", now=now)} == {"2026-10-11", "2026-10-11.2"}
    assert cat.find("kidney", now=now) == [] and cat.find("") == []


def test_dates_in_words():
    now = time.mktime((2026, 10, 13, 12, 0, 0, 0, 0, -1))  # Tuesday Oct 13
    assert _day_in("tuesday's blood smear", now) == "2026-10-13"
    assert _day_in("on sunday", now) == "2026-10-11"
    assert _day_in("yesterday", now) == "2026-10-12"
    assert _day_in("the oct 9 one", now) == "2026-10-09"
    assert _day_in("december 30", now) == "2025-12-30"
    assert _day_in("2026-10-10", now) == "2026-10-10"
    assert _day_in("the leaf one", now) is None


def test_day_words_are_calendar_days_just_after_midnight_and_a_clock_change(new_york):
    """'Yesterday' and a weekday are the calendar's, never 24 hours back: in the first hour after a 23-hour day (Sunday
    2027-03-14, clocks forward) 24 hours back is the Saturday."""
    after_midnight = time.mktime((2026, 10, 16, 0, 5, 0, 0, 0, -1))  # Friday Oct 16, 00:05 EDT
    assert _day_in("yesterday", after_midnight) == "2026-10-15"
    assert _day_in("today", after_midnight) == "2026-10-16"
    assert _day_in("thursday", after_midnight) == "2026-10-15"
    after_clocks_forward = time.mktime((2027, 3, 15, 0, 30, 0, 0, 0, -1))  # Monday Mar 15, 00:30 EDT
    assert _day_in("yesterday", after_clocks_forward) == "2027-03-14"
    assert _day_in("sunday", after_clocks_forward) == "2027-03-14"
    assert _day_in("saturday", after_clocks_forward) == "2027-03-13"


def test_a_session_used_on_the_day_clocks_went_forward_is_said_yesterday_after_midnight(new_york):
    """How a session's last use is said (the "Back in" notice, switch_session's rows) counts the same calendar days."""
    from orchestrator.app import last_used_words
    from orchestrator.brain.toolbox.threads import _day_words

    sunday_evening = time.mktime((2027, 3, 14, 20, 12, 0, 0, 0, -1))
    after_clocks_forward = time.mktime((2027, 3, 15, 0, 30, 0, 0, 0, -1))
    assert last_used_words(sunday_evening, after_clocks_forward) == "yesterday 8:12 pm"
    assert _day_words(sunday_evening, after_clocks_forward) == "yesterday"


def test_the_last_session_used_is_the_last_one_asked_in(tmp_path):
    write(tmp_path, "2026-10-11", [{"type": "turn", "role": "user", "thread": "t1", "id": 1, "text": "x", "at": DAY1 + 50}])
    write(tmp_path, "2026-10-12", [{"type": "thread-opened", "thread": "t1", "at": DAY2}])  # midnight's empty one
    assert Catalogue(tmp_path).last_used().id == "2026-10-11"


def test_a_session_called_exactly_that_is_the_one_even_when_another_looked_at_the_same(tmp_path):
    """The partner bench of 2026-10-29: "Open the Nuclei project" also matched the untitled session that had looked at
    nuclei, so nothing was opened. A session named exactly that is it; words still rank the rest."""
    write(tmp_path, "2026-10-29", [
        {"type": "thread-opened", "thread": "t1", "title": "", "at": DAY1},
        {"type": "subject", "thread": "t1", "name": "fluorescently stained cell nuclei", "at": DAY1},
    ])
    write(tmp_path, "2026-10-29.2", [
        {"type": "session-started", "session": "2026-10-29.2", "name": "Nuclei", "by": "user", "at": DAY1 + 60},
        {"type": "subject", "thread": "t1", "name": "fluorescently stained cell nuclei", "at": DAY1 + 120},
    ])
    cat = Catalogue(tmp_path)
    assert [i.id for i in cat.find("Nuclei")] == ["2026-10-29.2"]
    assert len(cat.find("stained")) == 2
