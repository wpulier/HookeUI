"""The live partner's acceptance standard, scored from recorded event logs (bench/voice_journeys/standard.py).

One synthetic run of the one brain meets every line; each case below breaks one line the way a real run would (a slow
first word, a barge-in that doesn't go quiet, an outcome said twice, a leaked note, a second researcher...) and the
scoreboard must say FAIL on that line. GPT-Live's run, with no partner records, leaves the one brain's lines "not
measured", which is never a pass."""
from __future__ import annotations

import copy
import json

import pytest

from bench.voice_journeys import standard
from bench.voice_journeys.standard import meets, score_run, scoreboard, summarise

T0 = 1_800_000_000.0

PLAN = {"id": "synthetic", "steps": [
    {"id": "outline", "expect": {"tools": ["outline_cells"], "visible": "outlines", "outcome": True}},
    {"id": "table", "expect": {"tools": ["show_measurements"], "visible": "table in a Measurements tab",
                               "marks": ["tab:", "document:"]}},
    {"id": "researcher", "expect": {"tools": ["start_researcher"], "researchers": 1, "visible": "researcher card"}},
    {"id": "status", "expect": {"tools": ["research_status"], "toolRequired": False, "status": "latest"}},
    {"id": "explain", "expect": {"tools": [], "toolRequired": False}},
    {"id": "barge", "interrupt": True, "expect": {"tools": [], "toolRequired": False}},
    {"id": "multi", "expect": {"tools": ["outline_cells", "measure"], "visible": "outlines and sizes", "multi": True}},
]}


def ms(t):
    return round((T0 + t) * 1000)


def step(sid, down, start, end, up, **more):
    return {"id": sid, "kind": "act", "say": sid, "down": ms(down), "start": ms(start), "end": ms(end), "up": ms(up), **more}


def audio(on, off):
    return [{"t": ms(on), "e": "audio", "on": True}, {"t": ms(off), "e": "audio", "on": False}]


def talk(n, t):
    return {"t": ms(t), "e": "ws-out", "m": {"type": "talk", "down": True, "n": n}}


def tool(n, name, start, end, *, report=None, screen=False, ok=True, **more):
    return {"kind": "partner-tool", "t": T0 + start, "n": n, "name": name, "startT": T0 + start, "endT": T0 + end,
            "ok": ok, "screenChange": screen, "confirmed": report is not None if screen else None,
            "reportT": T0 + report if report is not None else None, **more}


def line(lid, at, text):
    return {"type": "chat", "role": "assistant", "voice": True, "lineId": lid, "at": T0 + at, "text": text}


def good():
    """A run of the one brain that meets every line of the standard."""
    steps = [step("outline", 0, 0.1, 1.5, 1.6), step("table", 10, 10.1, 12.0, 12.1),
             step("researcher", 20, 20.1, 23.0, 23.1), step("status", 30, 30.1, 31.5, 31.6),
             step("explain", 40, 40.1, 41.5, 41.6), step("barge", 44.0, 44.2, 45.5, 45.6, interrupt=True),
             step("multi", 55, 55.1, 57.0, 57.1)]
    events = []
    for t in range(-10, 0):  # the live view before Talk
        events.append({"t": ms(t), "e": "frames", "fps": 30, "delayP90": 60})
    for t in range(0, 90, 5):
        events.append({"t": ms(t), "e": "frames", "fps": 30, "delayP90": 70})
    for n, t in enumerate([0, 10, 20, 30, 40, 44.0, 55], start=1):
        events.append(talk(n, t))
    for on, off in [(2.0, 2.9), (2.95, 4.5), (12.5, 13.3), (13.6, 15.0), (23.5, 24.5), (24.6, 26.0), (32.0, 34.0),
                    (42.0, 44.1), (46.0, 47.0), (57.5, 58.4), (58.9, 60.2), (61.1, 63.0), (70.8, 72.0)]:
        events += audio(on, off)
    events += [{"t": ms(13.65), "e": "mark", "on": True, "id": "m1", "target": "tab:Measurements",
                "rect": [10, 10, 100, 30], "targetRect": [10, 10, 100, 30]},
               {"t": ms(14.0), "e": "mark", "on": True, "id": "m1", "target": "tab:Measurements",
                "rect": [10, 10, 100, 30], "targetRect": [11, 10, 100, 30]},
               {"t": ms(13.7), "e": "ws-out", "m": {"type": "marks-shown", "id": "m1",
                                                     "resolved": [{"target": "tab:Measurements", "ok": True}]}},
               {"t": ms(16.2), "e": "mark", "on": False, "id": "m1", "target": "tab:Measurements"},
               {"t": ms(25.0), "e": "researchers", "rows": [{"rid": "r1", "number": 1, "group": "done"},
                                                            {"rid": "r2", "number": 2, "group": "working"}]},
               {"t": ms(70.85), "e": "mark", "on": True, "id": "m2", "target": "researcher:2"},
               {"t": ms(73.0), "e": "mark", "on": False, "id": "m2", "target": "researcher:2"},
               {"t": ms(80.0), "e": "fault", "fault": "close-provider"}]
    bench = {"plan": "synthetic", "voice": "gpt-realtime-2.1", "talk": "hold", "steps": steps, "events": events}
    conv = [line("L1", 2.0, "Outlining the nuclei now."), line("L2", 2.95, "Done: 42 nuclei outlined."),
            line("L3", 12.5, "Opening the table in a new tab."), line("L4", 13.6, "The table is in the Measurements tab."),
            line("L5", 23.5, "Starting a researcher on the measurements."),
            line("L6", 24.6, "Researcher 2 has started; it is reading the table."),
            line("L7", 32.0, "Researcher 2 is still reading the table."),
            line("L8", 42.0, "Each nucleus was measured from its outline."), line("L9", 46.0, "Forty-two."),
            line("L10", 57.5, "Outlining first, then measuring."), line("L11", 58.9, "Outlined; measuring now."),
            line("L12", 61.1, "Measured: the median area is 210 square microns."),
            line("L13", 70.8, "Researcher 2 is done: two size groups; it's in Findings.")]
    inter = [tool(1, "outline_cells", 1.9, 2.6, report=2.7, screen=True),
             tool(2, "show_measurements", 12.4, 13.4, report=13.5, screen=True), tool(2, "mark", 13.6, 13.7),
             tool(3, "start_researcher", 23.4, 24.0, rid="r2", ridT=T0 + 23.9),
             tool(4, "research_status", 31.8, 31.9),
             tool(7, "outline_cells", 57.4, 58.5, report=58.6, screen=True),
             tool(7, "measure", 58.7, 60.9, report=61.0, screen=True),
             {"kind": "partner-response", "t": T0 + 72, "cause": "proactive", "firstAudioT": T0 + 70.8,
              "audioEndT": T0 + 72.0, "transcript": "Researcher 2 is done: two size groups; it's in Findings.",
              "usage": {"inText": 10000, "inAudio": 500, "inImage": 2000, "outText": 100, "outAudio": 400}},
             {"kind": "partner-proactive", "t": T0 + 70.2, "event": "researcher-done", "ref": "r2", "eventT": T0 + 70.0,
              "userBusy": False, "responseCreatedT": T0 + 70.2, "markTarget": "researcher:2"},
             {"kind": "partner-reconnect", "t": T0 + 81.2, "reason": "bench", "closedT": T0 + 80.05,
              "readyT": T0 + 81.2, "ms": 1150, "midSpeech": False}]
    verdicts = {"outline": {"claims_on_screen": [1], "answered": True},
                "table": {"claims_on_screen": [1], "answered": True},
                "researcher": {"claims_on_screen": [], "answered": True},
                "multi": {"claims_on_screen": [2], "answered": True}}
    return bench, conv, inter, verdicts


def board(bench, conv, inter, verdicts=None, plan=PLAN):
    run = score_run(bench, conv, inter, plan, verdicts)
    summary = summarise([run])
    return run, summary, {line["line"]: line for line in scoreboard(summary)}


def result(lines, name):
    return lines[name]["result"]


def test_a_run_that_meets_the_standard_passes_every_line():
    run, summary, lines = board(*good())
    failed = {name: line for name, line in lines.items() if line["result"] != "pass"}
    assert not failed, json.dumps(failed, indent=1) + json.dumps([r["failures"] for r in run["steps"]], indent=1)
    assert meets(list(lines.values()))
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["outline"]["firstAudioS"] == pytest.approx(0.4)
    assert rows["outline"]["outcomeS"] == pytest.approx(1.35)
    assert rows["multi"]["outcomeS"] == pytest.approx(4.0)
    assert rows["multi"]["longestSilenceS"] == pytest.approx(0.9)
    assert rows["barge"]["bargeS"] == pytest.approx(0.1)
    assert rows["status"]["statusExpected"] == 2
    # The proactive line is no step's outcome (said once holds on the multi step around it).
    assert rows["multi"]["outcomesSaid"] == 1
    assert summary["marks"]["latencyMax"] == pytest.approx(0.05)
    assert summary["reconnect"]["max"] == pytest.approx(1.15)
    assert standard.realtime_cost(good()[2]) == pytest.approx(10000 * 4e-6 + 500 * 32e-6 + 2000 * 5e-6 + 100 * 24e-6 + 400 * 64e-6)


def shift_audio_on(bench, at, to):
    for e in bench["events"]:
        if e.get("e") == "audio" and e.get("on") and e["t"] == ms(at):
            e["t"] = ms(to)


def test_slow_first_audio_fails():
    bench, conv, inter, verdicts = good()
    for at in (2.0, 12.5, 23.5, 32.0):  # four of seven first words at 2.4 s
        shift_audio_on(bench, at, at + 2.0)
    _, summary, lines = board(bench, conv, inter, verdicts)
    assert result(lines, "Release to first audio") == "FAIL"


def test_barge_in_that_is_not_silent_in_time_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "audio" and not e.get("on") and e["t"] == ms(44.1):
            e["t"] = ms(44.4)  # 400 ms to silence
    assert result(board(bench, conv, inter, verdicts)[2], "Barge-in silent") == "FAIL"


def test_barge_in_that_comes_back_while_held_fails():
    bench, conv, inter, verdicts = good()
    bench["events"] += audio(44.6, 45.0)  # the old answer heard again during the hold
    _, summary, lines = board(bench, conv, inter, verdicts)
    assert summary["barge"]["leaks"] == 1 and result(lines, "Barge-in silent") == "FAIL"


def test_slow_single_step_fails():
    bench, conv, inter, verdicts = good()
    for r in inter:
        if r.get("name") in ("outline_cells", "show_measurements") and r["n"] in (1, 2):
            r["reportT"] += 5.0
    for c in conv:  # the outcome said once the report came
        if c["lineId"] in ("L2", "L4"):
            c["at"] += 5.0
    assert result(board(bench, conv, inter, verdicts)[2], "Single-step: visible and spoken") == "FAIL"


def test_multi_step_over_eight_seconds_fails():
    bench, conv, inter, verdicts = good()
    for r in inter:
        if r.get("name") == "measure":
            r["reportT"] = T0 + 66.0
    for c in conv:
        if c["lineId"] == "L12":
            c["at"] = T0 + 66.1
    bench["events"] += audio(66.1, 67.0)
    assert result(board(bench, conv, inter, verdicts)[2], "Multi-step: visible and spoken") == "FAIL"


def test_silence_over_one_and_a_half_seconds_while_working_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "audio" and e.get("on") and e["t"] == ms(61.1):
            e["t"] = ms(62.5)  # 2.3 s of nothing after "measuring now"
    for c in conv:
        if c["lineId"] == "L12":
            c["at"] = T0 + 62.5
    assert result(board(bench, conv, inter, verdicts)[2], "Longest silence while working") == "FAIL"


def test_outcome_said_twice_fails():
    bench, conv, inter, verdicts = good()
    conv.append(line("L2b", 4.6, "So that's 42 nuclei outlined."))
    bench["events"] += audio(4.6, 6.0)
    run, _, lines = board(bench, conv, inter, verdicts)
    assert {r["id"]: r for r in run["steps"]}["outline"]["outcomesSaid"] == 2
    assert result(lines, "Each outcome said exactly once") == "FAIL"


def test_outcome_never_said_fails():
    bench, conv, inter, verdicts = good()
    conv[:] = [c for c in conv if c["lineId"] != "L4"]
    run, _, lines = board(bench, conv, inter, verdicts)
    assert "the outcome was not said" in {r["id"]: r for r in run["steps"]}["table"]["failures"]
    assert result(lines, "Runs with zero step failures") == "FAIL"


@pytest.mark.parametrize("text", ["Done: 42 nuclei outlined. Nothing was outlined.", "Context only: the table is up.",
                                  'Here it is: {"tool": "present"}', "It's saved in Data as e3.",
                                  "I called show_measurements for you."])
def test_text_meant_for_the_model_spoken_fails(text):
    bench, conv, inter, verdicts = good()
    conv[1] = line("L2", 2.95, text)
    assert result(board(bench, conv, inter, verdicts)[2], "Nothing meant for the model spoken") == "FAIL"


def test_plain_speech_is_not_a_leak():
    for text in ("Researcher 2 is still reading the table.", "Measured: the median area is 210 square microns.",
                 "It's in the Measurements tab, in front."):
        assert standard.leaks(text) == []


def test_cant_see_and_restart_advice_fail():
    bench, conv, inter, verdicts = good()
    conv[7] = line("L8", 42.0, "I can't see your screen right now.")
    conv[8] = line("L9", 46.0, "Try to quit and reopen the app.")
    lines = board(bench, conv, inter, verdicts)[2]
    assert result(lines, 'Never "I can\'t see your screen"') == "FAIL"
    assert result(lines, 'Never "restart the app"') == "FAIL"


def test_wrong_first_tool_fails():
    bench, conv, inter, verdicts = good()
    inter.insert(1, tool(2, "tabs", 12.2, 12.3))  # a Tab made first, then the table (live 2026-10-28)
    run, _, lines = board(bench, conv, inter, verdicts)
    assert {r["id"]: r for r in run["steps"]}["table"]["toolRight"] is False
    assert result(lines, "Right tool on the first try") == "FAIL"


def test_do_in_hooke_over_five_percent_fails():
    bench, conv, inter, verdicts = good()
    inter.append(tool(5, "do_in_hooke", 41.8, 43.0))
    plan = copy.deepcopy(PLAN)
    plan["steps"][4]["expect"] = {"tools": ["do_in_hooke"], "toolRequired": False}
    _, summary, lines = board(bench, conv, inter, verdicts, plan)
    assert summary["doInHooke"]["calls"] == 1 and result(lines, "do_in_hooke share") == "FAIL"


def test_false_done_fails():
    bench, conv, inter, verdicts = good()
    verdicts["table"]["claims_on_screen"] = [0, 1]  # "Opening the table" judged a claim, said before the report
    conv[2] = line("L3", 12.5, "The table is up.")
    assert result(board(bench, conv, inter, verdicts)[2], 'False "done"') == "FAIL"


def test_false_done_unmeasured_without_the_judge():
    bench, conv, inter, _ = good()
    assert result(board(bench, conv, inter, None)[2], 'False "done"') == "not measured"
    assert not meets(scoreboard(board(bench, conv, inter, None)[1]))


def test_two_researchers_from_one_request_fail():
    bench, conv, inter, verdicts = good()
    inter.append(tool(3, "start_researcher", 24.1, 24.5, rid="r3", ridT=T0 + 24.4))
    assert result(board(bench, conv, inter, verdicts)[2], "One researcher per request") == "FAIL"


def test_researcher_started_late_fails():
    bench, conv, inter, verdicts = good()
    for r in inter:
        if r.get("name") == "start_researcher":
            r["ridT"] = T0 + 26.0
    assert result(board(bench, conv, inter, verdicts)[2], "Researcher started") == "FAIL"


def test_status_naming_another_researcher_fails():
    bench, conv, inter, verdicts = good()
    conv[6] = line("L7", 32.0, "Researcher 1 finished earlier.")  # the stopped one (live 2026-10-28)
    assert result(board(bench, conv, inter, verdicts)[2], "Status names the right researcher") == "FAIL"


def test_status_in_words_names_the_number():
    assert standard.researcher_numbers("Researcher two is reading the table.") == [2]


def test_late_mark_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "mark" and e.get("id") == "m1" and e.get("on"):
            e["t"] += 600  # 0.65 s after the speech started
    assert result(board(bench, conv, inter, verdicts)[2], "Mark after speech starts") == "FAIL"


def test_mark_on_the_wrong_target_or_unresolved_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "mark" and e.get("id") == "m1":
            e["target"] = "panel:magnifier"
    bench["events"].append({"t": ms(13.8), "e": "ws-out", "m": {"type": "marks-shown", "id": "m3", "resolved": [
        {"target": "tab:Sizes", "ok": False, "why": "ambiguous"}]}})
    _, summary, lines = board(bench, conv, inter, verdicts)
    assert summary["marks"]["wrongTarget"] == 1 and summary["marks"]["unresolved"] == 1
    assert result(lines, "Id marks correct and following") == "FAIL"


def test_mark_not_following_its_target_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "mark" and e.get("id") == "m1" and e.get("on"):
            e["targetRect"] = [300, 300, 100, 30]  # the tab moved, the mark stayed
    assert result(board(bench, conv, inter, verdicts)[2], "Id marks correct and following") == "FAIL"


def test_mark_left_after_speech_fails():
    bench, conv, inter, verdicts = good()
    bench["events"] = [e for e in bench["events"] if not (e.get("e") == "mark" and e.get("id") == "m1" and not e.get("on"))]
    assert result(board(bench, conv, inter, verdicts)[2], "Cleared at speech end") == "FAIL"


def test_event_spoken_late_or_over_the_user_fails():
    bench, conv, inter, verdicts = good()
    shift_audio_on(bench, 70.8, 73.5)  # 3.5 s after the researcher finished
    assert result(board(bench, conv, inter, verdicts)[2], "Event spoken when idle") == "FAIL"
    bench, conv, inter, verdicts = good()
    for r in inter:
        if r.get("kind") == "partner-proactive":
            r["responseCreatedT"], r["eventT"] = T0 + 56.0, T0 + 55.8  # while the user holds the key for "multi"
    bench["events"] += audio(56.2, 56.8)
    _, summary, lines = board(bench, conv, inter, verdicts)
    assert summary["events"]["overUser"] == 1
    assert result(lines, "Never over the user, one line, pointing") == "FAIL"


def test_event_said_twice_fails():
    bench, conv, inter, verdicts = good()
    inter.append({**[r for r in inter if r.get("kind") == "partner-proactive"][0], "t": T0 + 75})
    assert result(board(bench, conv, inter, verdicts)[2], "Never over the user, one line, pointing") == "FAIL"


def test_slow_reconnect_fails():
    bench, conv, inter, verdicts = good()
    for r in inter:
        if r.get("kind") == "partner-reconnect":
            r["ms"], r["readyT"] = 3400, T0 + 83.4
    assert result(board(bench, conv, inter, verdicts)[2], "Reconnect") == "FAIL"


def test_live_view_slowed_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "frames" and e["t"] >= ms(0):
            e["fps"], e["delayP90"] = 21, 180
    assert result(board(bench, conv, inter, verdicts)[2], "Live view frame rate and delay unchanged") == "FAIL"


def test_restart_checks_count_as_step_failures():
    bench, conv, inter, verdicts = good()
    bench["events"].append({"t": ms(41.0), "e": "check", "step": "explain", "name": "tab:Measurements", "ok": False,
                            "detail": "no Tab named Measurements after reopening"})
    run, summary, lines = board(bench, conv, inter, verdicts)
    assert summary["stepFailures"] == 1 and result(lines, "Runs with zero step failures") == "FAIL"


def test_long_answers_fail_elegance():
    bench, conv, inter, verdicts = good()
    for c in conv:
        c["text"] = "One. Two. Three."
    assert result(board(bench, conv, inter, verdicts)[2], "One or two sentences") == "FAIL"


def test_gpt_live_run_scores_from_cards_and_leaves_partner_lines_unmeasured():
    """GPT-Live: no partner records; its brain's tool cards (chat-action) give the tools and the screen's confirmations
    (a late ✓ is the same action shown again ok); marks, events and reconnects are not measured, never passed."""
    bench, conv, _, verdicts = good()
    bench["voice"], bench["talk"] = "gpt-live-1", "open"
    for s in bench["steps"]:
        s.pop("down"), s.pop("up")
    bench["events"] = [e for e in bench["events"] if e.get("e") not in ("mark", "fault")
                       and (e.get("m") or {}).get("type") not in ("talk", "marks-shown")]
    conv = [c for c in conv if c["lineId"] != "L13"]
    conv += [{"type": "chat-action", "tool": "outline_cells", "actionId": "a1", "ok": None, "at": T0 + 1.9},
             {"type": "chat-action", "tool": "outline_cells", "actionId": "a1", "ok": True, "at": T0 + 2.7},
             {"type": "chat-action", "tool": "show_measurements", "actionId": "a2", "ok": True, "at": T0 + 13.5},
             {"type": "chat-action", "tool": "start_researcher", "actionId": "a3", "ok": True, "at": T0 + 23.9,
              "resource": {"id": "r2"}},
             {"type": "chat-action", "tool": "research_status", "actionId": "a4", "ok": True, "at": T0 + 31.9},
             {"type": "chat-action", "tool": "outline_cells", "actionId": "a5", "ok": True, "at": T0 + 58.6},
             {"type": "chat-action", "tool": "measure", "actionId": "a6", "ok": True, "at": T0 + 61.0}]
    run, summary, lines = board(bench, conv, [], verdicts)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["outline"]["visibleS"] == pytest.approx(2.7 - 1.5)  # open mic: from the end of the words
    assert rows["researcher"]["researchers"] == 1 and rows["status"]["statusRight"] is True
    for name in ("Mark after speech starts", "Event spoken when idle", "Reconnect", "do_in_hooke share"):
        assert result(lines, name) == "not measured", name
    assert summary["doInHooke"]["of"] == 0 and summary["toolFirstTry"]["n"] == 7
    assert not meets(list(lines.values()))


def test_markdown_names_the_failed_steps():
    bench, conv, inter, verdicts = good()
    conv.append(line("L2b", 4.6, "So that's 42 nuclei outlined."))
    run = score_run(bench, conv, inter, PLAN, verdicts)
    summary = summarise([run])
    text = standard.markdown({"about": {"voice": "gpt-realtime-2.1", "talk": "hold", "plan": "synthetic"},
                              "summary": summary, "scoreboard": scoreboard(summary), "runs": [run]})
    assert "Standard met: no" in text and "outcome said 2 times" in text and "| Feel | Release to first audio |" in text


def test_a_request_that_cannot_succeed_is_said_once_and_changes_nothing():
    """"Open the table called Growth curves": the failing call is the outcome, said once after it, nothing shown."""
    bench, conv, inter, verdicts = good()
    plan = copy.deepcopy(PLAN)
    plan["steps"][4]["expect"] = {"tools": ["show_measurements", "present"], "toolRequired": False, "fails": True,
                                  "outcome": True}
    inter.append(tool(5, "show_measurements", 41.8, 41.9, ok=False, error="no table called Growth curves"))
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    row = {r["id"]: r for r in run["steps"]}["explain"]
    assert row["outcomesSaid"] == 1 and not row["failures"]
    conv.append(line("L8b", 43.0, "There's no table called Growth curves."))
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    assert "outcome said 2 times" in {r["id"]: r for r in run["steps"]}["explain"]["failures"]


def test_an_idle_wait_has_no_first_audio_and_needs_no_answer():
    bench, conv, inter, verdicts = good()
    bench["steps"].append({"id": "idle", "kind": "idle", "say": "", "start": ms(65), "end": ms(78), "idle": True})
    plan = copy.deepcopy(PLAN)
    plan["steps"].append({"id": "idle", "expect": {"silent": True}})
    run, _, lines = board(bench, conv, inter, verdicts, plan)
    row = {r["id"]: r for r in run["steps"]}["idle"]
    assert row["firstAudioS"] is None and row["failures"] == [] and row["lines"] == []  # the event's line is no step's
    assert result(lines, "Event spoken when idle") == "pass"
