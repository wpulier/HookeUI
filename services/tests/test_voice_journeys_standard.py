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
        events.append({"t": ms(t), "e": "frames", "fps": 30, "delayP90": 60, "n": 30, "stage": "source"})
    for t in range(0, 90, 5):
        events.append({"t": ms(t), "e": "frames", "fps": 30, "delayP90": 70, "n": 30, "stage": "source"})
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
              "readyT": T0 + 81.2, "ms": 1150, "midSpeech": False},
             # Hands: the table opened with its cue first; the user took over during the multi step, in 60 ms.
             {"kind": "partner-drive", "id": "d1", "intent": "open", "tool": "show_measurements", "target": "tab:Measurements",
              "sentT": T0 + 12.40, "shownT": T0 + 12.70, "ok": True},
             {"kind": "partner-hands", "t": T0 + 13.5, "intent": "open", "tool": "show_measurements", "driveId": "d1",
              "version": 3, "revision": 10, "result": "verified", "expected": "verified"},
             {"kind": "partner-takeover", "t": T0 + 75.1, "why": "mouse", "version": 4, "atStudio": T0 + 75.0,
              "atServer": T0 + 75.06}]
    # A sign it heard you, 0.15 s after each release (decision 1).
    inter += [{"kind": "partner-heard", "n": n, "cueT": T0 + up + 0.15}
              for n, up in enumerate([1.6, 12.1, 23.1, 31.6, 41.6, 45.6, 57.1], start=1)]
    verdicts = {sid: {"answered": True, "meets_expectation": True, "partner_quality": 3}
                for sid in ("outline", "table", "researcher", "multi")}
    return bench, conv, inter, verdicts


def board(bench, conv, inter, verdicts=None, plan=PLAN, voice=None, baseline=None):
    run = score_run(bench, conv, inter, plan, verdicts, baseline)
    summary = summarise([run])
    return run, summary, {line["line"]: line for line in scoreboard(summary, voice)}


def result(lines, name):
    """A line's result; a timing line is reported (spec #80), "reported: within" or "reported: over", read here as the
    measurement's pass or FAIL."""
    got = lines[name]["result"]
    return {"reported: within": "pass", "reported: over": "FAIL"}.get(got, got)


def test_a_run_that_meets_the_standard_passes_every_line():
    run, summary, lines = board(*good())
    failed = {name: line for name, line in lines.items() if line["result"] != "pass" and line.get("gate", True)}
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


def test_slow_first_meaningful_words_fail():
    """Decision 1: the first meaningful words, not the first audio, at most 1.2 s median and 1.8 s p95."""
    bench, conv, inter, verdicts = good()
    for lid in ("L1", "L3", "L5", "L7"):  # four of seven answers start 2.5 s later
        for c in conv:
            if c["lineId"] == lid:
                c["at"] += 2.5
    assert result(board(bench, conv, inter, verdicts)[2], "First meaningful words") == "FAIL"


def test_filler_first_words_are_not_meaningful():
    """"Mm, okay..." at once, the content 0.7 s later: the first meaningful words are the content's."""
    bench, conv, inter, verdicts = good()
    conv[0] = line("L1", 1.8, "Mm, okay. Outlining the nuclei now.")
    bench["events"] += [{"t": ms(1.8), "e": "line", "id": "L1", "text": "Mm, okay."},
                        {"t": ms(2.5), "e": "line", "id": "L1", "text": "Mm, okay. Outlining the nuclei now."}]
    row = {r["id"]: r for r in board(bench, conv, inter, verdicts)[0]["steps"]}["outline"]
    assert row["meaningfulS"] == pytest.approx(2.5 - 1.6)
    assert standard.meaningful("Mm, okay.") is False and standard.meaningful("Okay, 42 nuclei.") is True


def test_a_sign_it_heard_you_within_300_ms_every_turn():
    bench, conv, inter, verdicts = good()
    assert result(board(bench, conv, inter, verdicts)[2], "A sign it heard you") == "pass"
    slow = [{**r, "cueT": r["cueT"] + 0.3} if r.get("kind") == "partner-heard" and r["n"] == 1 else r for r in inter]
    assert result(board(bench, conv, slow, verdicts)[2], "A sign it heard you") == "FAIL"
    missing = [r for r in inter if not (r.get("kind") == "partner-heard" and r["n"] == 2)]
    assert result(board(bench, conv, missing, verdicts)[2], "A sign it heard you") == "FAIL"  # a turn without one
    none = [r for r in inter if r.get("kind") != "partner-heard"]
    assert result(board(bench, conv, none, verdicts)[2], "A sign it heard you").startswith("not measured")
    bench["events"].append({"t": ms(1.7), "e": "ws-in", "m": {"type": "heard"}})  # the studio's own cue counts too
    assert {r["id"]: r for r in board(bench, conv, none, verdicts)[0]["steps"]}["outline"]["heardS"] == pytest.approx(0.1)


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


def test_do_in_hooke_share_is_reported_never_a_gate():
    """Handing the work to Hooke's brain is the design (PARTNER_ACTS "brain"): its share is reported, never a FAIL."""
    bench, conv, inter, verdicts = good()
    inter.append(tool(5, "do_in_hooke", 41.8, 43.0))
    plan = copy.deepcopy(PLAN)
    plan["steps"][4]["expect"] = {"tools": ["do_in_hooke"], "toolRequired": False}
    _, summary, lines = board(bench, conv, inter, verdicts, plan)
    assert summary["doInHooke"]["calls"] == 1 and result(lines, "do_in_hooke share") == "reported"


def test_false_done_is_a_verified_receipt_the_app_does_not_back():
    """The spec: false "done" is counted from receipts and app state, never judged. A receipt saying verified for the
    table with no studio report of it is one."""
    bench, conv, inter, verdicts = good()
    for r in inter:
        if r.get("kind") == "partner-tool" and r.get("name") == "show_measurements":
            r["confirmed"], r["reportT"] = False, None
    run, _, lines = board(bench, conv, inter, verdicts)
    assert {r["id"]: r for r in run["steps"]}["table"]["falseDone"] == 1 and result(lines, 'False "done"') == "FAIL"


def test_false_done_needs_receipts_not_the_judge():
    bench, conv, inter, verdicts = good()
    assert result(board(bench, conv, inter, None)[2], 'False "done"') == "pass"  # no judge: still scored
    no_receipts = [r for r in inter if r.get("kind") != "partner-hands"]
    assert result(board(bench, conv, no_receipts, verdicts)[2], 'False "done"') == "not measured: no receipts (no Hands)"


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
            e["fps"], e["delayP90"], e["n"] = 21, 180, 21
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


def test_gpt_live_run_scores_from_cards_and_says_the_one_brains_lines_do_not_apply():
    """GPT-Live: no partner records; its brain's tool cards (chat-action) give the tools and the screen's confirmations
    (a late ✓ is the same action shown again ok); marks, events, reconnects and do_in_hooke are the one brain's: not
    applicable to this voice, and never a pass."""
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
    run, summary, lines = board(bench, conv, [], verdicts, voice="gpt-live-1")
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["outline"]["visibleS"] == pytest.approx(2.7 - 1.5)  # open mic: from the end of the words
    assert rows["researcher"]["researchers"] == 1 and rows["status"]["statusRight"] is True
    for name in standard.ONE_BRAIN_ONLY:
        assert result(lines, name) == "not applicable to this voice", name
    assert result(lines, "Barge-in silent") != "not applicable to this voice"  # speaking over its open microphone
    assert summary["doInHooke"]["of"] == 0 and summary["toolFirstTry"]["n"] == 7
    assert not meets(list(lines.values()))


def test_markdown_names_the_failed_steps():
    bench, conv, inter, verdicts = good()
    conv.append(line("L2b", 4.6, "So that's 42 nuclei outlined."))
    run = score_run(bench, conv, inter, PLAN, verdicts)
    summary = summarise([run])
    text = standard.markdown({"about": {"voice": "gpt-realtime-2.1", "talk": "hold", "plan": "synthetic"},
                              "summary": summary, "scoreboard": scoreboard(summary), "runs": [run]})
    assert "Standard met: no" in text and "outcome said 2 times" in text and "| Feel | First meaningful words |" in text
    assert f"Scorer {standard.SCORER_VERSION}." in text


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


def test_an_outcome_is_timed_by_when_its_words_were_said():
    """GPT-Live, 2026-10-29 bench: one long line began before the outlines were confirmed and said "The outlines are
    on" after; the journal keeps only when a line began, the page saw its words arrive."""
    bench, conv, inter, verdicts = good()
    conv[1] = line("L2", 2.3, "I'm outlining them now. The outlines are on: 42 nuclei.")
    bench["events"] += [{"t": ms(2.3), "e": "line", "id": "L2", "text": "I'm outlining them now."},
                        {"t": ms(2.9), "e": "line", "id": "L2", "text": "I'm outlining them now. The outlines are on: 42 nuclei."}]
    row = {r["id"]: r for r in board(bench, conv, inter, verdicts)[0]["steps"]}["outline"]
    assert row["spokenS"] == pytest.approx(2.9 - 1.6) and row["outcomesSaid"] == 1


def test_server_audio_spans_end_at_partner_audio():
    """Over WebRTC partner-response is logged at response.done; its audio's end is its own partner-audio record."""
    inter = [{"kind": "partner-response", "responseId": "r1", "firstAudioT": 10.0, "audioEndT": 10.5},
             {"kind": "partner-audio", "responseId": "r1", "audioEndT": 14.0, "how": "stopped"},
             {"kind": "partner-response", "responseId": "r2", "firstAudioT": 20.0, "audioEndT": None}]
    assert standard.server_segments(inter) == [(10.0, 14.0), (20.0, 20.0)]


def test_a_turn_number_used_again_after_a_reload_stays_in_its_own_step():
    bench, conv, inter, verdicts = good()
    inter.append(tool(1, "tabs", 90.0, 90.1))  # turn 1 again, after the restart: never the outline step's call
    run, _, _ = board(bench, conv, inter, verdicts)
    assert {r["id"]: r for r in run["steps"]}["outline"]["tools"] == ["outline_cells"]


# --- the bench's own defects in the first GPT-Live baseline (live-1, 2026-10-29) ---------------------------------------

def api_rows(*rows):
    return {"e": "researchers", "rows": [{"source": "api", "group": "done", **r} for r in rows]}


def test_researchers_come_from_the_orchestrator_not_the_board():
    """live-1: the board was not drawn in the bench's studio, so status expected no one. The orchestrator's list says
    which researcher the researcher step made (by its created time), and its number."""
    bench, conv, inter, verdicts = good()
    bench["events"] = [e for e in bench["events"] if e.get("e") != "researchers"]
    inter = [r for r in inter if r.get("name") != "start_researcher"]  # nothing from the voice's own records either
    bench["events"].append({**api_rows({"rid": "r5461011", "number": 1, "created": T0 - 300, "finished": T0 - 200},
                                       {"rid": "r6001", "number": 2, "created": T0 + 23.9, "group": "working"}),
                            "t": ms(26)})
    run, _, lines = board(bench, conv, inter, verdicts)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["researcher"]["researchers"] == 1 and rows["researcher"]["researcherStartS"] == pytest.approx(0.8)
    assert rows["status"]["statusExpected"] == 2 and rows["status"]["statusRight"] is True
    assert all(r["researchers"] == 0 for r in run["steps"] if r["id"] != "researcher")  # the setup's is no step's


def test_two_researchers_listed_in_one_step_fail():
    bench, conv, inter, verdicts = good()
    bench["events"].append({**api_rows({"rid": "a", "number": 2, "created": T0 + 23.9},
                                       {"rid": "b", "number": 3, "created": T0 + 24.5}), "t": ms(26)})
    assert result(board(bench, conv, inter, verdicts)[2], "One researcher per request") == "FAIL"


def test_gpt_live_card_names_its_researcher_without_the_orchestrators_list():
    """A run from before the player read the orchestrator (live-1): the start_researcher card's research-run output
    names the researcher and its summary its number."""
    bench, conv, _, verdicts = good()
    bench["events"] = [e for e in bench["events"] if e.get("e") != "researchers"]
    conv.append({"type": "chat-action", "tool": "start_researcher", "actionId": "a3", "ok": True, "at": T0 + 23.9,
                 "summary": "Researcher 2 is making a research note at quick effort (in about 75 s)",
                 "outputs": [{"kind": "resource", "resource": {"kind": "research-run", "researcherId": "r5461011"}}]})
    run, _, _ = board(bench, conv, [r for r in good()[2] if r.get("kind") != "partner-tool"], verdicts)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["researcher"]["researcherIds"] == ["r5461011"] and rows["status"]["statusExpected"] == 2


def test_status_is_not_checked_when_the_researcher_step_started_none():
    """That step's failure is counted there; the status step has no right answer and is not failed for it."""
    bench, conv, inter, verdicts = good()
    inter = [r for r in inter if r.get("name") != "start_researcher"]
    run, summary, lines = board(bench, conv, inter, verdicts)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["researcher"]["failures"] and rows["status"]["statusRight"] is None and not rows["status"]["failures"]
    assert summary["status"]["unchecked"] == 1


def test_live_view_counts_only_seconds_with_something_on_the_stage():
    """live-1: every new Project opened on the empty stage, so no frames came: no live view, not a 0 fps one."""
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "frames" and e["t"] >= ms(40):
            e.update(fps=0, n=0, delayP90=None, stage="empty")  # the Scratch Project, nothing to view
    _, summary, lines = board(bench, conv, inter, verdicts)
    assert summary["liveView"]["fps"] == 30 and result(lines, "Live view frame rate and delay unchanged") == "pass"
    for e in bench["events"]:
        if e.get("e") == "frames":
            e.update(fps=0, n=0, delayP90=None, stage="empty")
    _, summary, lines = board(bench, conv, inter, verdicts)
    assert result(lines, "Live view frame rate and delay unchanged") == "not measured: no live view on the stage"


def test_a_live_view_that_freezes_with_a_source_on_stage_fails():
    bench, conv, inter, verdicts = good()
    for e in bench["events"]:
        if e.get("e") == "frames" and e["t"] >= ms(0):
            e.update(fps=0, n=0, delayP90=None)  # stage still "source": the view stopped
    assert result(board(bench, conv, inter, verdicts)[2], "Live view frame rate and delay unchanged") == "FAIL"


def test_the_setups_frames_are_the_baseline_when_none_came_before_talk():
    bench, conv, inter, verdicts = good()
    bench["events"] = [e for e in bench["events"] if not (e.get("e") == "frames" and e["t"] < ms(0))]
    setup = [{"t": ms(-400 + i), "e": "frames", "fps": 25, "delayP90": 65, "n": 25, "stage": "source"} for i in range(10)]
    assert result(board(bench, conv, inter, verdicts)[2], "Live view frame rate and delay unchanged") == \
        "not measured: no live view before Talk to compare with"
    _, summary, lines = board(bench, conv, inter, verdicts, baseline=setup)
    assert summary["liveView"]["baselineFps"] == 25 and result(lines, "Live view frame rate and delay unchanged") == "pass"


def test_a_restart_checks_only_what_was_there_before_it():
    """live-1: the journey was left in Scratch by step 1, so the restart's "Nuclei" and Tab checks failed again. A check
    of something not there before the restart is not applicable (ok None); one that was there and is gone fails."""
    bench, conv, inter, verdicts = good()
    bench["events"] += [{"t": ms(41.0), "e": "check", "step": "explain", "name": "tab:Measurements", "ok": None,
                         "detail": "no Tab Measurements before the restart"},
                        {"t": ms(41.0), "e": "check", "step": "explain", "name": "project:Nuclei", "ok": True}]
    run, summary, _ = board(bench, conv, inter, verdicts)
    row = {r["id"]: r for r in run["steps"]}["explain"]
    assert row["checks"] == {"tab:Measurements": None, "project:Nuclei": True} and not row["failures"]


def test_a_step_the_bench_put_back_is_named_and_scored_as_it_was():
    bench, conv, inter, verdicts = good()
    bench["events"].append({"t": ms(9), "e": "recovered", "step": "outline", "what": "project:Nuclei", "from": "Scratch",
                            "ok": True})
    run, _, _ = board(bench, conv, inter, verdicts)
    assert {r["id"]: r for r in run["steps"]}["outline"]["recovered"] == [{"what": "project:Nuclei", "from": "Scratch", "ok": True}]


def test_one_brain_records_as_the_server_writes_them():
    """voice/openai_realtime.py (a8e3ce2): over WebRTC partner-response has audioEndT null and partner-audio ends it; a
    proactive line's eventT is when it was sent, so a researcher's event is timed by when it finished (the orchestrator's
    list); partner-tool carries the tool bridge's facts (screenChange, reportT, confirmed, rid, ridT)."""
    bench, conv, inter, verdicts = good()
    bench["events"] = [e for e in bench["events"] if e.get("e") != "audio"]  # heard only through the server's records
    inter = [r for r in inter if r.get("kind") not in ("partner-response", "partner-proactive")]
    for i, (on, off) in enumerate([(2.0, 2.9), (2.95, 4.5), (12.5, 13.3), (13.6, 15.0), (23.5, 24.5), (24.6, 26.0),
                                    (32.0, 34.0), (42.0, 44.1), (46.0, 47.0), (57.5, 58.4), (58.9, 60.2), (61.1, 63.0)]):
        inter += [{"kind": "partner-response", "responseId": f"resp{i}", "cause": "turn", "firstAudioT": T0 + on,
                   "audioEndT": None, "transcript": ""},
                  {"kind": "partner-audio", "responseId": f"resp{i}", "audioEndT": T0 + off, "how": "stopped"}]
    inter += [{"kind": "partner-response", "responseId": "pro", "cause": "proactive", "firstAudioT": T0 + 70.8,
               "audioEndT": None, "transcript": "Researcher 2 is done."},
              {"kind": "partner-audio", "responseId": "pro", "audioEndT": T0 + 72.0, "how": "stopped"},
              {"kind": "partner-proactive", "event": "researcher-done", "ref": "r2", "eventT": T0 + 70.2,
               "userBusy": False, "responseCreatedT": T0 + 70.2, "markTarget": "researcher:r2"}]
    bench["events"].append({**api_rows({"rid": "r2", "number": 2, "created": T0 + 23.9, "finished": T0 + 69.5}),
                            "t": ms(71)})
    run, summary, lines = board(bench, conv, inter, verdicts)
    assert run["audioFrom"] == "server"
    assert summary["firstAudio"]["n"] == 7 and summary["silence"]["max"] == pytest.approx(0.9)
    assert run["proactive"][0]["spokenS"] == pytest.approx(70.8 - 69.5)  # from when the researcher finished
    assert result(lines, "Event spoken when idle") == "pass"


def test_a_restart_keeps_the_project_the_journal_was_in():
    """Any run (the acoustic replay has no page): the journal's Project before the restart is the one after it. The
    first player's fixed "project:Nuclei" and its board-read researcher check are read that way too."""
    bench, conv, inter, verdicts = good()
    bench["steps"].insert(5, {"id": "reopen", "kind": "restart", "say": "", "start": ms(43.0), "end": ms(43.0), "restart": True})
    for c in conv:
        c["day"] = "2026-10-29.3"
    bench["events"] += [{"t": ms(43.5), "e": "check", "step": "reopen", "name": "project:Nuclei", "ok": False,
                         "detail": "open: Scratch"},
                        {"t": ms(43.5), "e": "check", "step": "reopen", "name": "researcher:latest", "ok": False,
                         "detail": "rows: ; expected undefined"}]
    plan = copy.deepcopy(PLAN)
    plan["steps"].insert(5, {"id": "reopen", "expect": {"silent": True}})
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    row = {r["id"]: r for r in run["steps"]}["reopen"]
    assert row["checks"] == {"project:Nuclei": True, "researcher:latest": None} and not row["failures"]
    for c in conv:
        if c["at"] > T0 + 43.0:
            c["day"] = "2026-10-29.4"  # reopened somewhere else
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    assert {r["id"]: r for r in run["steps"]}["reopen"]["checks"]["project:Nuclei"] is False


def test_an_idle_wait_or_a_restart_is_nobody_talking():
    from bench.voice_journeys.standard import holds
    steps = [{"id": "status", "say": "What's our researcher doing?", "start": 10.0, "end": 12.0},
             {"id": "idle", "say": "", "idle": True, "start": 14.0, "end": 35.0},
             {"id": "reopen", "say": "", "restart": True, "start": 36.0, "end": 50.0},
             {"id": "held", "say": "Outline them.", "down": 60.0, "up": 61.5, "start": 60.2, "end": 61.3}]
    spans = holds(steps)
    assert [round(a, 1) for a, _ in spans] == [10.0, 60.0]  # the idle wait and the restart are not the user talking



def test_partner_quality_gates_the_overall_line():
    """The spec, section 1: the judge's partner quality, a mean of at least 2.5 and no step under 2."""
    bench, conv, inter, verdicts = good()
    assert result(board(bench, conv, inter, verdicts)[2], "Partner quality (0-3, judged)") == "pass"
    for sid, q in (("outline", 3), ("table", 3), ("researcher", 1), ("multi", 3)):
        verdicts[sid]["partner_quality"] = q
    run, summary, lines = board(bench, conv, inter, verdicts)
    line = lines["Partner quality (0-3, judged)"]
    assert line["result"] == "FAIL" and line["value"] == "mean 2.50 over 4 steps; under 2: 1 (researcher)"
    verdicts["outline"]["partner_quality"] = "great"  # not a number: not counted
    assert board(bench, conv, inter, verdicts)[1]["quality"]["n"] == 3


def test_a_status_step_can_name_the_researcher_the_user_named():
    """"What's researcher 2 doing?": the answer names researcher 2, whichever started last."""
    bench, conv, inter, verdicts = good()
    plan = copy.deepcopy(PLAN)
    plan["steps"][3]["expect"]["status"] = 1
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    row = {r["id"]: r for r in run["steps"]}["status"]
    assert row["statusExpected"] == 1 and row["statusRight"] is False  # it said researcher 2


def test_think_is_a_hand_off_whose_result_belongs_to_the_step_that_asked():
    """claude/p25-think: think returns at once; the frontier mind's own tool calls are inside partner-think; its result
    is said once later, here during the next step. That line is the asking step's outcome, not the next step's."""
    bench, conv, inter, verdicts = good()
    inter = [r for r in inter if not (r.get("kind") == "partner-tool" and r.get("n") == 7)]  # the multi step, handed off
    inter += [{**tool(7, "think", 57.3, 57.4), "callId": "c-think"},
              {"kind": "partner-think", "task": "t1", "callId": "c-think", "n": 7, "ok": True, "doneT": T0 + 64.0,
               "tools": [{"name": "outline_cells", "ok": True, "confirmed": True},
                         {"name": "measure", "ok": True, "confirmed": True}]},
              {"kind": "partner-think-said", "task": "t1", "n": 8, "responseCreatedT": T0 + 66.0},
              {"kind": "partner-response", "responseId": "th1", "cause": "think", "firstAudioT": T0 + 66.3,
               "audioEndT": T0 + 68.0, "transcript": "Done: 42 nuclei outlined; the median area is 210 square microns."}]
    conv = [c for c in conv if c["lineId"] not in ("L11", "L12")]
    conv.append(line("L12", 66.3, "Done: 42 nuclei outlined; the median area is 210 square microns."))
    bench["steps"].append(step("after", 65.0, 65.1, 66.5, 66.6))  # the user's next request starts before it is said
    bench["events"] += audio(66.3, 68.0) + [talk(8, 65.0)]
    plan = copy.deepcopy(PLAN)
    plan["steps"][6]["expect"]["tools"] = ["outline_cells", "measure", "think"]
    plan["steps"].append({"id": "after", "expect": {"tools": [], "toolRequired": False}})
    verdicts["multi"] = {"claims_on_screen": [], "answered": True}
    run, summary, lines = board(bench, conv, inter, verdicts, plan)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["multi"]["firstTool"] == "think" and rows["multi"]["thinkTools"] == ["outline_cells", "measure"]
    assert rows["multi"]["outcomeS"] == pytest.approx(66.3 - 57.1) and rows["multi"]["outcomesSaid"] == 1
    assert "Done: 42 nuclei" not in " ".join(rows["after"]["lines"])
    # think is the intended route for multi-step work: never on the fallback's 5% cap, reported by kind of step.
    assert rows["multi"]["toolRight"] is True and summary["doInHooke"]["calls"] == 0
    assert result(lines, "do_in_hooke share") == "reported"
    assert lines["think share"]["result"] == "reported" and lines["think share"]["value"] == "1 of 5 (20%); by step kind: act 1 of 5"
    assert lines["think share"].get("gate") is False  # think's share is reported, never a gate


def test_think_on_a_quick_step_is_the_wrong_tool():
    """"Switch back to the main tab": think where the plan expects tabs fails the first-try line, and shows as think
    where not expected."""
    bench, conv, inter, verdicts = good()
    inter.insert(1, {**tool(2, "think", 12.2, 12.3), "callId": "c2"})  # before show_measurements
    run, summary, lines = board(bench, conv, inter, verdicts)
    assert {r["id"]: r for r in run["steps"]}["table"]["toolRight"] is False
    assert result(lines, "Right tool on the first try") == "FAIL"
    assert summary["think"]["unexpected"] == ["table"] and "where not expected: 1 (table)" in lines["think share"]["value"]



def test_a_status_step_can_name_the_first_researcher_a_step_started():
    """think-broad-1: "make two researchers, one compares the sizes, the other looks for dividing cells" was numbered 1
    and 2 by the app, not 2 and 3 as the plan assumed. "The size one" is the first that step started, by its number."""
    bench, conv, inter, verdicts = good()
    inter = [r for r in inter if r.get("name") != "start_researcher"]
    bench["events"].append({**api_rows({"rid": "r668081", "number": 1, "created": T0 + 23.6},
                                       {"rid": "r668082", "number": 2, "created": T0 + 23.9}), "t": ms(26)})
    plan = copy.deepcopy(PLAN)
    plan["steps"][2]["expect"]["researchers"] = 2
    plan["steps"][3]["expect"]["status"] = {"from": "researcher", "index": 0}
    conv[6] = line("L7", 32.0, "Researcher 1 is comparing the sizes.")
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["researcher"]["researcherNumbers"] == [1, 2]
    assert rows["status"]["statusExpected"] == 1 and rows["status"]["statusRight"] is True


def test_a_receipt_for_a_note_needs_its_success_not_a_studio_report():
    """think-broad-2: "Researcher 2 got the message" after researcher_note: no studio report can come; the receipt
    stands on the call having succeeded (and is false if it did not)."""
    bench, conv, inter, verdicts = good()
    inter += [tool(5, "researcher_note", 41.7, 41.9),
              {"kind": "partner-hands", "t": T0 + 41.95, "intent": "note", "tool": "researcher_note", "result": "verified", "expected": "verified"}]
    row = lambda: {r["id"]: r for r in board(bench, conv, inter, verdicts)[0]["steps"]}["explain"]
    assert row()["falseDone"] == 0
    inter[-2]["ok"] = False
    assert row()["falseDone"] == 1


def test_a_note_or_a_setting_receipt_is_backed_by_the_apps_own_state():
    """Confirmed by the state the app reports (/api/research's notes, the settings the studios are sent)."""
    bench, conv, inter, verdicts = good()
    inter += [tool(5, "researcher_note", 41.7, 41.9),
              {"kind": "partner-hands", "t": T0 + 41.95, "intent": "note", "tool": "researcher_note", "result": "verified", "expected": "verified"}]
    rows = lambda notes: {"rows": [{"source": "api", "rid": "r2", "number": 2, "created": T0 + 23.9, "notes": notes}]}
    bench["events"] += [{"t": ms(41.0), "e": "researchers", **rows(0)}, {"t": ms(41.95), "e": "researchers", **rows(1)}]
    row = lambda: {r["id"]: r for r in board(bench, conv, inter, verdicts)[0]["steps"]}["explain"]
    assert row()["falseDone"] == 0
    bench["events"][-1].update(rows(0))  # ok said, but no note reached the researcher
    assert row()["falseDone"] == 1
    inter[-2]["name"], inter[-1]["tool"] = "set_default", "set_default"
    bench["events"] += [{"t": ms(-5), "e": "settings", "first": True, "changed": ["brain"]},
                        {"t": ms(41.9), "e": "settings", "first": False, "changed": ["brain"]}]
    assert row()["falseDone"] == 0
    bench["events"].pop()  # the setting never changed
    assert row()["falseDone"] == 1


def test_a_verified_receipt_for_a_visible_change_needs_the_studios_report():
    bench, conv, inter, verdicts = good()
    inter += [tool(5, "highlight", 41.7, 41.9, screen=True),  # no report came
              {"kind": "partner-hands", "t": T0 + 42.0, "intent": "highlight", "tool": "highlight", "target": "outline:o3",
               "result": "verified", "expected": "verified"}]
    assert {r["id"]: r for r in board(bench, conv, inter, verdicts)[0]["steps"]}["explain"]["falseDone"] == 1


def test_rescoring_a_kept_run_with_the_same_scorer_gives_the_same_result():
    """The spec: the scorer is fixed and versioned per run; a kept run scored twice gives the same result."""
    bench, conv, inter, verdicts = good()
    first = json.dumps(score_run(copy.deepcopy(bench), conv, inter, PLAN, copy.deepcopy(verdicts)), sort_keys=True)
    again = json.dumps(score_run(copy.deepcopy(bench), conv, inter, PLAN, copy.deepcopy(verdicts)), sort_keys=True)
    assert first == again


def test_a_background_call_is_done_when_its_result_lands_and_belongs_to_the_step_that_asked():
    """claude/p25-think: reference_photo returns at once ({"started": true}) with its task; partner-background says when
    the photo landed; its result, said during the next step, is the asking step's outcome."""
    bench, conv, inter, verdicts = good()
    inter = [r for r in inter if not (r.get("kind") == "partner-tool" and r.get("n") == 2)]  # the table step, as a photo
    inter += [{**tool(2, "reference_photo", 12.4, 12.5), "callId": "c-photo", "task": "t-photo"},
              {"kind": "partner-background", "task": "t-photo", "name": "reference_photo", "n": 2, "ok": True,
               "points": ["window:w-1"], "doneT": T0 + 21.0},
              {"kind": "partner-think-said", "task": "t-photo", "n": 3, "responseCreatedT": T0 + 21.2},
              {"kind": "partner-response", "responseId": "ph", "cause": "think", "firstAudioT": T0 + 21.5,
               "audioEndT": T0 + 22.5, "transcript": "Here's a dividing nucleus for comparison."}]
    conv = [c for c in conv if c["lineId"] not in ("L4",)]
    conv.append(line("LP", 21.5, "Here's a dividing nucleus for comparison."))
    bench["events"] += audio(21.5, 22.5)
    plan = copy.deepcopy(PLAN)
    plan["steps"][1]["expect"] = {"tools": ["reference_photo"], "visible": "a reference picture", "outcome": True}
    run, _, _ = board(bench, conv, inter, verdicts, plan)
    rows = {r["id"]: r for r in run["steps"]}
    assert rows["table"]["visibleS"] == pytest.approx(21.0 - 12.1) and rows["table"]["outcomesSaid"] == 1
    assert "Here's a dividing nucleus" not in " ".join(rows["researcher"]["lines"])


def drive_records(stale=False, slow=False, hidden=False):
    out = [{"kind": "partner-drive", "id": "d1", "intent": "open", "tool": "show_measurements", "target": "tab:Measurements",
            "sentT": T0 + 12.40, "shownT": T0 + 12.70, "ok": True},
           {"kind": "partner-hands", "t": T0 + 13.5, "intent": "open", "tool": "show_measurements", "driveId": "d1",
            "version": 3, "revision": 10, "result": "verified", "expected": "verified"},
           {"kind": "partner-takeover", "t": T0 + 75.1, "why": "mouse", "version": 4, "atStudio": T0 + 75.0,
            "atServer": T0 + (75.3 if slow else 75.06)}]
    if hidden:
        out.append({"kind": "partner-drive", "id": "d2", "intent": "highlight", "tool": "highlight", "target": "outline:o3",
                    "sentT": T0 + 30.0, "shownT": None, "ok": False, "why": "hidden"})
    if stale:
        out.append({"kind": "partner-hands", "t": T0 + 76.0, "intent": "close", "tool": "present", "version": 3, "result": "verified", "expected": "verified"})
    return out


HANDS = ("partner-drive", "partner-hands", "partner-takeover")


def test_drive_gates_a_cue_for_every_action_and_nothing_after_a_takeover():
    """The spec (part-2.5-live-partner-spec.md, section 1): every action visible as it happens, a takeover stops it
    within 150 ms, no queued action runs after. Actions verified and cues never ended are details within the line."""
    bench, conv, inter, verdicts = good()
    bench["events"] += [{"t": ms(12.4), "e": "ws-in", "m": {"type": "drive", "id": "d1"}},
                        {"t": ms(13.6), "e": "ws-in", "m": {"type": "drive-end", "id": "d1", "outcome": "verified"}}]
    inter = [r for r in inter if r.get("kind") not in HANDS]
    line = lambda **k: board(bench, conv, inter + drive_records(**k), verdicts)[2]["Drive"]
    good_line = line()
    assert good_line["result"] == "pass" and "studio to server max 0.06 s" in good_line["value"]
    assert "actions verified 1 of 1" in good_line["value"]
    assert line(stale=True)["result"] == "FAIL" and line(hidden=True)["result"] == "FAIL"
    slow = line(slow=True)  # the takeover's milliseconds are reported (spec #80), the line still passes
    assert slow["result"] == "pass" and "studio to server max 0.30 s, reported" in slow["value"]
    no_takeover = board(bench, conv, inter + drive_records()[:2], verdicts)[2]["Drive"]
    assert no_takeover["result"] == "not measured: no takeover in the run"
    assert board(bench, conv, inter + drive_records(), verdicts, voice="gpt-live-1")[2]["Drive"]["result"] == \
        "not applicable to this voice"


def test_a_takeover_step_is_scored_on_stopping():
    bench, conv, inter, verdicts = good()
    plan = copy.deepcopy(PLAN)
    plan["steps"][4]["expect"] = {"tools": [], "toolRequired": False, "takeover": True}
    row = lambda i: {r["id"]: r for r in board(bench, conv, i, verdicts, plan)[0]["steps"]}["explain"]
    assert "no takeover reached the server" in row(inter)["failures"]
    taken = [{"kind": "partner-takeover", "t": T0 + 42.2, "why": "mouse", "version": 5, "atStudio": T0 + 42.1, "atServer": T0 + 42.18}]
    assert row(inter + taken)["takeover"] == [0.08] and not row(inter + taken)["failures"]



def test_the_gates_come_first_and_every_timing_is_reported():
    """Spec #80 (Will: "our benchmark subjective matters most"): the gates, in order, are partner quality, false
    "done" from receipts, receipts, and Drive's truthful parts; every timing is reported, never a gate."""
    bench, conv, inter, verdicts = good()
    for at in (2.0, 12.5, 23.5, 32.0):
        shift_audio_on(bench, at, at + 2.0)
    for c in conv:
        c["at"] += 3.0  # every answer slow
    run, summary, lines = board(bench, conv, inter, verdicts)
    board_ = scoreboard(summary)
    assert [l["line"] for l in board_[:4]] == ["Partner quality (0-3, judged)", 'False "done"', "Receipts", "Drive"]
    for name in standard.TIMING_LINES:
        assert lines[name].get("gate") is False and lines[name]["result"].startswith(("reported", "not measured")), name
    assert lines["First meaningful words"]["result"] == "reported: over"
    assert meets(board_) is (all(l["result"] == "pass" for l in board_ if l.get("gate", True)))


def test_receipts_gate_every_action_ending_in_a_checked_receipt():
    bench, conv, inter, verdicts = good()
    assert result(board(bench, conv, inter, verdicts)[2], "Receipts") == "pass"
    unreceipted = inter + [{"kind": "partner-drive", "id": "d9", "intent": "close", "tool": "present", "target": "window:w1",
                            "sentT": T0 + 30.0, "shownT": T0 + 30.2, "ok": True}]
    assert result(board(bench, conv, unreceipted, verdicts)[2], "Receipts") == "FAIL"
    unchecked = [{k: v for k, v in r.items() if k != "expected"} if r.get("kind") == "partner-hands" else r for r in inter]
    assert result(board(bench, conv, unchecked, verdicts)[2], "Receipts") == "FAIL"


# --- the watcher (orchestrator/view_watch.py) ------------------------------------------------------------------------
WATCH_PLAN = {**PLAN, "steps": PLAN["steps"] + [{"id": "watch-pan", "expect": {"watch": ["slide-moved"]}},
                                                {"id": "watch-steady", "expect": {"watch": []}}]}


def watched(spoken_at=1.4, false_alarms=0, raised=True):
    """good() with two idle watch steps after it: the slide moved (one event said) and ten steady minutes."""
    bench, conv, inter, verdicts = good()
    bench["steps"] += [{"id": "watch-pan", "kind": "idle", "say": "", "start": ms(100), "end": ms(125), "idle": True},
                       {"id": "watch-steady", "kind": "idle", "say": "", "start": ms(130), "end": ms(730), "idle": True}]
    if raised:
        inter += [{"kind": "watch-event", "t": T0 + 110, "event": "slide-moved", "id": "v1", "eventT": T0 + 110.0,
                   "sinceT": T0 + 109.4, "delivery": "idle", "to": "voice", "pointAt": ["frame"]},
                  {"kind": "partner-proactive", "t": T0 + 110.1, "event": "slide-moved", "ref": "v1",
                   "eventT": T0 + 110.05, "userBusy": False, "responseCreatedT": T0 + 110.1, "markTarget": "frame"}]
        bench["events"] += audio(110 + spoken_at, 112.8)
    for k in range(false_alarms):
        inter.append({"kind": "watch-event", "t": T0 + 200 + k, "event": "focus-drifted", "id": f"f{k}",
                      "eventT": T0 + 200.0 + 30 * k, "delivery": "context", "to": "voice"})
    return bench, conv, inter, verdicts


def test_the_watch_lines_pass_when_the_event_is_said_within_two_seconds_and_nothing_else_is_raised():
    run, summary, lines = board(*watched(), plan=WATCH_PLAN)
    assert result(lines, "Watch: an event said when idle") == "pass"
    assert result(lines, "Watch: false alarms per hour") == "pass"
    assert summary["watch"]["spokenMax"] == pytest.approx(1.4) and summary["watch"]["steadyMinutes"] == 10.0
    assert summary["watch"]["falsePerHour"] == 0.0


def test_a_watch_event_said_late_unsaid_or_missed_fails():
    assert result(board(*watched(spoken_at=2.6), plan=WATCH_PLAN)[2], "Watch: an event said when idle") == "FAIL"
    bench, conv, inter, verdicts = watched()
    inter[:] = [r for r in inter if r.get("kind") != "partner-proactive" or r.get("ref") != "v1"]
    assert result(board(bench, conv, inter, verdicts, plan=WATCH_PLAN)[2], "Watch: an event said when idle") == "FAIL"
    _, summary, lines = board(*watched(raised=False), plan=WATCH_PLAN)
    assert summary["watch"]["missed"] == ["watch-pan: slide-moved"]
    assert result(lines, "Watch: an event said when idle") == "FAIL"


def test_events_on_a_steady_stretch_are_false_alarms_per_hour():
    _, summary, lines = board(*watched(false_alarms=2), plan=WATCH_PLAN)
    assert summary["watch"]["falseAlarms"] == 2 and summary["watch"]["falsePerHour"] == pytest.approx(12.0)
    assert result(lines, "Watch: false alarms per hour") == "FAIL"


def test_a_plan_without_watch_steps_has_no_watch_lines_and_gpt_live_is_not_measured_on_saying_them():
    lines = board(*good())[2]
    assert not [name for name in lines if name.startswith("Watch:")]
    bench, conv, inter, verdicts = watched()
    bench["voice"] = "gpt-live-1"
    lines = board(bench, conv, inter, verdicts, plan=WATCH_PLAN, voice="gpt-live-1")[2]
    assert result(lines, "Watch: an event said when idle") == "not applicable to this voice"
    assert result(lines, "Watch: false alarms per hour") == "pass"  # the watcher is the app's, whichever voice


def test_with_watch_quiet_a_slide_move_told_silently_is_no_said_line_but_a_missed_one_still_fails():
    """part-2.5-broad runs with the default WATCH quiet: the slide's move is told silently (the judge asks what changed);
    the said line appears only when something was to be said, or an expected event was never raised."""
    bench, conv, inter, verdicts = watched()
    for r in inter:
        if r.get("kind") == "watch-event":
            r["delivery"] = "context"
    inter[:] = [r for r in inter if r.get("kind") != "partner-proactive" or r.get("ref") != "v1"]
    lines = board(bench, conv, inter, verdicts, plan=WATCH_PLAN)[2]
    assert "Watch: an event said when idle" not in lines and result(lines, "Watch: false alarms per hour") == "pass"
    lines = board(*watched(raised=False), plan=WATCH_PLAN)[2]
    assert result(lines, "Watch: an event said when idle") == "FAIL"


def test_an_event_cut_off_and_brought_back_is_one_line():
    """voice/events.py: a result the user cut off comes back once ("as I started to say…"); its second record says
    `again`, and it is the same line, not a second one. Two first sayings still fail (test_event_said_twice_fails)."""
    bench, conv, inter, verdicts = good()
    first = [r for r in inter if r.get("kind") == "partner-proactive"][0]
    first["eventId"] = "ev-1"
    inter.append({**first, "t": T0 + 75, "responseCreatedT": T0 + 75.0, "eventT": T0 + 75.0, "again": 1})
    inter.append({"kind": "partner-response", "t": T0 + 77, "cause": "proactive", "firstAudioT": T0 + 75.3,
                  "audioEndT": T0 + 76.5, "events": ["ev-1"], "transcript": "As I started to say, two size groups."})
    bench["events"] += audio(75.3, 76.5)
    run, _, lines = board(bench, conv, inter, verdicts)
    assert [e["lines"] for e in run["proactive"]] == [1], "one event, one line"
    assert result(lines, "Never over the user, one line, pointing") == "pass"


def test_a_think_result_cut_off_and_brought_back_is_said_once():
    """A think result whose first saying the user cut off and that came back in the next pause: two lines, one
    outcome. The line that brought it back is matched by the events its response carried."""
    bench, conv, inter, verdicts = good()
    inter = [r for r in inter if not (r.get("kind") == "partner-tool" and r.get("n") == 7)]
    inter += [{**tool(7, "think", 57.3, 57.4), "callId": "c-think"},
              {"kind": "partner-think", "task": "t1", "callId": "c-think", "n": 7, "ok": True, "doneT": T0 + 59.0,
               "tools": [{"name": "outline_cells", "ok": True, "confirmed": True},
                         {"name": "measure", "ok": True, "confirmed": True}]},
              {"kind": "partner-think-said", "task": "t1", "n": 7, "eventId": "ev-t1", "responseCreatedT": T0 + 59.5,
               "again": 0},
              {"kind": "partner-response", "responseId": "th1", "cause": "think", "firstAudioT": T0 + 59.8,
               "audioEndT": T0 + 60.3, "events": ["ev-t1"], "transcript": "Done: 42 nuclei"},
              {"kind": "partner-think-said", "task": "t1", "n": 7, "eventId": "ev-t1", "responseCreatedT": T0 + 61.0,
               "again": 1},
              {"kind": "partner-response", "responseId": "th2", "cause": "think", "firstAudioT": T0 + 61.2,
               "audioEndT": T0 + 63.0, "events": ["ev-t1"],
               "transcript": "As I started to say, the median area is 210 square microns."}]
    conv = [c for c in conv if c["lineId"] not in ("L10", "L11", "L12")]
    conv += [line("L12a", 59.8, "Done: 42 nuclei"),
             line("L12b", 61.2, "As I started to say, the median area is 210 square microns.")]
    bench["events"] = [e for e in bench["events"] if not (e.get("e") == "audio" and ms(57.4) <= e["t"] <= ms(63.1))]
    bench["events"] += audio(59.8, 60.3) + audio(61.2, 63.0)
    plan = copy.deepcopy(PLAN)
    plan["steps"][6]["expect"]["tools"] = ["outline_cells", "measure", "think"]
    verdicts["multi"] = {"claims_on_screen": [], "answered": True}
    run, _, lines = board(bench, conv, inter, verdicts, plan)
    row = {r["id"]: r for r in run["steps"]}["multi"]
    assert row["outcomesSaid"] == 1, row
    assert not [f for f in row["failures"] if "outcome said" in f]


def test_a_hand_offs_row_gives_the_brains_steps_and_when_the_studio_confirmed_them():
    """will-workspace, 29 Oct: the brain outlined the stoma and the studio confirmed it, but the bench read only the
    voice's own calls and chat-action cards, so the step was "first tool do_in_hooke" and "not confirmed on screen". A
    hand-off row's steps are the brain's tools, confirmed when the row settled; a step sent but not seen is not."""
    from bench.voice_journeys.standard import card_calls

    conv = [{"type": "handoff", "at": 10.0, "state": "working", "steps": None, "startedAt": 5.0},
            {"type": "handoff", "at": 12.0, "state": "done", "startedAt": 5.0, "steps": [
                {"tool": "find", "ok": True, "actionId": "a1"},
                {"tool": "present", "ok": True, "actionId": "a2",
                 "outcome": {"status": "unobserved", "failure": {"code": "STUDIO_UNREPORTED"}}},
                {"tool": "outline_cells", "ok": None, "actionId": "a3"}]},
            {"type": "chat-action", "at": 20.0, "tool": "outline_cells", "ok": True, "actionId": "a3"}]
    calls = {c.name: c for c in card_calls(conv, 0, 30)}
    assert [c.name for c in card_calls(conv, 0, 30)][0] == "find", "the brain's first step is the first tool"
    assert calls["find"].confirmed_at == 12.0
    assert calls["present"].confirmed_at is None, "sent but not seen on screen is not confirmed"
    assert calls["outline_cells"].ok is True and calls["outline_cells"].confirmed_at == 20.0, "a late step by its card"
