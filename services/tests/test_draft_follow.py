"""The studios follow an AI draft while its builder works (commands._follow_draft; ticket 62 L13)."""
from types import SimpleNamespace

from orchestrator import drafts
from tests.test_draft_review import FIELDS
from tests.test_drafts import ADAPTER, bench  # noqa: F401 - the shared bench fixture


def test_the_studios_follow_a_draft_while_its_builder_works_until_it_ends(bench, monkeypatch):
    """Live 2026-10-20: the sheet said "Reading the retained AI draft" until a reload, since nothing told the studios a
    try had ended. Each change of the draft's row is broadcast until its builder ends."""
    import asyncio

    from orchestrator import commands

    did = drafts.create("https://github.com/maya/blobs")
    sent = []
    app = SimpleNamespace(hub=SimpleNamespace(broadcast=sent.append), capabilities_wire=lambda: {"type": "capabilities"})
    real_sleep = asyncio.sleep

    async def run():
        follow = asyncio.create_task(commands._follow_draft(app, did))
        await real_sleep(0.3)
        assert len(sent) == 1, "its first state"
        drafts.write(did, ADAPTER, "numpy==2.0.0", FIELDS)
        await real_sleep(0.3)
        assert len(sent) == 2, "a written draft is seen"
        drafts.finish(did, "finished")
        await asyncio.wait_for(follow, 2)
        assert len(sent) == 3, "its end is seen, then it stops following"

    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0.05))
    asyncio.run(run())
