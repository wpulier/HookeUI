"""Failed-step retries through real App/Registry/native controls; synthetic renderer.

No model, camera, network search or ordinary ask is submitted. The first handler
failure is injected before production dispatch; the retry uses the real handler.
"""
import asyncio

import pytest

from orchestrator import execution, ticket as tickets
from orchestrator.brain.registry import Steps, ToolContext, ToolFailed
from orchestrator.commands import COMMANDS
from tests.test_workspace_actions_app import built, open_window, saved_picture, plan


async def failed_control(app, studio, iid, monkeypatch):
    await studio.send_report()
    request = tickets.issue(app.hub, "turn", asked=app.day.here(), channel="chat")
    original = app.brain.registry.tools["present"].handler

    async def fail_before_dispatch(ctx, args):
        raise ToolFailed("synthetic pre-dispatch failure")

    monkeypatch.setattr(app.brain.registry.tools["present"], "handler", fail_before_dispatch)
    with tickets.using(request), execution.request(app.hub, "ask"):
        pin = app.hub.screen.pin(studio, request_id=request.admission.request_id)
        ctx = ToolContext("chat", 7, None, "Keep the photo; zoom this window", asked=request.asked,
                          admission=request.admission, request_id=request.admission.request_id,
                          client=studio, request_snapshot=pin, workspace_id=studio.client_ref["workspaceId"])
        run = app.brain.registry.start("present", {"do": "zoom", "what": {"instanceId": iid},
                                                    "zoom": {"level": 2}}, ctx)
        await run.task
        root = execution.root_id()
    request.seal()
    monkeypatch.setattr(app.brain.registry.tools["present"], "handler", original)
    return run, request, root


def test_retry_only_failed_window_step_preserves_original_request_and_successful_work(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    app.hub.execution = execution.ExecutionLog(enabled=True)
    entry, _ = saved_picture(app)

    async def go():
        iid, successful, _ = await open_window(app, entry)
        failed, request, root = await failed_control(app, studio, iid, monkeypatch)
        original_output = [value.wire() for value in successful.outputs]
        available = await COMMANDS["operation-retry"](app, studio, {"receiptId": root})
        assert available["operations"][0]["available"] is True, available
        action = failed.occurrence.action_id
        first, replay = await asyncio.gather(*[
            COMMANDS["operation-retry"](app, studio, {"actionId": action}) for _ in range(2)])
        assert first == replay and first["ok"] is True, first
        assert await COMMANDS["operation-retry"](app, studio, {"actionId": action}) == first
        assert first["operation"]["requestId"] == request.admission.request_id
        assert first["operation"]["actionId"] != action
        assert len(app.services.presentations.list(studio.client_ref["workspaceId"])) == 1
        assert [value.wire() for value in successful.outputs] == original_output
        assert failed.ok is False  # original failure remains evidence
        assert request.admission.used == 0  # no model or original ask replay
        detail = app.hub.execution.inspect(first["receiptId"])
        assert detail["root"]["kind"] == "operation-retry"
        assert detail["root"]["turnId"] == 7
        assert detail["root"]["thread"] == request.asked.thread

    asyncio.run(go())


@pytest.mark.parametrize("boundary", ["peer", "disconnect", "cancel", "dispatched", "retained", "unexpected", "expired"])
def test_retry_refuses_lost_authority_or_ambiguous_effect(tmp_path, monkeypatch, boundary):
    app, studio = built(tmp_path)
    app.hub.execution = execution.ExecutionLog(enabled=True)
    entry, _ = saved_picture(app)

    async def go():
        iid, _, _ = await open_window(app, entry)
        failed, request, _ = await failed_control(app, studio, iid, monkeypatch)
        client = studio
        if boundary == "peer":
            client = object()
        elif boundary == "disconnect":
            app.hub.screen.gone(studio)
        elif boundary == "cancel":
            request.cancel("user stopped")
        elif boundary == "dispatched":
            failed.outcome = {"commandId": "already-issued"}
        elif boundary == "unexpected":
            failed.error = "RuntimeError: unexpected dispatch failure"
        elif boundary == "retained":
            failed.resource = {"id": "already-retained"}
        else:
            app.brain.registry.operation_retries.entries[failed.occurrence.action_id].at -= 301
        with pytest.raises(ToolFailed):
            await COMMANDS["operation-retry"](app, client, {"actionId": failed.occurrence.action_id})

    asyncio.run(go())


def test_historical_receipt_does_not_authorize_replaying_a_request(tmp_path):
    app, studio = built(tmp_path)
    result = app.brain.registry.operation_retries.inspect("old-process-receipt", studio)
    assert result["operations"] == [] and "Historical" in result["unavailable"]


def test_failed_photo_search_retries_original_lookup_only_then_confirms_new_window(tmp_path, monkeypatch):
    from orchestrator import reference

    app, studio = built(tmp_path)
    app.hub.execution = execution.ExecutionLog(enabled=True)
    _, pixels = saved_picture(app)
    web = {"page": "https://previous.example/photo", "title": "Prior reference"}
    record = app.hub.resources.results.add("reference_photo", {}, "Previously retained reference",
        picture_bytes=[pixels], web=web, source={"web": web})
    entry = app.hub.resources.get(record["id"], record["day"])
    calls = []

    def lookup(subject, n, **kwargs):
        calls.append((subject, n, kwargs))
        if len(calls) == 1:
            raise reference.NotFound("scripted source blocked")
        return reference.Found(pixels, 120, 80, "Synthetic reference", "https://example.org/photo",
                               "CC0", "Fixture", "Fixture", subject, n, 0.01)

    monkeypatch.setattr(reference, "find_blocking", lookup)

    async def go():
        iid, successful, _ = await open_window(app, entry)
        await studio.send_report()
        request = tickets.issue(app.hub, "turn", asked=app.day.here(), channel="chat")
        with tickets.using(request), execution.request(app.hub, "ask"):
            pin = app.hub.screen.pin(studio, request_id=request.admission.request_id)
            words = "Keep this open and find a different phase contrast photo from another website"
            ctx = ToolContext("chat", 8, None, words, asked=request.asked,
                admission=request.admission, request_id=request.admission.request_id,
                client=studio, request_snapshot=pin, workspace_id=studio.client_ref["workspaceId"])
            run = app.brain.registry.start("reference_photo", {
                "subject": "phase contrast stoma", "search_terms": ["phase contrast stoma"], "n": 2}, ctx)
            await run.task
            root = execution.root_id()
        request.seal()
        assert run.ok is False and not run.outputs and not run.resource
        status = await COMMANDS["operation-retry"](app, studio, {"receiptId": root})
        assert status["operations"][0]["available"] is True, status
        result = await COMMANDS["operation-retry"](app, studio, {"actionId": run.occurrence.action_id})
        assert result["ok"] is True, result
        assert calls[0] == calls[1]
        assert calls[1][2]["request_context"] == words
        assert calls[1][2]["previous_sources"][0]["page"] == "https://previous.example/photo"
        assert result["operation"]["requestId"] == request.admission.request_id
        windows = app.services.presentations.list(studio.client_ref["workspaceId"])
        assert len(windows) == 2 and any(window.id == iid for window in windows)
        assert successful.ok is True and run.ok is False
        assert result["operation"]["outcome"]["status"] == "confirmed"

    asyncio.run(go())


@pytest.mark.parametrize("predecessor", ["open", "resize"])
@pytest.mark.parametrize("external_edit", [None, "before_failure", "after_failure"])
@pytest.mark.parametrize("prior_round", [False, True])
def test_compound_retry_keeps_successful_predecessors_but_refuses_unrelated_edits(tmp_path, monkeypatch, predecessor, external_edit, prior_round):
    app, studio = built(tmp_path)
    app.hub.execution = execution.ExecutionLog(enabled=True)
    entry, _ = saved_picture(app)

    async def go():
        iid = None
        if predecessor == "resize":
            iid, _, _ = await open_window(app, entry)
        await studio.send_report()
        request = tickets.issue(app.hub, "turn", asked=app.day.here(), channel="chat")
        with tickets.using(request), execution.request(app.hub, "ask"):
            pin = app.hub.screen.pin(studio, request_id=request.admission.request_id)
            ctx = ToolContext("chat", 9, None, "Open this photo and zoom it", asked=request.asked,
                admission=request.admission, request_id=request.admission.request_id,
                client=studio, request_snapshot=pin, workspace_id=studio.client_ref["workspaceId"], step=1)
            args = ({"do": "open", "source": {"day": entry.day, "id": entry.id, "version": None, "picture": 0}}
                    if predecessor == "open" else {"do": "resize", "what": {"instanceId": iid}, "size": {"width": 400, "height": 300}})
            first = app.brain.registry.start("present", args, ctx)
            await first.task
            assert first.ok is True, first.summary
            if predecessor == "open":
                iid = first.outputs[0]["instanceId"]
            original_outputs = [out.wire() for out in first.outputs]

            async def edit():
                runs, _ = await plan(app, [("present", {"do": "zoom", "what": {"instanceId": iid}, "zoom": {"level": 3}})])
                assert runs[0].ok is True, runs[0].summary

            if external_edit == "before_failure":
                await edit()
            handler = app.brain.registry.tools["present"].handler
            async def fail(ctx, args):
                raise ToolFailed("synthetic pre-dispatch failure")
            monkeypatch.setattr(app.brain.registry.tools["present"], "handler", fail)
            if prior_round:
                ctx.prior_steps = (ctx.steps,)
                ctx.steps = Steps()
            ctx.step = 1 if prior_round else 2
            failed = app.brain.registry.start("present", {"do": "zoom", "what": {"instanceId": iid}, "zoom": {"level": 2}}, ctx)
            await failed.task
            monkeypatch.setattr(app.brain.registry.tools["present"], "handler", handler)
            root = execution.root_id()
            if external_edit == "after_failure":
                await edit()
        request.seal()
        result = app.brain.registry.operation_retries.inspect(root, studio)
        assert result["operations"][0]["available"] is (external_edit is None), result
        if external_edit:
            with pytest.raises(ToolFailed):
                await app.brain.registry.operation_retries.retry(failed.occurrence.action_id, studio)
        else:
            retried = await app.brain.registry.operation_retries.retry(failed.occurrence.action_id, studio)
            assert retried["ok"] is True, retried
            assert len(app.services.presentations.list(studio.client_ref["workspaceId"])) == 1
        assert first.ok is True and [out.wire() for out in first.outputs] == original_outputs
        assert failed.ok is False

    asyncio.run(go())
