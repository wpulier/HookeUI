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


# --- finding a Project by voice (Lane B: "open the leaf one from last week", "what Projects do I have on guard cells") ---
NOV3 = time.mktime((2026, 11, 3, 15, 0, 0, 0, 0, -1))  # Tuesday Nov 3, 15:00 local


def at(month, day, hour=10):
    return time.mktime((2026, month, day, hour, 0, 0, 0, 0, -1))


def test_spans_of_days_in_words():
    from orchestrator.day.catalogue import span_in

    def days(words):
        span = span_in(words, NOV3)
        return (span.first, span.last) if span else None

    assert days("the leaf one from last week") == ("2026-10-26", "2026-11-01")  # the calendar week before, Mon to Sun
    assert days("earlier this week") == ("2026-11-02", "2026-11-03")
    assert days("what did we do in October") == ("2026-10-01", "2026-10-31")
    assert days("the October ones") == ("2026-10-01", "2026-10-31")
    assert days("last month") == ("2026-10-01", "2026-10-31")
    assert days("this month") == ("2026-11-01", "2026-11-03")
    assert days("3 days ago") == days("three days ago") == ("2026-10-31", "2026-10-31")
    assert days("the last 10 days") == ("2026-10-25", "2026-11-03")
    assert days("the past week") == ("2026-10-28", "2026-11-03")
    assert days("two weeks ago") == ("2026-10-19", "2026-10-25")
    assert days("in may") == ("2026-05-01", "2026-05-31")
    assert days("in December") == ("2025-12-01", "2025-12-31")  # said in November: last year's
    assert days("the one that may be stained") is None  # "may" alone is no month
    assert days("the leaf one") is None
    assert days("the day before yesterday") == ("2026-11-01", "2026-11-01")
    assert span_in("last week", NOV3).words == "Oct 26 to Nov 1"


def test_plural_and_singular_are_one_word():
    from orchestrator.day.catalogue import stem

    assert stem("leaves") == stem("leaf") == "leaf"
    assert stem("cells") == stem("cell") and stem("stomata") == stem("stoma")
    assert stem("nuclei") == stem("nucleus") and stem("studies") == stem("study")
    assert stem("epidermis") == "epidermis" and stem("glass") == "glass"


def seeded(root):
    """Five Projects as Will's might be on Nov 3: two leaf ones (last week and the week before), a guard-cell one this
    week, an onion one whose conversation was about guard cells, and an archived test one."""
    write(root, "2026-10-28", [
        {"type": "session-started", "session": "2026-10-28", "name": "Leaf epidermis", "by": "user", "at": at(10, 28)},
        {"type": "thread-opened", "thread": "t1", "title": "", "at": at(10, 28)},
        {"type": "subject", "thread": "t1", "name": "leaf epidermis", "at": at(10, 28)},
    ])
    write(root, "2026-10-20", [
        {"type": "thread-opened", "thread": "t1", "title": "", "at": at(10, 20)},
        {"type": "subject", "thread": "t1", "name": "leaves", "at": at(10, 20)},
    ])
    write(root, "2026-11-02", [
        {"type": "session-started", "session": "2026-11-02", "name": "Stomata in light", "by": "user", "at": at(11, 2)},
        {"type": "thread-opened", "thread": "t1", "title": "Aperture", "at": at(11, 2)},
        {"type": "subject", "thread": "t1", "name": "guard cells", "at": at(11, 2)},
    ])
    write(root, "2026-10-15", [
        {"type": "session-started", "session": "2026-10-15", "name": "Onion skin", "by": "user", "at": at(10, 15)},
        {"type": "thread-opened", "thread": "t1", "title": "", "at": at(10, 15)},
        {"type": "summary", "thread": "t1", "text": "Compared onion cells with the guard cells of a leaf.", "upto": 4,
         "at": at(10, 15, 11)},
    ])
    write(root, "2026-10-16", [
        {"type": "session-started", "session": "2026-10-16", "name": "Test run", "by": "user", "at": at(10, 16)},
        {"type": "session-archived", "archived": True, "by": "user", "at": at(10, 17)},
    ])
    return Catalogue(root)


def test_the_leaf_one_from_last_week_is_one_sure_match(tmp_path):
    cat = seeded(tmp_path)
    ranked = cat.rank("the leaf one from last week", now=NOV3)
    assert ranked.sure is not None and ranked.sure.info.id == "2026-10-28"
    assert ranked.span.words == "Oct 26 to Nov 1" and ranked.words == ["leaf"]
    assert [i.id for i in cat.find("open the leaf one from last week", now=NOV3)] == ["2026-10-28"]


def test_the_leaf_one_alone_is_unsure_and_ranked(tmp_path):
    cat = seeded(tmp_path)
    ranked = cat.rank("the leaf one", now=NOV3)
    assert ranked.sure is None
    assert [m.info.id for m in ranked.matches] == ["2026-10-28", "2026-10-20", "2026-10-15"]  # named first; a summary last
    assert [m.full for m in ranked.matches] == [True, True, False]
    assert ranked.matches[1].matched == {"leaf": "specimen"}  # "leaves" seen is the leaf asked for
    # find, which switch_session reads, gives the full matches only: a word only in a summary is no Project to ask about
    assert [i.id for i in cat.find("the leaf one", now=NOV3)] == ["2026-10-28", "2026-10-20"]


def test_a_time_with_nothing_matching_in_it_gives_the_matches_outside_it_said_so(tmp_path):
    cat = seeded(tmp_path)
    ranked = cat.rank("the leaf one from yesterday", now=NOV3)
    assert ranked.sure is None and {m.info.id for m in ranked.matches if m.full} == {"2026-10-28", "2026-10-20"}
    assert all(m.in_span is False for m in ranked.matches)


def test_projects_on_guard_cells_include_those_where_they_were_talked_about(tmp_path):
    cat = seeded(tmp_path)
    ranked = cat.rank("what Projects do I have on guard cells", now=NOV3)
    assert ranked.words == ["cells", "guard"]
    assert [(m.info.id, m.full) for m in ranked.matches] == [("2026-11-02", True), ("2026-10-15", False)]
    assert ranked.sure.info.id == "2026-11-02"
    assert ranked.matches[1].matched == {"cells": "summary", "guard": "summary"}


def test_a_span_alone_lists_what_touched_it_and_archived_projects_are_found_and_said(tmp_path):
    cat = seeded(tmp_path)
    assert {i.id for i in cat.find("last week", now=NOV3)} == {"2026-10-28"}
    assert {i.id for i in cat.find("in October", now=NOV3)} == {"2026-10-28", "2026-10-20", "2026-10-15", "2026-10-16"}
    archived = cat.get("2026-10-16")
    assert archived.archived and archived.wire()["archived"] is True and "archived" not in cat.get("2026-10-28").wire()
    assert [i.id for i in cat.find("the test one", now=NOV3)] == ["2026-10-16"]
    assert cat.find("the latest one", now=NOV3) == []  # "test" is a word, never part of one


def test_a_time_with_only_partial_matches_in_it_keeps_the_full_match_outside_and_is_unsure(tmp_path):
    """Review of #189: "the onion one from last week" opened Leaf epidermis (last week, its summary named onion cells)
    over Onion skin (the week before). Only a full match in the time decides; else both are given."""
    write(tmp_path, "2026-10-20", [
        {"type": "session-started", "session": "2026-10-20", "name": "Onion skin", "by": "user", "at": at(10, 20)}])
    write(tmp_path, "2026-10-28", [
        {"type": "session-started", "session": "2026-10-28", "name": "Leaf epidermis", "by": "user", "at": at(10, 28)},
        {"type": "thread-opened", "thread": "t1", "title": "", "at": at(10, 28)},
        {"type": "summary", "thread": "t1", "text": "Compared the leaf with onion cells.", "upto": 2, "at": at(10, 28)}])
    cat = Catalogue(tmp_path)
    ranked = cat.rank("open the onion one from last week", now=NOV3)
    assert ranked.sure is None
    assert [(m.info.name, m.full, m.in_span) for m in ranked.matches] == [("Onion skin", True, False),
                                                                          ("Leaf epidermis", False, True)]
    assert [i.name for i in cat.find("open the onion one from last week", now=NOV3)] == ["Onion skin"]  # never the partial
    # Without a time, a word found only in a summary is no Project to open: it is unsure.
    assert cat.rank("the onion one", now=NOV3).sure.info.name == "Onion skin"
    alone = Catalogue(tmp_path)
    assert alone.rank("compared cells", now=NOV3).sure is None


def test_the_other_one_leaves_the_open_project_out(tmp_path):
    cat = seeded(tmp_path)
    ranked = cat.rank("open the other leaf one", current="2026-10-28", now=NOV3)
    assert ranked.sure.info.id == "2026-10-20" and all(m.info.id != "2026-10-28" for m in ranked.matches)


def test_more_times_in_words_and_week_edges():
    from orchestrator.day.catalogue import span_in

    def days(words, now=NOV3):
        span = span_in(words, now)
        return (span.first, span.last) if span else None

    assert days("the week before last") == ("2026-10-19", "2026-10-25")
    assert days("last weekend") == days("this past weekend") == ("2026-10-31", "2026-11-01")
    assert days("in October 2025") == ("2025-10-01", "2025-10-31")
    assert days("the leaf one from tonight") == ("2026-11-03", "2026-11-03")
    monday, sunday = at(11, 2, 9), at(11, 8, 20)  # Monday Nov 2, Sunday Nov 8
    assert days("last week", monday) == days("last week", sunday) == ("2026-10-26", "2026-11-01")
    assert days("this week", monday) == ("2026-11-02", "2026-11-02") and days("this week", sunday) == ("2026-11-02", "2026-11-08")
    assert days("last weekend", sunday) == ("2026-10-31", "2026-11-01") and days("this weekend", sunday) == ("2026-11-07", "2026-11-08")


def test_tonight_and_weekend_are_times_never_name_words(tmp_path):
    cat = seeded(tmp_path)
    write(tmp_path, "2026-11-03", [{"type": "session-started", "session": "2026-11-03", "name": "Leaf, evening",
                                    "by": "user", "at": at(11, 3, 9)}])
    assert Catalogue(tmp_path).rank("the leaf one from tonight", now=NOV3).sure.info.id == "2026-11-03"
