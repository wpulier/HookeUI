"""A job's state is said where the user sees it (App.job_said): an Activity line updated in place, and, once it ends, a
notice in the conversation it was asked in. Live 2026-10-13 an interrupted job was never said: its notice's tone was one
`dialogue.notice` refuses, raised on the loop where nobody saw it, and the broadcast after it was skipped."""
import asyncio

from tests.capability_rig import Rig


def test_every_end_of_a_job_is_a_notice_in_the_conversation_it_was_asked_in(tmp_path):
    rig = Rig(tmp_path)
    app = rig.app
    thread = app.day.active_id
    notices, errors = [], []
    notice = app.dialogue.notice

    def kept(**kw):
        notices.append(kw)
        return notice(**kw)

    app.dialogue.notice = kept
    base = {"kind": "job", "session": app.day.sid, "thread": thread, "volume": {"id": "v1", "name": "brain"},
            "seconds": 12.0, "regions": [{"name": "cortex", "mm3": 2.5}]}

    async def run():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        app._loop = loop
        for n, state in enumerate(("running", "done", "failed", "cancelled", "interrupted"), 1):
            app.job_said({**base, "id": f"j{n}", "state": state, "error": "it broke", "says": "said"})
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert errors == [], errors
    assert [n["event"] for n in notices] == ["done", "failed", "cancelled", "interrupted"], "running is no notice"
    assert all(n["asked"].thread == thread for n in notices)
    assert [n["tone"] for n in notices] == ["info", "error", "error", "error"]


def test_a_done_registration_opens_its_own_picture_and_one_not_done_is_no_product(tmp_path):
    """Made in this session's Open of a done registration delivers its own PNG (live 2026-10-13 it was refused: no
    deliverable original); a cancelled or interrupted one made nothing, so it is not a product and has no picture."""
    import pytest
    import hashlib

    from orchestrator.presentation_delivery import DeliveryUnavailable
    from orchestrator.presentations import ResourceRef

    rig = Rig(tmp_path)
    app = rig.app
    regs, sid = app.registrations, app.day.sid
    date = app.day.date
    for jid, state in (("j1", "done"), ("j2", "cancelled")):
        record = {"id": jid, "kind": "job", "session": sid, "date": date, "thread": app.day.active_id, "at": 1.0,
                  "state": state, "entry": "brainreg", "volume": {"id": "v1", "name": "brain"}, "seconds": 300.0,
                  "regions": [], **({"picture": "brainreg/picture.png", "outputs": {"picture": {
                      "sha256": hashlib.sha256(b"\x89PNG " + jid.encode()).hexdigest()}}} if state == "done" else {})}
        out = regs.folder(record) / "out" / "brainreg"
        out.mkdir(parents=True)
        (out / "picture.png").write_bytes(b"\x89PNG " + jid.encode())
        regs._write(record)
    entries = {e.id: e for e in app.hub.resources.list(kind="job", day=sid)}
    assert entries["j1"].wire()["product"] and not entries["j2"].wire()["product"]
    sources = app.services.prepared_sources
    selection = sources._select(ResourceRef(day=sid, id="j1", version=entries["j1"].version, picture=0))
    assert selection.path.read_bytes() == b"\x89PNG j1" and selection.mime == "image/png"
    with pytest.raises(DeliveryUnavailable):  # it lists no picture
        sources._select(ResourceRef(day=sid, id="j2", version=entries["j2"].version, picture=0))


def test_jobs_left_running_are_recovered_off_the_event_loop(tmp_path):
    """The review of 2026-10-13, finding 4: ending a job a crash left running waits for it (jobs.end, up to its grace),
    so at start that recovery runs on a thread of its own and each job is said from there."""
    import threading

    rig = Rig(tmp_path)
    app = rig.app
    ran_on, said = [], []
    record = {"id": "j1", "kind": "job", "state": "interrupted", "volume": {"id": "v1", "name": "brain"}}

    def interrupted():
        ran_on.append(threading.current_thread())
        return [record]

    app.registrations.interrupted = interrupted
    app.job_said = said.append

    async def run():
        await app.recover_jobs()
        return threading.current_thread()

    loop_thread = asyncio.run(run())
    assert ran_on and ran_on[0] is not loop_thread and said == [record]


def test_finished_job_is_filed_in_its_original_session_after_a_switch(tmp_path):
    rig = Rig(tmp_path)
    app = rig.app
    origin = app.day.here()
    record = {"id": "j1", "kind": "job", "state": "done", "session": origin.session,
              "thread": origin.thread, "volume": {"id": "v1", "name": "brain"}, "seconds": 12,
              "regions": [], "says": "registered it"}
    app.day.new("Another specimen")

    async def finish():
        app._loop = asyncio.get_running_loop()
        app.job_said(record)
        await asyncio.sleep(.02)

    asyncio.run(finish())
    def jobs_in(store):
        return [line for thread in store.threads.values() for line in thread.lines
                if line.get("producer", {}).get("kind") == "job"]
    assert jobs_in(app.day) == [], "a late result belongs to the session that requested it"
    lines = jobs_in(app.day.elsewhere(origin.session))
    assert len(lines) == 1 and lines[0]["thread"] == origin.thread
