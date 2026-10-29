"""The journey bench's runner, comparison and plans (bench/voice_journeys/run.py, compare.py, plans/), with no stack,
no browser and no model: a paid run reserves its worst case first and refuses when the budget does, settles from what
the stack recorded (else the worst case), re-scores kept runs, and the shipped plans name real tools."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bench.voice_journeys import compare, run, stack
from tests.test_voice_journeys_standard import PLAN, good

BUDGET = """import json, sys
from pathlib import Path
calls = Path(__file__).with_name("calls.jsonl")
with calls.open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "reserve":
    if float(sys.argv[3]) > {limit}:
        print("REFUSED", file=sys.stderr)
        sys.exit(3)
    print("res-1")
"""


def budget(tmp_path: Path, limit: float) -> tuple[str, Path]:
    script = tmp_path / "budget.py"
    script.write_text(BUDGET.replace("{limit}", str(limit)))
    return str(script), tmp_path / "calls.jsonl"


def calls(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def kept_run(out: Path, verdicts=True) -> None:
    """A run folder as run.py leaves it: the player's state, the judge's verdicts and the stack's logs."""
    bench, conv, inter, judged = good()
    (out / "run-1").mkdir(parents=True)
    (out / "run-1" / "bench.json").write_text(json.dumps(bench))
    if verdicts:
        (out / "run-1" / "verdicts.json").write_text(json.dumps(judged))
    logs = out / "home" / "logs"
    logs.mkdir(parents=True)
    (logs / "conversation.jsonl").write_text("\n".join(json.dumps(c) for c in conv))
    (logs / "interactions.jsonl").write_text("\n".join(json.dumps(r) for r in inter))


def test_worst_case_counts_spoken_steps_researchers_setup_and_judge():
    plan = stack.load_plan("part-2.5")
    spoken = run.spoken_steps(plan)
    assert spoken == 10 and run.researchers(plan) == 1  # steps 1-8, 10 and the multi-step; idle and the restart say nothing
    from bench.voice_journeys.judge import COST_PER_STEP_USD

    one = spoken * (run.STEP_USD["gpt-realtime-2.1"] + COST_PER_STEP_USD) + run.RESEARCHER_USD
    assert run.worst_case(plan, 2, "gpt-realtime-2.1", judge=True, setup=False) == pytest.approx(2 * one, abs=0.01)
    assert run.worst_case(plan, 2, "gpt-realtime-2.1", judge=True, setup=True) == pytest.approx(2 * one + run.SETUP_USD, abs=0.01)


def test_a_paid_run_needs_a_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(run.stacks, "up", lambda *_: pytest.fail("started without a budget"))
    with pytest.raises(SystemExit) as stopped:
        run.main(["--voice", "gpt-live-1", "--out", str(tmp_path / "out"), "--budget", ""])
    assert stopped.value.code == 2


def test_gpt_live_has_no_hold_to_talk(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run.main(["--voice", "gpt-live-1", "--talk", "hold", "--out", str(tmp_path / "out"), "--budget", "x"])
    assert stopped.value.code == 2


def test_a_refused_reservation_starts_nothing(tmp_path, monkeypatch):
    script, log = budget(tmp_path, limit=1.0)
    monkeypatch.setattr(run.stacks, "up", lambda *_: pytest.fail("started after the budget refused"))
    with pytest.raises(SystemExit) as stopped:
        run.main(["--voice", "gpt-realtime-2.1", "--plan", "part-2.5", "--out", str(tmp_path / "out"), "--budget", script])
    assert stopped.value.code == 3
    assert [c[0] for c in calls(log)] == ["reserve"]


def test_a_run_reserves_first_and_settles_from_what_the_stack_recorded(tmp_path, monkeypatch):
    script, log = budget(tmp_path, limit=100.0)
    out = tmp_path / "out"
    seen = []
    monkeypatch.setattr(run.stacks, "up", lambda a: seen.append(("up", a.voice, a.talk, a.faults, a.research_cap)) or
                        {"ok": True, "studio": "http://127.0.0.1:1/", "bridge": "http://127.0.0.1:2"})
    monkeypatch.setattr(run.stacks, "down", lambda a: seen.append(("down",)))

    def drive(out_, plan, told, **kw):
        seen.append(("drive", kw["name"], kw["mode"]))
        if kw["name"].startswith("run-"):
            kept_run_into(out_, kw["name"])
        return out_ / kw["name"] / "bench.json"

    def kept_run_into(out_: Path, name: str):
        bench, conv, inter, judged = good()
        (out_ / name).mkdir(parents=True, exist_ok=True)
        (out_ / name / "bench.json").write_text(json.dumps(bench))
        (out_ / name / "verdicts.json").write_text(json.dumps(judged))
        logs = out_ / "home" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "conversation.jsonl").write_text("\n".join(json.dumps(c) for c in conv))
        (logs / "interactions.jsonl").write_text("\n".join(json.dumps(r) for r in inter))

    monkeypatch.setattr(run, "drive", drive)
    monkeypatch.setattr(run, "spent", lambda port, inter: 1.25)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({**PLAN, "id": "synthetic", "steps": PLAN["steps"]}))
    code = run.main(["--voice", "gpt-realtime-2.1", "--plan", str(plan_file), "--out", str(out), "--budget", script,
                     "--reps", "1"])
    assert code == 0  # the synthetic run meets the standard
    assert seen[0][:4] == ("up", "gpt-realtime-2.1", "hold", True)
    assert ("drive", "run-1", "hold") in seen and seen[-1] == ("down",)
    budget_calls = calls(log)
    assert budget_calls[0][:2] == ["reserve", "pr"] and budget_calls[0][3] == run.OWNER
    from bench.voice_journeys.judge import COST_PER_STEP_USD

    assert budget_calls[1][:3] == ["settle", "res-1", f"{1.25 + 7 * COST_PER_STEP_USD:.2f}"]
    result = json.loads((out / "result.json").read_text())
    assert result["meets"] and result["about"]["voice"] == "gpt-realtime-2.1" and (out / "scoreboard.md").exists()


def test_a_run_that_fails_settles_at_the_worst_case(tmp_path, monkeypatch):
    script, log = budget(tmp_path, limit=100.0)
    monkeypatch.setattr(run.stacks, "up", lambda a: {"ok": False})
    monkeypatch.setattr(run.stacks, "down", lambda a: None)
    with pytest.raises(SystemExit):
        run.main(["--voice", "gpt-live-1", "--plan", "smoke", "--out", str(tmp_path / "out"), "--budget", script])
    reserve, settle = calls(log)
    assert settle[:3] == ["settle", "res-1", reserve[2]] and "worst case" in settle[3:]


def test_score_only_rescores_kept_runs_without_starting_anything(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    kept_run(out)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({**PLAN, "id": "synthetic"}))
    monkeypatch.setattr(run.stacks, "up", lambda *_: pytest.fail("score-only started a stack"))
    assert run.main(["--score-only", "--plan", str(plan_file), "--out", str(out)]) == 0
    result = json.loads((out / "result.json").read_text())
    assert result["meets"] and result["about"]["audioFrom"] == "in the page"
    assert "Standard met: yes" in (out / "scoreboard.md").read_text()


def test_compare_switches_the_default_only_when_the_one_brain_meets_every_line(tmp_path):
    rt, live = tmp_path / "rt", tmp_path / "live"
    kept_run(rt)
    plan = {**PLAN, "id": "synthetic"}
    good_result = run.write_results(rt, plan, {"voice": "gpt-realtime-2.1", "talk": "hold"})
    kept_run(live, verdicts=False)
    live_result = run.write_results(live, plan, {"voice": "gpt-live-1", "talk": "open"})
    board = compare.compare([live_result, good_result])
    assert "gpt-live-1 (open, 1 runs)" in board and "gpt-realtime-2.1 (hold, 1 runs)" in board
    assert "default to the one brain with hold to talk: yes**" in board
    good_result["meets"] = False
    assert "default to the one brain with hold to talk: no**" in compare.compare([live_result, good_result])


def expects_of(plan: dict, step_id: str) -> dict:
    return next((s.get("expect") or {} for s in plan["steps"] if s["id"] == step_id), {})


KNOWN = {"look_hard", "mark", "do_in_hooke", "think", "show_measurements", "run_capability", "web_search"}


@pytest.mark.parametrize("name", sorted(p.stem for p in stack.PLANS.glob("*.json")))
def test_shipped_plans_name_real_tools_and_known_fields(name):
    golden = json.loads((Path(__file__).parent / "golden" / "tool_schemas.json").read_text())
    tools = KNOWN | set(__import__("re").findall(r'"name": "([a-z_]+)"', json.dumps(golden)))
    plan = stack.load_plan(name)
    assert plan["id"] == name and plan["voices"]["user"]
    fields = {"tools", "toolRequired", "visible", "researchers", "marks", "status", "multi", "fails", "outcome",
              "judge", "silent"}
    for step in [*plan.get("setup", []), *plan["steps"]]:
        expect = step.get("expect") or {}
        assert set(expect) <= fields, (step["id"], set(expect) - fields)
        status = expect.get("status")
        assert status in (None, "latest") or (isinstance(status, int) and status > 0) or (
            isinstance(status, dict) and status.get("from") in {s["id"] for s in plan["steps"]}
            and (expects_of(plan, status["from"]).get("researchers") or 0) > status.get("index", 0)), step["id"]
        assert set(expect.get("tools") or []) <= tools, (step["id"], set(expect["tools"]) - tools)
        assert step["kind"] in ("act", "view", "ask", "status", "idle", "restart", "typed", "plural", "close", "photo",
                                "side", "stage"), step["id"]
        if step["kind"] not in ("idle", "restart", "stage"):
            assert step.get("say"), step["id"]
        if step.get("ensure"):
            assert step["ensure"].split(":")[0] == "project" and step["ensure"].split(":", 1)[1], step["id"]


def test_part_two_and_a_half_covers_the_journey_and_its_second_pass():
    first, second = stack.load_plan("part-2.5"), stack.load_plan("part-2.5-second")
    assert [s["kind"] for s in first["steps"]].count("restart") == 1  # step 8, quit and reopen
    assert any("models_in_use" in (s.get("expect") or {}).get("tools", []) for s in first["steps"])  # step 10
    assert sum(1 for s in second["steps"] if s.get("interrupt")) == 1
    assert sum(1 for s in second["steps"] if (s.get("expect") or {}).get("fails")) == 1
    assert [s["id"] for s in second["steps"] if "switch_session" in (s.get("expect") or {}).get("tools", [])] == \
        ["back-to-nuclei", "to-scratch", "back-again"]
    assert {s["say"] for s in first["steps"] if s.get("say")}.isdisjoint({s["say"] for s in second["steps"] if s.get("say")})
    assert first["voices"]["user"] != second["voices"]["user"]
    assert [s["id"] for s in first["steps"] if (s.get("expect") or {}).get("multi")] == ["outline-and-sizes"]
    for plan in (first, second):  # every new Project in the setup gets the specimen on its stage (else nothing to view)
        kinds = [s["kind"] for s in plan["setup"]]
        assert kinds.count("stage") == 2 and all(plan["setup"][i + 1]["kind"] == "stage" for i, s in enumerate(plan["setup"])
                                                  if "new project" in s.get("say", "").lower())
        assert plan["camera"]["footage"] == Path(plan["camera"]["still"]).stem + "-noise.mp4"  # stack.noisy_loop's name
    assert first["steps"][0]["ensure"] == "project:Nuclei"


def test_words_are_said_once_and_kept(tmp_path, monkeypatch):
    said = []

    def fake(cmd, check):
        said.append(cmd[0])
        if cmd[0] == "say":
            Path(cmd[4]).write_bytes(b"aiff")
        else:
            Path(cmd[-1]).write_bytes(b"wav")

    monkeypatch.setattr(stack.subprocess, "run", fake)
    plan = stack.load_plan("smoke")
    stack.words(plan, tmp_path)
    stack.words(plan, tmp_path)
    assert said.count("say") == 2 and sorted(p.name for p in tmp_path.glob("*.wav")) == ["looking.wav", "outline.wav"]


def test_harness_and_driver_parse():
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node not installed")
    for name in ("harness.js", "drive.mjs"):
        subprocess.run(["node", "--check", str(stack.HERE / name)], check=True)
    assert sys.executable


def test_the_judge_numbers_the_lines_the_scorer_reads():
    """Claims come back by line number: the judge's lines and the scorer's must be the same, in the same order (an
    event's line in a step's window belongs to neither)."""
    from bench.voice_journeys.judge import facts, verdicts
    from bench.voice_journeys.standard import score_run

    bench, conv, inter, _ = good()
    told = {f["id"]: f for f in facts(bench, conv, inter)}
    run_ = score_run(bench, conv, inter, PLAN)
    for row in run_["steps"]:
        assert [l.split(") ", 1)[1] for l in told[row["id"]]["lines"]] == row["lines"]
    assert not any("Researcher 2 is done" in l for l in told["multi"]["lines"])

    class Model:
        def __init__(self):
            self.prompts = []

        async def ask(self, images, prompt, max_output_tokens=600):
            self.prompts.append(prompt)
            data = {"claims_on_screen": [{"line": 2, "words": "Done"}, 1], "outcome_mentions": 1, "answered": True}
            return type("R", (), {"data": data, "error": None, "raw_text": ""})()

    import asyncio

    model = Model()
    judged = asyncio.run(verdicts(bench, conv, inter, PLAN, None, model=model))
    assert judged["outline"]["claims_on_screen"] == [{"line": 1, "words": "Done"}, 0]
    assert 'The user said: "outline"' in model.prompts[0] and "confirmed +" in model.prompts[0]
    assert "partner_quality: 0-3" in model.prompts[0] and "I've circled it." in model.prompts[0]  # calibrated
    assert "Earlier" not in model.prompts[0] and 'Earlier, the user said "outline"' in model.prompts[1]  # the turns before


def test_the_broad_journey_covers_what_a_partner_is_asked():
    """Will, 2026-10-29: broad, not a memorised path. 25-30 requests in one session, each area covered."""
    plan = stack.load_plan("part-2.5-broad")
    spoken = [s for s in plan["steps"] if s.get("say")]
    assert 25 <= len(spoken) <= 30 and len({s["say"] for s in spoken}) == len(spoken)
    expects = {s["id"]: s.get("expect") or {} for s in plan["steps"]}
    assert sum(1 for s in plan["steps"] if s.get("interrupt")) == 1
    assert sum(1 for e in expects.values() if e.get("fails")) == 1
    assert expects["thanks"]["tools"] == [] and not expects["thanks"].get("toolRequired")  # small talk: no tool
    assert expects["two-researchers"]["researchers"] == 2 and run.researchers(plan) == 3
    # The researcher the user named ("researcher 2"), and "the size one": the first the two-researchers step started.
    assert [e["status"] for e in expects.values() if e.get("status")] == [2, {"from": "two-researchers", "index": 0}]
    assert any("compare_models" in e.get("tools", []) and e.get("multi") for e in expects.values())
    assert {"reference_photo", "retrieve_page", "run_capability", "models_in_use", "set_default", "new_project"} <= \
        {t for e in expects.values() for t in e.get("tools", [])}
    assert [s["ensure"] for s in plan["steps"] if s.get("ensure")] == ["project:Nuclei", "project:Experiment", "project:Nuclei"]
    assert plan["steps"][-2]["kind"] == "idle" and plan["steps"][-2]["waitFor"] == {"researcher": "latest-done"}
    assert run.worst_case(plan, 1, "gpt-realtime-2.1", judge=True, setup=True) < 30  # one run's worst case stays bounded
