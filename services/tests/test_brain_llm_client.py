"""The function-calling brain builds one provider client per key, in a thread, never one per turn on the event loop
(orchestrator/brain/llm.py `_client`; L1b part 2)."""
import asyncio
import threading

from orchestrator.brain.llm import FunctionCallingBrain


def test_one_client_per_key_built_off_the_loop():
    brain = FunctionCallingBrain.__new__(FunctionCallingBrain)
    brain._clients = {}
    built = []

    def make(name):
        def build():
            built.append((name, threading.current_thread() is threading.main_thread()))
            return object()
        return build

    async def turns():
        first = await brain._client(("gemini", "key-a"), make("a"))
        again = await brain._client(("gemini", "key-a"), make("a"))
        replaced = await brain._client(("gemini", "key-b"), make("b"))
        return first, again, replaced

    first, again, replaced = asyncio.run(turns())
    assert first is again and replaced is not first
    assert built == [("a", False), ("b", False)]  # built once per key, never on the loop's thread
