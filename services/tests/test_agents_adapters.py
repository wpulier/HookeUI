"""The outside-agent adapters against what the real programs said (ADR 0029; tests/fixtures/agents/README.md).

Each test plays a recorded stream back as the program and drives the adapter the way the conductor does: the brief as
the first turn, a steer, the user's answer to an ask, an interrupt. Every ask reaches the user as an event and is
answered only by the call the user's answer makes.
"""
import asyncio

import pytest

from agent_streams import Events, Replay, factory, load, until_waiting
from orchestrator.agents.acp import SignInNeeded, gemini, grok
from orchestrator.agents.adapter import HOOKE_MARK, Spec, marked
from orchestrator.agents.claude import Claude
from orchestrator.agents.codex import Codex

SPEC = Spec(cwd="/work", brief=marked("Make a folder named hello."), instructions="Standing rules.",
            env={"HOOKE_AGENT": "codex"}, budget_usd=1.5)


def test_marked_says_whose_words_they_are():
    assert marked("use micro-sam") == f"{HOOKE_MARK} use micro-sam"
    assert marked(f"{HOOKE_MARK} already") == f"{HOOKE_MARK} already"


# --- Codex ------------------------------------------------------------------------------------------------------------
def test_codex_start_steer_ask_and_interrupt():
    async def scenario():
        replay = Replay(load("codex-app-server.jsonl"))
        make = factory(replay)
        adapter = Codex(make, "/opt/bin/codex")
        events = Events(adapter)
        await adapter.start(SPEC)
        assert make.calls[0][0] == ["/opt/bin/codex", "app-server"]
        assert [m.get("method") for m in replay.sent] == ["initialize", "initialized", "thread/start", "turn/start"]
        started = replay.sent[2]["params"]
        # Codex's own sandbox, approvals on request: everything outside the sandbox comes back as an ask.
        assert started["sandbox"] == "workspace-write" and started["approvalPolicy"] == "on-request"
        assert started["developerInstructions"] == "Standing rules."
        assert replay.sent[3]["params"]["input"][0]["text"].startswith(HOOKE_MARK)
        assert events.of("session")[0]["session"] == adapter.thread

        assert (await until_waiting(replay))["method"] == "turn/steer"
        assert await adapter.send(marked("Also mention stomata.")) == "steered"
        assert replay.sent[-1]["params"]["expectedTurnId"] == adapter.turn

        asks = await events.until(lambda: events.of("permission"))
        assert asks[0]["title"] == "Run a command" and "mkdir" in asks[0]["detail"]
        assert adapter.permissions  # waiting for the user: nothing answered it
        await adapter.answer(asks[0]["id"], True)
        assert replay.sent[-1] == {"id": 0, "result": {"decision": "accept"}}
        await events.until(lambda: events.of("resolved", id=asks[0]["id"], outcome="allowed"))

        await events.until(lambda: events.of("turn", state="completed"))
        final = [e for e in events.of("text") if e["final"]]
        assert final and "stomata" in final[-1]["text"]
        assert any(e["type"] == "tool" and e["state"] == "done" for e in events.items)
        assert events.of("usage")[-1]["tokens"] > 0

        assert (await until_waiting(replay))["method"] == "turn/start"
        assert await adapter.send(marked("Count to 200.")) == "started"
        assert (await until_waiting(replay))["method"] == "turn/interrupt"
        assert await adapter.interrupt() is True
        await events.until(lambda: events.of("turn", state="interrupted"))
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
        assert events.items[-1]["type"] == "exit"
    asyncio.run(scenario())


def test_codex_declined_and_always_answers():
    async def scenario():
        replay = Replay(load("codex-app-server.jsonl"))
        adapter = Codex(factory(replay))
        events = Events(adapter)
        await adapter.start(SPEC)
        await until_waiting(replay)
        await adapter.send(marked("steer"))
        ask = (await events.until(lambda: events.of("permission")))[0]
        await adapter.answer(ask["id"], True, always=True)
        # For this session only: never Codex's exec-policy amendment, which it keeps in the user's own ~/.codex/rules.
        assert replay.sent[-1]["result"] == {"decision": "acceptForSession"}
        with pytest.raises(KeyError):
            await adapter.answer(ask["id"], False)  # answered once; a second answer finds nothing waiting
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


def test_codex_refuses_requests_it_does_not_answer():
    async def scenario():
        replay = Replay([{"dir": "in", "msg": {"id": 7, "method": "item/tool/requestUserInput", "params": {}}},
                         {"dir": "out", "msg": {"id": 7, "error": {}}}])
        adapter = Codex(factory(replay))
        adapter.transport = replay
        from orchestrator.agents.jsonrpc import Peer
        adapter.peer = Peer(replay, on_request=adapter._request, on_notification=adapter._notification)
        reader = asyncio.create_task(adapter.peer.run())
        for _ in range(200):
            if replay.sent:
                break
            await asyncio.sleep(0.005)
        assert replay.sent[0]["id"] == 7 and replay.sent[0]["error"]["code"] == -32601
        assert not adapter.permissions
        replay.end(0)
        await reader
    asyncio.run(scenario())


# --- Claude Code ------------------------------------------------------------------------------------------------------
def test_claude_start_ask_interrupt():
    async def scenario():
        replay = Replay(load("claude-stream-json.jsonl"))
        make = factory(replay)
        adapter = Claude(make, "/opt/bin/claude")
        events = Events(adapter)
        await adapter.start(SPEC)
        argv = make.calls[0][0]
        assert argv[:2] == ["/opt/bin/claude", "-p"]
        # The user's own sign-in (never --bare, which reads only an API key), their own settings left out, sandbox on,
        # every ask over stdio to Hooke, held to the launch's budget.
        assert "--bare" not in argv and "--safe-mode" in argv and "--strict-mcp-config" in argv
        assert argv[argv.index("--permission-prompt-tool") + 1] == "stdio"
        assert '"sandbox": {"enabled": true}' in argv[argv.index("--settings") + 1]
        assert "--max-budget-usd=1.50" in argv
        assert replay.sent[0]["request"]["subtype"] == "initialize"
        assert replay.sent[1]["type"] == "user" and "priority" not in replay.sent[1]

        ask = (await events.until(lambda: events.of("permission")))[0]
        assert ask["title"] == "Write a file" and ask["detail"].endswith("notes.txt")
        await adapter.answer(ask["id"], True)
        sent = replay.sent[-1]["response"]
        assert sent["subtype"] == "success" and sent["response"]["behavior"] == "allow"
        assert "updatedPermissions" not in sent["response"]  # allowed once, not for the session

        await events.until(lambda: events.of("turn", state="completed"))
        assert events.of("session")[0]["billing"] == "none"  # the user's own sign-in
        assert events.of("usage")[-1]["costUsd"] > 0
        assert [e for e in events.of("text") if e["final"]]

        assert (await until_waiting(replay))["type"] == "user"
        assert await adapter.send(marked("Write an essay.")) == "started"
        assert (await until_waiting(replay))["type"] == "control_request"
        assert await adapter.interrupt() is True
        await events.until(lambda: events.of("turn", state="interrupted"))
        replay.end(1)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


def test_claude_steers_a_running_turn():
    async def scenario():
        replay = Replay(load("claude-steer.jsonl"))
        adapter = Claude(factory(replay))
        events = Events(adapter)
        await adapter.start(SPEC)
        assert (await until_waiting(replay)).get("priority") == "now"
        assert await adapter.send(marked("Change of plan.")) == "steered"
        assert replay.sent[-1]["priority"] == "now"
        await events.until(lambda: events.of("turn", state="completed"))
        # The segment the steer ended is not the turn's end: one turn, finished with the steered answer.
        assert not events.of("turn", state="failed") and not events.of("turn", state="interrupted")
        assert [e["text"] for e in events.of("text") if e["final"]] == ["STEERED"]
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


def test_claude_denies_and_says_whose_answer_it_is():
    async def scenario():
        replay = Replay(load("claude-stream-json.jsonl"))
        adapter = Claude(factory(replay))
        events = Events(adapter)
        await adapter.start(SPEC)
        ask = (await events.until(lambda: events.of("permission")))[0]
        await adapter.answer(ask["id"], False)
        response = replay.sent[-1]["response"]["response"]
        assert response == {"behavior": "deny", "message": "The researcher declined this in Hooke."}
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


# --- ACP: Grok Build, Gemini CLI ----------------------------------------------------------------------------------------
def test_grok_asks_and_is_allowed_once():
    async def scenario():
        replay = Replay(load("grok-acp-permission.jsonl"))
        make = factory(replay)
        adapter = grok(make, "/opt/bin/grok")
        events = Events(adapter)
        await adapter.start(SPEC)
        assert make.calls[0][0] == ["/opt/bin/grok", "--permission-mode", "default", "agent", "stdio"]
        hello = replay.sent[0]
        assert hello["jsonrpc"] == "2.0" and hello["params"]["protocolVersion"] == 1
        # Hooke offers no file system or terminal: the agent uses its own, in its own folder.
        assert hello["params"]["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False},
                                                         "terminal": False}
        assert replay.sent[1]["params"]["mcpServers"] == []
        await events.until(lambda: len(replay.sent) >= 3)  # the prompt is a request of its own, sent as the turn starts
        prompt = replay.sent[2]["params"]["prompt"][0]["text"]
        assert prompt.startswith("Standing rules.") and HOOKE_MARK in prompt
        assert events.of("session")[0]["model"] == "grok-4.7"

        ask = (await events.until(lambda: events.of("permission")))[0]
        assert "codex-probe-write-action.txt" in ask["detail"] and ask["always"] is True
        await adapter.answer(ask["id"], True)
        assert replay.sent[-1]["result"] == {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
        await events.until(lambda: events.of("turn", state="completed"))
        assert any(e["type"] == "tool" and e["state"] == "done" for e in events.items)
        assert [e for e in events.of("text") if e["final"]]
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


def test_grok_steer_cancels_then_sends_the_note():
    async def scenario():
        replay = Replay(load("grok-acp-steer.jsonl"))
        adapter = grok(factory(replay))
        events = Events(adapter)
        await adapter.start(SPEC)
        assert (await until_waiting(replay))["method"] == "session/cancel"
        assert await adapter.send(marked("Actually, respond with exactly: steering fixture observed")) == "steered"
        await events.until(lambda: events.of("turn", state="completed"))
        assert [m.get("method") for m in replay.sent].count("session/prompt") == 2
        assert replay.sent[-1]["params"]["prompt"][0]["text"].startswith(HOOKE_MARK)
        assert not events.of("turn", state="interrupted")  # the turn went on with the note
        assert "steering fixture observed" in [e for e in events.of("text") if e["final"]][-1]["text"]
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


def test_grok_interrupt_and_decline():
    async def scenario():
        replay = Replay(load("grok-acp-permission.jsonl"))
        adapter = grok(factory(replay))
        events = Events(adapter)
        await adapter.start(SPEC)
        ask = (await events.until(lambda: events.of("permission")))[0]
        await adapter.answer(ask["id"], False)
        # Declining picks the agent's own reject option; never a different answer than the user gave.
        assert replay.sent[-1]["result"]["outcome"]["optionId"].startswith("reject")
        replay.end(0)
        await asyncio.wait_for(events.task, 5)
    asyncio.run(scenario())


def test_gemini_refusal_is_said_as_sign_in():
    async def scenario():
        replay = Replay(load("gemini-acp-refused.jsonl"))
        adapter = gemini(factory(replay), "/opt/bin/gemini")
        with pytest.raises(SignInNeeded, match="no longer supported"):
            await adapter.start(SPEC)
        replay.end(0)
    asyncio.run(scenario())


def test_claude_allows_only_hookes_own_command_outside_its_sandbox():
    from orchestrator.agents.claude import settings
    allowed = settings("/Applications/HookeUI Beta.app/Contents/Helpers/hooke")
    assert allowed["sandbox"] == {"enabled": True,
                                  "excludedCommands": ["/Applications/HookeUI Beta.app/Contents/Helpers/hooke"]}
    assert allowed["permissions"] == {"allow": ["Bash(/Applications/HookeUI Beta.app/Contents/Helpers/hooke:*)"]}
    assert settings(None) == {"sandbox": {"enabled": True}}
    assert settings("/opt/hooke", "/Apps/H/.agents/skills/hooke/SKILL.md")["permissions"]["allow"] == [
        "Bash(/opt/hooke:*)", "Read(//Apps/H/.agents/skills/hooke/SKILL.md)"]


def test_codex_session_answer_covers_plain_hooke_commands_only():
    async def scenario():
        hooke = "/w/agents/bin/hooke-1"
        ask = lambda rid, command: {"dir": "in", "msg": {"id": rid, "method": "item/commandExecution/requestApproval",
                                                         "params": {"command": command, "reason": "outside the sandbox"}}}
        replay = Replay([ask(0, f"/bin/zsh -lc '{hooke} --agent codex status --json'"),
                         {"dir": "out", "msg": {"id": 0, "result": {}}},
                         ask(1, f"/bin/zsh -lc '{hooke} --agent codex tools --json'"),
                         {"dir": "out", "msg": {"id": 1, "result": {}}},
                         ask(2, f"/bin/zsh -lc '{hooke} --agent codex tools --json > ../caps.json'"),
                         {"dir": "out", "msg": {"id": 2, "result": {}}}])
        adapter = Codex(factory(replay))
        adapter.hooke, adapter.transport = hooke, replay
        from orchestrator.agents.jsonrpc import Peer
        adapter.peer = Peer(replay, on_request=adapter._request, on_notification=adapter._notification)
        events = Events(adapter)
        reader = asyncio.create_task(adapter.peer.run())
        first = (await events.until(lambda: events.of("permission")))[0]
        await adapter.answer(first["id"], True, always=True)
        # The next plain hooke command is the user's own answer for this session; one that writes a file still asks.
        await events.until(lambda: len(replay.sent) >= 2)
        assert replay.sent[1] == {"id": 1, "result": {"decision": "acceptForSession"}}
        said = await events.until(lambda: [e for e in events.of("tool") if "allowed for this session" in (e["title"] or "")])
        assert len(said) == 1  # said once, not after every hooke command
        asks = await events.until(lambda: [e for e in events.of("permission") if e["id"] == "codex:2"])
        assert "../caps.json" in asks[0]["detail"] and len(events.of("permission")) == 2
        assert adapter._plain_hooke(f"{hooke} --agent codex tools --json > tools.json")  # into its own folder: covered
        assert not adapter._plain_hooke(f"{hooke} tools > /etc/x") and not adapter._plain_hooke(f"{hooke} tools >> a")
        replay.end(0)
        await reader
    asyncio.run(scenario())
