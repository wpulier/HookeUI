"""A voice request is as first-class as a typed Send on the screen, and an internal check is never the user's to repeat
(live 2026-10-29, feel-check-home 17:17-17:26: two closes, a highlight, a table, a researcher's graphic and the
researcher's own result all failed "stale ... ask again in a moment" while the studio kept reporting).

What broke: one window whose grant had ended made the server refuse every report whole ("No exact prepared source grant
for this connection"), so Hooke's view of the screen froze. Each test here is one failure path, its real state, how
Hooke re-checks it and its own recovery: a report with an ended window is taken without it; a change with no report
since asks the studio for one; a request on a report gone stale asks again before its steps run; a voice hand-off is
pinned to the studio's report now; what still cannot be confirmed is said as that, never "done", never "ask again".

The App, Registry, Runner, PreparedSources, Presentations, native commands and Screen are real; the studio is the
synthetic renderer of test_workspace_actions_app (AppStudio), not browser evidence."""
import asyncio
import json
from types import SimpleNamespace

from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.registry import ToolContext
from orchestrator.commands import COMMANDS
from orchestrator.presentations import PresentationAction
from orchestrator.ticket import issue, using
from tests.test_workspace_actions_app import (
    WORKSPACE, _ObservationClock, built, open_window, research_fixture, saved_picture, succeeded,
)
from tests.waiting import HANG_S


async def voice_run(app, studio, actions, words="close everything on this tab"):
    """A spoken request's tools as the voice runs them (partner_tools._start): its own turn Ticket, pinned to the
    studio's last report with the hand-off's timing, without the fresh report a typed Send carries."""
    ticket = issue(app.hub, "turn", asked=app.day.here(), channel="voice")
    pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id, timing="server-handoff-arrival")
    ctx = ToolContext("voice", 0, None, question=words, asked=ticket.asked, admission=ticket.admission,
                      request_id=ticket.admission.request_id)
    ctx.client, ctx.request_snapshot, ctx.workspace_id = studio, pin, WORKSPACE
    ctx.when_asked = app.brain.board.when_asked()

    async def on_late(run, context):
        pass

    runner = ActionRunner(app.brain.registry, app.hub, lambda: None, on_late, board=app.brain.board)
    with using(ticket):
        runs = await runner.run(actions, ctx)
        await asyncio.wait_for(runs[-1].sequence, HANG_S)
    ticket.seal()
    return runs, ctx


def close_in_core_only(app, studio, iid):
    """The server closes a window and releases its grant, and the studio has not applied it yet: its next reports
    still show the window open (how a report arrives after a close)."""
    core = studio.store()
    ticket = issue(app.hub, "command", asked=app.day.here(), channel="studio")
    context = app.workspace_context(studio, request_id=ticket.admission.request_id, owner="synthetic-server-close")
    with using(ticket):
        core.commit(core.admit(PresentationAction("close", instance=iid), context))
    ticket.seal()


def test_a_report_with_an_ended_window_is_taken_without_it_and_the_next_close_confirms(tmp_path):
    """17:17:45: the reports after a close named a window whose grant had ended; each was refused whole, and the
    closes waited 4 s and failed "not causally newer". Now the rest of the screen still counts."""
    app, studio = built(tmp_path)
    first, _ = saved_picture(app, 60)
    second, _ = saved_picture(app, 190)
    logged = []
    original_log = app.agent.log
    app.agent.log = lambda record: (logged.append(record), original_log(record))

    async def go():
        ended, _, _ = await open_window(app, first)
        kept, _, _ = await open_window(app, second)
        close_in_core_only(app, studio, ended)
        assert studio.items[ended]["open"], "the studio still shows the window the server closed"
        before = app.hub.screen.observation(studio).wire()["seq"]
        await studio.send_report()
        taken = app.hub.screen.observation(studio).wire()
        assert taken["seq"] > before, "the report is taken, not refused whole"
        assert taken["complete"] is False
        assert all(item["id"] != ended for item in taken["items"]), "the ended window is not represented"
        assert any(item["id"] == kept for item in taken["items"])
        partial = app.hub.screen.partial(studio)
        assert partial and partial["unresolved"][0]["items"] == [ended]
        assert any(r.get("kind") == "screen-partial" for r in logged), "the log names what was left out and why"
        runs, _ = await voice_run(app, studio, [("present", {"do": "close", "instance": {"instanceId": kept}})])
        succeeded(runs[0])
        assert runs[0].outcome["status"] == "confirmed"
        assert "ask again" not in runs[0].summary

    asyncio.run(go())


def test_a_change_with_no_report_since_asks_the_studio_for_one_and_confirms(tmp_path, monkeypatch):
    """A studio that applied the change but did not report it: Hooke asks for a report (screen-ask) and confirms from
    it, instead of waiting the deadline out."""
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        iid, _, _ = await open_window(app, entry)
        observe, report = studio.observe_command, studio.send_report

        async def quiet(_user=()):
            return None

        async def applied_silently(message):
            studio.send_report = quiet
            try:
                await observe(message)
            finally:
                studio.send_report = report

        monkeypatch.setattr(studio, "observe_command", applied_silently)
        asked_before = sum(m.get("type") == "screen-ask" for m in studio.messages)
        runs, _ = await voice_run(app, studio, [("present", {"do": "front", "instance": {"instanceId": iid}})])
        succeeded(runs[0])
        assert runs[0].outcome["status"] == "confirmed"
        assert sum(m.get("type") == "screen-ask" for m in studio.messages) > asked_before

    asyncio.run(go())


def test_a_change_never_reported_is_uncertain_said_plainly_never_ask_again(tmp_path, monkeypatch):
    """Sent, and no report came at all though it was asked: neither done nor failed."""
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        iid, _, _ = await open_window(app, entry)
        observe = studio.observe_command

        async def applied_never_reported(message):
            async def quiet(_user=()):
                return None
            studio.send_report = quiet  # this studio reports nothing more
            await observe(message)

        monkeypatch.setattr(studio, "observe_command", applied_never_reported)
        monkeypatch.setattr("orchestrator.brain.toolbox.present.CONFIRM_S", 0.4, raising=False)
        runs, _ = await voice_run(app, studio, [("present", {"do": "close", "instance": {"instanceId": iid}})])
        run = runs[0]
        assert run.outcome["status"] == "unobserved" and run.outcome["failure"]["code"] == "STUDIO_UNREPORTED"
        assert "not confirmed on screen" in run.summary and "ask again" not in run.summary
        assert run.next is None

    asyncio.run(go())


def test_a_voice_request_on_a_report_gone_stale_asks_again_before_its_steps(tmp_path, monkeypatch):
    """17:21:57 on: every tool failed in 1 ms "original studio observation is unavailable or stale" (the last report
    taken was over 30 s old). A request now asks the studio for its report before its steps run."""
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        iid, _, _ = await open_window(app, entry)
        clock = _ObservationClock(monkeypatch, app)
        clock.offset = 45.0  # the last report taken is 45 s old, the studio in view
        asked = sum(m.get("type") == "screen-ask" for m in studio.messages)
        runs, _ = await voice_run(app, studio, [("present", {"do": "hide", "what": "outlines"})])
        assert "unavailable or stale" not in (runs[0].summary or "")
        assert sum(m.get("type") == "screen-ask" for m in studio.messages) > asked
        observed = app.hub.screen.observation(studio)
        assert app.hub.screen._current_observation(observed), "a report taken now, not 45 s ago"
        runs, _ = await voice_run(app, studio, [("present", {"do": "close", "instance": {"instanceId": iid}})])
        succeeded(runs[0])
        assert runs[0].outcome["status"] == "confirmed" and not studio.items[iid]["open"]

    asyncio.run(go())


def test_a_researchers_result_opens_from_a_voice_request_while_an_ended_window_is_still_reported(tmp_path):
    """17:26:19: placing Researcher 2's graphic in the Stoma Reference Tab failed "the workspace view was out of
    date", because every report had been refused since 17:17. With an ended window still in the studio's reports,
    the researcher's result opens from the voice and is confirmed on screen. (Naming the Tab is the tabs broker's,
    which this synthetic studio has no Tab strip for; the report it reads is the same.)"""
    app, studio = built(tmp_path)
    _, session = research_fixture(app)
    picture, _ = saved_picture(app)

    async def go():
        ended, _, _ = await open_window(app, picture)
        close_in_core_only(app, studio, ended)
        runs, _ = await voice_run(app, studio, [("show_result", {"researcher": session.rid})],
                                  words="put the researcher's graphic in this tab as the main item")
        succeeded(runs[0])
        assert "confirmed by the studio" in runs[0].summary and "ask again" not in runs[0].summary

    asyncio.run(go())


def test_a_user_change_without_an_original_is_left_unowned_not_a_refused_report(tmp_path):
    """Activity's "User change has no represented original structural property": the studio owns a drag against
    the last report it sent, which the server may not have taken. The report is taken; the change is left unowned."""
    app, studio = built(tmp_path)

    async def go():
        before = app.hub.screen.observation(studio).wire()["seq"]
        await studio.send_report(user=[{"id": "never-reported", "prop": "rect"}])
        assert app.hub.screen.observation(studio).wire()["seq"] > before

    asyncio.run(go())


class _Partner:
    def __init__(self):
        self.session = None

    def turn(self):
        return 1

    async def words(self, n, wait):
        return "close everything on this tab"

    def when_asked(self, n):
        return None

    async def snapshot(self):
        return None  # act, then look: no window picture in this synthetic studio

    def screen_now(self):
        return ""


def test_the_voice_hand_off_is_pinned_to_the_studios_report_now_and_seals_its_ticket(tmp_path):
    """The hand-off asks the bound studio for a report and pins that one, as a Send pins its inline report; a step
    sent but not confirmed comes back as uncertain, said plainly."""
    from orchestrator.voice.partner_tools import PartnerTools

    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    seen = {}

    async def turn(text, channel, **kwargs):
        seen.update(kwargs, text=text, channel=channel)
        return {"answer": "Sent the close.", "actions": [{"tool": "present", "ok": True, "summary": "sent",
                "outcome": {"status": "unobserved", "failure": {"code": "STUDIO_UNREPORTED"}}}]}

    async def go():
        await open_window(app, entry)
        brain = SimpleNamespace(turn=turn, conversation=app.brain.conversation, registry=app.brain.registry)
        tools = PartnerTools(brain, app.hub, _Partner(), client=studio)
        last = app.hub.screen.observation(studio).wire()["seq"]
        await asyncio.sleep(0.2)  # the last report is no longer "a moment ago"
        out = await tools._do_in_hooke({"request": "Close every window on this Tab."})
        assert seen["channel"] == "voice" and seen["client"] is studio
        pin = seen["request_snapshot"]
        assert pin.observation.wire()["seq"] > last, "pinned to a report asked for at the hand-off"
        assert any(m.get("type") == "screen-ask" for m in studio.messages)
        assert seen["request_ticket"].sealed, "sealed after the turn, as a typed Send's"
        assert out["result"] == "uncertain" and out["ok"] is None
        assert "couldn't confirm it on screen" in out["note"] and "ask again" not in json.dumps(out).replace(
            "never to ask again", "")

    asyncio.run(go())


def test_screen_command_replies_ok_with_what_it_left_out(tmp_path):
    """The studio is told what was left out (no notice is shown for it); a forged source is still refused whole."""
    app, studio = built(tmp_path)
    first, _ = saved_picture(app, 70)

    async def go():
        iid, _, _ = await open_window(app, first)
        close_in_core_only(app, studio, iid)
        captured = {}
        original = COMMANDS["screen"]

        async def capture(owner, client, message):
            captured["reply"] = await original(owner, client, message)
            return captured["reply"]

        COMMANDS["screen"] = capture
        try:
            await studio.send_report()
        finally:
            COMMANDS["screen"] = original
        reply = captured["reply"]
        assert reply["ok"] is True and reply["partial"]["unresolved"][0]["items"] == [iid]

    asyncio.run(go())
