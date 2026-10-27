"""An unexpected failure reaches the user in plain words with a next step, and its real error reaches the log
when it happens, a step that ends after the answer included (docs/design/2026-10-07-the-partner.md §1c).
"It is in the log" is said only when it was written."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator import commands
from orchestrator.brain.registry import Registry, Tool, ToolContext, ToolFailed


def broken_registry(log=None, delay: float = 0.0):
    registry = Registry()
    registry.log = log

    async def broken(ctx, args):
        await asyncio.sleep(delay)
        raise KeyError("thing")

    registry.add(Tool("measure_it", "measures it", {"type": "object", "properties": {}}, broken))
    return registry


def run_to_end(registry):
    async def go():
        run = registry.start("measure_it", {"x": 1}, ToolContext("chat", 7, 3, "measure it"))
        await run.task
        return run
    return asyncio.run(go())


def test_an_unexpected_failure_is_plain_words_with_its_real_error_in_the_log():
    logged = []
    run = run_to_end(broken_registry(logged.append, delay=0.05))  # finishes after the answer would have gone out
    assert run.ok is False and run.summary.startswith("something went wrong in the app")
    assert run.next == "try asking another way; if it keeps happening, it is in the log"
    assert "KeyError" not in run.summary and "KeyError" not in json.dumps(run.wire())  # never the user's words
    assert run.wire(full=True)["error"] == "KeyError: 'thing'"
    assert len(logged) == 1  # written when it failed, with what it was asked and the real error
    assert {k: logged[0][k] for k in ("kind", "tool", "turn", "channel", "args", "error", "ok")} == {
        "kind": "tool-error", "tool": "measure_it", "turn": 7, "channel": "chat", "args": {"x": 1},
        "error": "KeyError: 'thing'", "ok": False}


def test_in_the_log_is_said_only_when_it_was_written():
    assert run_to_end(broken_registry(None)).next == "try asking another way"

    def full_disk(record):
        raise OSError("no space left")

    assert run_to_end(broken_registry(full_disk)).next == "try asking another way"


def handle(app, message):
    sent = []
    asyncio.run(commands._handle(app, SimpleNamespace(offer=sent.append), message))
    return json.loads(sent[0]) if sent else None


def test_a_studio_command_that_fails_says_so_plainly_and_always_replies(monkeypatch):
    logged = []

    async def broken(app, client, msg):
        raise RuntimeError("boom")

    async def refused(app, client, msg):
        raise ToolFailed("that box is too small", next="drag a larger one")

    monkeypatch.setitem(commands.COMMANDS, "broken-test", broken)
    monkeypatch.setitem(commands.COMMANDS, "refused-test", refused)
    app = SimpleNamespace(agent=SimpleNamespace(log=logged.append), day=SimpleNamespace(local_only=False))
    reply = handle(app, {"type": "broken-test"})
    assert reply["ok"] is False and reply["error"] == "something went wrong in the app; try again; if it keeps happening, it is in the log"
    assert logged == [{"kind": "command-error", "command": "broken-test", "error": "RuntimeError: boom"}]
    reply = handle(app, {"type": "refused-test"})
    assert reply["error"] == "that box is too small; drag a larger one" and reply["next"] == "drag a larger one"

    def full_disk(record):
        raise OSError("no space left")

    reply = handle(SimpleNamespace(agent=SimpleNamespace(log=full_disk), day=SimpleNamespace(local_only=False)), {"type": "broken-test"})
    assert reply is not None and reply["error"] == "something went wrong in the app; try again"  # the reply still goes


def test_a_raw_model_error_is_a_plain_line_and_a_plain_refusal_is_said_as_it_is():
    from orchestrator.operations import RAW_ERROR

    for raw in ("RuntimeError: CUDA out of memory", "KeyError: 'masks'", "TimeoutError: ", "ConnectionError: refused"):
        assert RAW_ERROR.match(raw), raw
    for plain in ("8 segment requests are already waiting; try again in a moment", "outline: nothing found",
                  "EdgeTAM can't run here (the heavy worker is not started)"):
        assert not RAW_ERROR.match(plain), plain
