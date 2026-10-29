"""The browser voice endpoint (wired in server.create_app; the studio's Talk button).

GPT-Live is the voice of the brain. It hears and speaks; every request about the view is
handed to the same brain you type to (orchestrator/brain/loop.py), so voice has exactly the
tools chat has. What you say and what it says go into the shared conversation, so switching
between talking and typing just works.

Protocol on WS /ws/voice[?model=gpt-live-1][&rate=24000][&link=webrtc]
  link=webrtc (option VOICELINK): the browser first sends {"type": "offer", "sdp": "..."} and gets
  {"type": "voice", "kind": "answer", "data": {"sdp": "..."}}; audio then goes browser <-> OpenAI
  over WebRTC, and this socket carries only events, text and mute (no binary audio).
  browser -> server  binary: 16-bit mono PCM at `rate` Hz (mic, sent continuously, ~40 ms
                             chunks; 24000 from current studios, 16000 when absent)
                     text:   {"type": "text", "text": "..."} typed message
                             {"type": "mute", "muted": true|false} (session.input_audio.mute)
  server -> browser  binary: b"MMA1" + 16-bit mono PCM at 24 kHz (model speech)
                     text:   {"type": "voice", "kind": <VoiceEvent kind>, ...} transcripts,
                             interrupted (browser must flush its playback buffer),
                             tool_call / tool_result, turn_complete, error, closed
"""
from __future__ import annotations

from research_worker import api_usage

import asyncio
import logging
import contextlib
import hashlib
import json
import time
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..options import VOICE
from .. import execution
from ..prompts import LIVE_VOICE_DELEGATE
from .diagnostics import VoiceDiagnostics
from .base import ToolSpec, VoiceConfig, VoiceEvent
from ..brain import context
from ..brain.speech import SpokenRequests
from .openai_live import OpenAILiveSession

AUDIO_MAGIC = b"MMA1"
VIEW_NOTE_EVERY_S = 3.0  # at most one view note this often (each is a thinking.append, 500 tokens max)
# A view note waits until nobody has spoken for this long: silent context arriving mid-turn can
# set GPT-Live talking about it (live 2026-10-06: replies 8-27 s after the user's last words).
NOTE_IDLE_S = 1.0
UNPROMPTED_AFTER_S = 6.0  # model speech this long after both the user and the app last prompted it
log = logging.getLogger(__name__)
SHUTDOWN_S = 10.0  # the provider's session closing, at most, before the route goes on
SETTLE_S = 5.0  # its allowance settled in the ledger, at most, before the route goes on
CAPPED = ("The monthly cap you set for cloud models is reached: talking has stopped. Raise or remove it in the spending "
          "panel.")
RECAP_LOGGED_CHARS = 32768  # the recap is logged whole up to this (it is about 1900 characters: brain/context.py)


def passive_workspace_pin(screen, client):
    """Conversation awareness uses the existing display-report lifetime, not Send's action deadline.

    This pin supplies context only. Spoken tool admission captures its own fresh
    request pin and retains the stricter action checks.
    """
    from ..screen import REPORT_FRESH_S

    return screen.pin(client, request_id=f'voice-context:{uuid4()}', timing='server-receive-legacy',
                      max_report_age_ms=REPORT_FRESH_S * 1000)


def add_voice_route(app: FastAPI, hub, brain, dialogue, workspace_transport=None, home=None) -> None:
    @app.websocket("/ws/voice")
    async def voice(websocket: WebSocket) -> None:
        from ..origin import socket_kind
        if socket_kind(websocket.headers, websocket.query_params, home) is None:  # ADR 0026: no Origin alone
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if getattr(hub, "local_only", lambda: False)():
            await websocket.send_json({"type": "voice", "kind": "error", "data": {"message": api_usage.LOCAL_ONLY}})
            await websocket.close(code=1008)
            return
        model = websocket.query_params.get("model") or hub.settings.voice
        if model not in VOICE:
            await websocket.send_text(json.dumps({"type": "voice", "kind": "error", "data": {"message": f"unknown voice model {model}"}}))
            await websocket.close()
            return
        client = None
        if workspace_transport is not None:
            try:
                binding = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
                if not isinstance(binding, dict) or set(binding) != {'type', 'connectionGrant'} or binding['type'] != 'workspace-bind':
                    raise ValueError('Reconnect to the studio before starting Talk.')
                client = workspace_transport.voice_client(binding['connectionGrant'])
            except Exception:
                await websocket.send_json({'type': 'voice', 'kind': 'error', 'data': {
                    'message': 'Talk could not join this Project’s window yet. Wait for the Project to finish opening, then press Talk again.'}})
                await websocket.close(code=1008)
                return
        spoken_requests = (SpokenRequests(brain, dialogue, client=client) if client is not None
                           else SpokenRequests(brain, dialogue))
        def workspace_pin():
            if client is None:
                return None
            return passive_workspace_pin(hub.screen, client)
        rate = 24000 if websocket.query_params.get("rate") == "24000" else 16000
        link = "webrtc" if websocket.query_params.get("link") == "webrtc" else "relay"
        # Each Talk press is a fresh GPT-Live session that remembers nothing: it starts knowing the
        # conversation so far, the researchers' work and what is on screen (`session.input`, as
        # the Live guide says, rather than a context update after it has started).
        conversation = getattr(brain, 'conversation', None)
        diagnostics = VoiceDiagnostics(hub, conversation.here() if conversation is not None else None, model)
        diagnostics.record("connecting")
        recap = ""
        initial_view_key = None
        initial_question_view = None
        with contextlib.suppress(Exception):
            pin = workspace_pin()
            recap = brain.voice_recap(request_snapshot=pin) if client is not None else brain.voice_recap()
            # session.input already contains this workspace. A second identical
            # thinking.append at ready can provoke an unsolicited spoken reply.
            moment = brain.moment(request_snapshot=pin) if client is not None else brain.moment()
            initial_view_key = context.view_key(moment)
            initial_question_view = getattr(brain.sight, "question_view", None)
        instructions = voice_prompt(brain)
        try:  # under a monthly cap the session is admitted first (its first minutes at their most), never after it opens
            usage_meter = api_usage.VoiceUsage(model)
        except (api_usage.CapReached, api_usage.ProviderRefused) as exc:
            await websocket.send_text(json.dumps({"type": "voice", "kind": "error", "data": {"message": str(exc)[:300]}}))
            await websocket.close()
            return
        session = OpenAILiveSession(VoiceConfig(model=model, system_prompt=instructions, tools=[brain_tool(spoken_requests)],
                                                input_rate=rate, history=[("developer", recap)] if recap else []), link=link,
                                    said=getattr(brain, "said", None))  # the brain's: what an earlier Talk said stays said
        session.noted = brain.agent.log  # every silent note, whole, as a voice-note record
        spoken_requests.voice = session
        # Which request a delegation is (its identity only). Not `spoken`: that would let any later words, a cough or a
        # "thanks" included, turn the narrator's late results into silent context (Announcer._newest).
        session.requests = spoken_requests
        # The journal and the talking window render the same line IDs. Raw voice events
        # provide status only; two sockets must never independently construct chat rows.
        spoken_requests.user_line.caption = spoken_requests.reply_line.caption = True
        spoken_requests.user_line.join = ""
        try:
            if link == "webrtc":
                offer = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=15))
                await session.start(offer_sdp=str(offer.get("sdp") or ""))
                await websocket.send_text(json.dumps({"type": "voice", "kind": "answer", "data": {"sdp": session.answer_sdp}}))
            else:
                await session.start()
        except BaseException as exc:
            diagnostics.record("error")
            # Whatever it acquired (a connection, a created WebRTC session, its sideband) is closed first, then its
            # allowance settled, also when cancelled, once or again (Codex's diagnosis of 89e834b5, point 4; its review of
            # 2026-10-14): the route owns that cleanup to its end.
            await finish_owned(close_then_settle((), session, usage_meter))
            if not isinstance(exc, Exception):
                raise
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps({"type": "voice", "kind": "error", "data": {"message": f"{type(exc).__name__}: {exc}"[:300]}}))
            with contextlib.suppress(Exception):
                await websocket.close()
            return
        try:  # everything after the provider's session is acquired is owned by its cleanup (Codex's recheck of 73626a0d)
            session.browser_client = client
            brain.voice = session
            with execution.request(hub, "voice-session") as receipt:
                if receipt.id is not None:
                    with contextlib.suppress(Exception):
                        with execution.span("live-session", selected=model, selection="explicit" if websocket.query_params.get("model") else "settings",
                                            adapter="voice.openai_live.OpenAILiveSession", provider="openai", model=session.config.model,
                                            scope=link, sessionId=getattr(session, "session_id", None), identity="session-start sent; hosted revision unconfirmed"):
                            pass
            await websocket.send_text(json.dumps({"type": "voice", "kind": "model", "data": {"model": model, "label": VOICE[model].label}}))
            # What this session started knowing: its instructions by their hash (the same text every session until a
            # prompt or the tool list changes) and the recap whole, so a session's answers can be read against them.
            brain.agent.log({"kind": "voice-session", "link": link, "sessionId": getattr(session, "session_id", None),
                             "instructions": {"sha256": _sha(instructions), "chars": len(instructions)},
                             "recap": recap if len(recap) <= RECAP_LOGGED_CHARS else
                             recap[:RECAP_LOGGED_CHARS] + f"… [cut: {len(recap) - RECAP_LOGGED_CHARS} more characters]",
                             "recapChars": len(recap), "recapSha256": _sha(recap) if recap else None, **hub.settings.to_wire()})
        except BaseException as exc:
            try:
                await finish_owned(close_then_settle((), session, usage_meter))  # owned to its end, as at the start and end
            finally:  # the app's own hold on it let go whatever ends that wait (a cancellation is raised after settling)
                if brain.voice is session:
                    brain.voice = None
                spoken_requests.close()
            if not isinstance(exc, Exception):
                raise
            with contextlib.suppress(Exception):
                await websocket.close()
            return

        # Timing, logged per reply ("voice-reply") and per session ("voice-session-end"), so
        # how fast the voice answers live is measured, not guessed (make voice-report).
        timing = {"opened": time.monotonic(), "user_at": 0.0, "waiting": False, "replies": 0, "delegations": 0,
                  "delegated": False, "first": [], "lags": [], "last_end_ms": None, "note_at": 0.0, "turn_logged": False,
                  "unprompted": 0}

        ended = asyncio.Event()  # the session is over for the app's own reason (the cap): the route stops waiting
        provider_ready = asyncio.Event()

        async def end_for_cap() -> None:
            """The provider first, whatever the browser does (Codex's diagnosis of 89e834b5, point 3); then the user is
            told and the browser closed, each on its own; then the route stops waiting for the browser."""
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.close(), timeout=SHUTDOWN_S)
            ended.set()
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps({"type": "voice", "kind": "error", "data": {"message": CAPPED}}))
            with contextlib.suppress(Exception):
                await websocket.close()

        async def pump_out() -> None:
            async for event in session.events():
                if event.kind == 'ready':
                    provider_ready.set()
                if event.kind == 'error':
                    diagnostics.provider_error(event.data)
                else:
                    diagnostics.record(event.kind)
                if event.kind == "usage":
                    await asyncio.to_thread(usage_meter.update, (event.data or {}).get("seconds"))
                    try:
                        fits = await asyncio.to_thread(usage_meter.renew)
                    except Exception:  # noqa: BLE001 - an allowance that can't be renewed is not held: it ends
                        fits = False
                    if not fits:  # the month's cap reached while talking: said, and the session ends
                        await end_for_cap()
                        return
                if event.kind == "tool_call":
                    spoken_requests.handed_over()
                    timing["delegated"] = True
                    timing["delegations"] += 1
                    # How long after the user's last words the voice handed over, by our clock
                    # and by the server's (offsetMs against the last piece's endMs).
                    server = event.data or {}
                    brain.agent.log({"kind": "voice-handoff", "id": event.call_id,
                                     "afterWordsMs": round((event.at - timing["user_at"]) * 1000) if timing["user_at"] else None,
                                     "offsetMs": server.get("offsetMs"), "offsetLagMs": server.get("offsetLagMs"),
                                     "lastEndMs": timing["last_end_ms"],
                                     "serverGapMs": (server["offsetMs"] - timing["last_end_ms"])
                                     if isinstance(server.get("offsetMs"), (int, float)) and timing["last_end_ms"] is not None else None})
                if event.kind == "input_transcript" and event.data.get("interim"):
                    # Each piece of the user's speech as it arrives; the final transcript is only
                    # emitted when the model starts answering, so it says nothing about the wait.
                    if not timing["waiting"] and brain.sight is not None and hasattr(brain.sight, "question_coming"):
                        # The first words of an utterance: the eyes look again now if the view may have changed,
                        # so the notes are fresh by the time the user finishes (ADR 0016's See loop).
                        from ..brain.toolbox.time import motion_share

                        asyncio.create_task(brain.sight.question_coming(motion_share(hub)))
                    timing["user_at"], timing["waiting"], timing["delegated"] = event.at, True, False
                    if isinstance(event.data.get("endLagMs"), (int, float)):
                        timing["lags"].append(event.data["endLagMs"])
                        timing["last_end_ms"] = event.data.get("endMs")
                elif event.kind == "output_transcript" and timing["waiting"] and timing["user_at"]:
                    # The first words of a reply: how long after the user's last words.
                    first = round((event.at - timing["user_at"]) * 1000)
                    timing["waiting"] = False
                    timing["replies"] += 1
                    timing["first"].append(first)
                    brain.agent.log({"kind": "voice-reply", "firstWordsMs": first, "delegated": timing["delegated"]})
                elif event.kind == "output_transcript" and not timing["turn_logged"]:
                    # Speech nobody asked for just now: measured, so whether notes set it off is known.
                    now = event.at
                    since_user = now - timing["user_at"] if timing["user_at"] else None
                    since_app = now - session.last_commentary_at if session.last_commentary_at else None
                    if (since_user is None or since_user > UNPROMPTED_AFTER_S) and (since_app is None or since_app > UNPROMPTED_AFTER_S):
                        timing["unprompted"] += 1
                        brain.agent.log({"kind": "voice-unprompted", "text": event.text[:80],
                                         "sinceUserMs": round(since_user * 1000) if since_user is not None else None,
                                         "sinceNoteMs": round((now - timing["note_at"]) * 1000) if timing["note_at"] else None})
                elif event.kind == "tool_result":
                    brain.agent.log({"kind": "voice-delegation", "ms": (event.data or {}).get("ms"), "id": event.call_id})
                elif event.kind == "note_ack":
                    timing["note_at"] = event.at
                if event.kind == "output_transcript":
                    timing["turn_logged"] = True
                elif event.kind in ("turn_complete", "interrupted"):
                    timing["turn_logged"] = False
                if event.kind in ("input_transcript", "output_transcript"):
                    event.data["journaled"] = True
                if event.kind == "input_transcript" and event.data.get("interim"):
                    spoken_requests.user_line.add(event.data.get("delta", ""), span=event.data)
                if event.kind == "input_transcript" and not event.data.get("interim"):
                    # Shown, and joined with what comes close after; its span on the server's clock is
                    # how a delegation is matched to it.
                    spoken_requests.heard(event.text, span=(event.data.get("startMs"), event.data.get("endMs")))
                elif event.kind == "output_transcript":
                    spoken_requests.said(event.text, span=event.data)  # one authoritative caption
                elif event.kind == "turn_complete" and (event.data or {}).get("reason") != "user_spoke":
                    spoken_requests.reply_done()  # the voice went quiet; the user speaking over it ends nothing
                if event.kind == "audio":
                    await websocket.send_bytes(AUDIO_MAGIC + event.audio)
                else:
                    await websocket.send_text(json.dumps(_wire(event)))
                if event.kind == "closed":
                    ended.set()
            ended.set()

        async def keep_seeing() -> None:
            """The voice sees through view notes: what the app's running look sees and what is on
            screen, given as silent context when the session opens and whenever it changes, so
            "what are we looking at?" is answered at once (GPT-Live itself takes no images)."""
            # A sent session.start is not acknowledgement. Context updates, like
            # microphone audio, must wait for the provider's ready event.
            await provider_ready.wait()
            sight = brain.sight
            if sight is not None and (sight.view is None or not sight.view.current(hub.latest_info)):
                with contextlib.suppress(Exception):
                    await sight.look()
            last, sent_at, last_asked = initial_view_key, 0.0, initial_question_view
            while not session.closed:
                if getattr(hub, "local_only", lambda: False)():
                    await session.close()
                    ended.set()
                    return
                if client is not None:
                    try:
                        workspace_transport.voice_client(binding['connectionGrant'])
                    except Exception:
                        await websocket.close(code=1008)
                        return
                    try:
                        pin = workspace_pin()
                    except Exception:
                        await asyncio.sleep(0.5)
                        continue  # no current report: never substitute another window's view
                    moment = brain.moment(request_snapshot=pin)
                else:
                    moment = brain.moment()
                key = context.view_key(moment)  # a note only when what it says has changed
                now = time.monotonic()
                chat = list(brain.voice_notes)
                # The look the user's first words asked for goes out at once, while they are still talking (the
                # model is not), so the voice answers from the view now (ADR 0016's See loop); others wait as before.
                asked = getattr(sight, "question_view", None)
                fresh = asked is not None and asked is not last_asked and getattr(sight, "view", None) is asked
                due = chat or fresh or (key != last and (last is None or now - sent_at >= VIEW_NOTE_EVERY_S))
                if due and (session.idle_since() >= NOTE_IDLE_S
                            or (fresh and getattr(session, "user_speaking", lambda: False)())):
                    brain.voice_notes.clear()
                    note = context.view_note(moment)
                    text = note + (" Meanwhile in the chat (already answered there): " + " ".join(chat) if chat else "")
                    why = [reason for reason, held in (("session start", last is None),
                                                       ("view changed", last is not None and key != last),
                                                       ("chat notes", bool(chat)),
                                                       ("the look the user's first words asked for", fresh)) if held]
                    facts = {"why": why, **look_facts(hub, getattr(moment, "view", None))}
                    with contextlib.suppress(Exception):
                        await session.inject(text[:1900], respond=False, source="view", facts=facts)
                    last, sent_at, last_asked = key, now, asked if fresh else last_asked
                await asyncio.sleep(0.5)

        async def keep_allowance() -> None:
            """Under a monthly cap, its next minutes renewed by the clock, usage events or not: ended when they don't fit,
            and when a renewal fails for any reason (an allowance not renewed is not held: fail closed)."""
            while True:
                await asyncio.sleep(api_usage.RENEW_S)
                try:
                    fits = await asyncio.to_thread(usage_meter.renew)
                except Exception:  # noqa: BLE001
                    fits = False
                if not fits:
                    await end_for_cap()
                    return

        async def deliver_responses() -> None:
            try:
                await pump_out()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failed consumer used to look like an unexplained disconnection.
                # Keep provider payloads private, but retain which boundary failed.
                diagnostics.record('response-failed')
                log.warning('voice response delivery failed: %s', type(exc).__name__)
                with contextlib.suppress(Exception):
                    await websocket.send_text(json.dumps({'type': 'voice', 'kind': 'error', 'data': {
                        'message': 'Hooke could not deliver the voice response. Your conversation is kept. Check Activity, then start Talk again.'}}))
            finally:
                ended.set()

        tasks = [asyncio.create_task(deliver_responses()), asyncio.create_task(keep_seeing()), asyncio.create_task(keep_allowance())]
        tasks[0].add_done_callback(lambda _: ended.set())
        try:
            while not ended.is_set():
                if getattr(hub, "local_only", lambda: False)():
                    break
                receiving = asyncio.ensure_future(websocket.receive())
                stop = asyncio.ensure_future(ended.wait())
                done, _ = await asyncio.wait({receiving, stop}, return_when=asyncio.FIRST_COMPLETED)
                if receiving not in done:
                    receiving.cancel()
                    with contextlib.suppress(BaseException):
                        await receiving
                    break
                stop.cancel()
                message = receiving.result()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    if not provider_ready.is_set():
                        continue  # never buffer early speech for a future session
                    diagnostics.record("audio-received")
                    await session.send_audio(message["bytes"])
                elif message.get("text"):
                    command = json.loads(message["text"])
                    if command.get("type") == "diagnostic":
                        diagnostics.record(command.get("stage"), client=True)
                    elif command.get("type") == "text":
                        await session.send_text(str(command.get("text", "")))
                    elif command.get("type") == "mute":
                        await session.mute(bool(command.get("muted")))
        except WebSocketDisconnect:
            pass
        except Exception:
            # An upstream write can race a provider close. Keep cleanup owned by this
            # route and report the lost connection rather than escaping the ASGI task.
            diagnostics.record('error')
            with contextlib.suppress(Exception):
                await websocket.send_json({'type': 'voice', 'kind': 'error', 'data': {
                    'message': 'The voice connection ended. Check provider access and your connection, then start Talk again.'}})
        finally:
            diagnostics.record("ended")
            # Its tasks reaped and the provider closed before its allowance is settled (Codex's diagnosis of 89e834b5,
            # point 6: a renewal still running finishes first, under the meter's lock, and none can follow finish).
            for task in tasks:
                task.cancel()
            first = sorted(timing["first"])
            with contextlib.suppress(Exception):
                lags = timing["lags"]
                brain.agent.log({"kind": "voice-session-end", "seconds": round(time.monotonic() - timing["opened"]),
                                 "replies": timing["replies"], "delegations": timing["delegations"],
                                 "unprompted": timing["unprompted"],
                                 "firstWordsP50Ms": first[len(first) // 2] if first else None,
                                 # The server's word times against our clock: steady lag can be
                                 # subtracted, drift (last minus first) cannot.
                                 "lagP50Ms": sorted(lags)[len(lags) // 2] if lags else None,
                                 "lagDriftMs": lags[-1] - lags[0] if len(lags) > 1 else None, "lagSamples": len(lags)})
            spoken_requests.close()
            if brain.voice is session:
                brain.voice = None
            # Its tasks, then the provider closed, then its allowance settled, in that order and bounded, as one task the
            # route owns to its end: a cancellation (the browser or the server going away, once or again) waits for it and
            # is raised after, so the hold is never settled before the provider is closed, nor left held.
            await finish_owned(close_then_settle(tasks, session, usage_meter))


_LATE: set = set()  # closes and settlements still running past their bound: supervised to their end, never awaited


def _outcome(task) -> str:
    """How a finished task ended, read so no exception goes unretrieved: "" when it returned."""
    if task.cancelled():
        return "cancelled"
    exc = task.exception()
    return f"{type(exc).__name__}: {exc}" if exc is not None else ""


def _late(task, said) -> None:
    """A step past its bound, supervised to its end: kept until it ends, then its outcome read and `said`."""
    _LATE.add(task)

    def ended(t) -> None:
        _LATE.discard(t)
        said(_outcome(t))

    task.add_done_callback(ended)


def _settlement_failed(usage_meter, why: str) -> None:
    """A settlement that failed is a tracking gap Spending already shows ("Some usage could not be saved"), and a log line:
    never a silent hold."""
    ledger = getattr(usage_meter, "ledger", None)
    if ledger is not None:
        ledger.failures += 1
    log.warning("voice session's settlement failed: %s", why)


async def close_then_settle(tasks, session, usage_meter) -> None:
    """A voice session's end, bounded throughout (Codex's reviews of 3023699e): its tasks given SHUTDOWN_S to stop, then
    the provider's close awaited at most SHUTDOWN_S (asyncio.wait returns at its deadline even when the close delays its
    own cancellation), and only after that its allowance settled, at most SETTLE_S. Settled as released only when the
    provider's end is confirmed (its close returned and said so, `termination`); otherwise its minutes ahead stay counted
    against the limit as an unknown cost (api_usage's tracking gap), never declared released."""
    if tasks:
        await asyncio.wait(tasks, timeout=SHUTDOWN_S)
    closing = asyncio.ensure_future(session.close())
    done, _ = await asyncio.wait({closing}, timeout=SHUTDOWN_S)
    if closing not in done:
        closing.cancel()
        _late(closing, lambda why: why and log.warning("voice session's late close ended: %s", why))
        termination = f"unconfirmed: its close didn't end within {SHUTDOWN_S:.0f} s"
    elif closing.cancelled() or closing.exception() is not None:
        termination = "unconfirmed: its close failed"
    else:
        termination = str(getattr(session, "termination", "") or "confirmed")
    # The browser event pump has stopped; retain the provider's final total before
    # settling. Both writes share one bounded off-loop task, in this exact order.
    seconds = getattr(session, "usage_seconds", None)
    def settle():
        if seconds is not None:
            usage_meter.update(seconds)
        usage_meter.finish(termination == "confirmed", termination)
    settling = asyncio.ensure_future(asyncio.to_thread(settle))
    done, _ = await asyncio.wait({settling}, timeout=SETTLE_S)
    if settling in done:
        failed = _outcome(settling)
        if failed:
            _settlement_failed(usage_meter, failed)
    else:  # a thread can't be stopped: its settlement finishes on its own, supervised, and a late failure is said too
        _late(settling, lambda why: why and _settlement_failed(usage_meter, f"{why} (after {SETTLE_S:.0f} s)"))


async def finish_owned(cleanup) -> None:
    """Run `cleanup` to its end even when the caller is cancelled, once or repeatedly; then raise that cancellation.
    Its own failure is raised as it is."""
    task = asyncio.ensure_future(cleanup)
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():  # the cleanup itself was cancelled: nothing left to wait for
                raise
            cancelled = True  # ours: keep waiting for the cleanup, never detach it
    if cancelled:
        raise asyncio.CancelledError()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def look_facts(hub, view) -> dict:
    """How old the running look behind a view note is (sight.SightView: when it was made, and when its frame was
    taken), for the voice-note record; the newest frame's id beside it, so a note's staleness can be read."""
    latest = getattr(getattr(hub, "latest_info", None), "id", None)
    if view is None:
        return {"lookFrameId": None, "latestFrameId": latest}
    try:  # a log's facts never cost the note
        now = time.time()
        frame_at = getattr(view, "frame_at", 0.0)
        if not frame_at:  # a look made before it kept its frame's time: the frame's facts, while the hub still holds them
            frame_at = getattr((getattr(hub, "infos", None) or {}).get(view.frame_id), "wall_at", 0.0)
        return {"lookAgeS": round(now - view.at, 1), "lookFrameId": view.frame_id,
                "lookFrameAgeS": round(now - frame_at, 1) if frame_at else None, "latestFrameId": latest}
    except Exception:
        return {"latestFrameId": latest}


def brain_tool(spoken_requests: SpokenRequests) -> ToolSpec:
    """GPT-Live hands every request to the brain (client delegation): the brain looks, acts
    with its tools, and its findings come back for GPT-Live to say in its own words."""

    async def ask_brain(args: dict) -> dict:
        # The delegation carries no task of its own: it is matched to a request heard by its offset.
        return await spoken_requests.handoff(str(args.get("question") or ""), args.get("delegation_id"), say=args.get("say"),
                                             offset_ms=args.get("offset_ms"))

    return ToolSpec(name="ask_app", description="Hand the request to the app, which sees the view and acts.",
                    parameters={"type": "object"}, handler=ask_brain)


def voice_prompt(brain) -> str:
    """GPT-Live's instructions, listing what the brain's tools can do by family (brain/menu.py;
    from the registry, so a tool added for chat is known to voice too, and "what can you do?"
    is answered in families)."""
    from ..brain.menu import voice_abilities

    return LIVE_VOICE_DELEGATE.format(abilities=voice_abilities(list(brain.registry.tools.values())))


def _wire(event: VoiceEvent) -> dict:
    payload = {"type": "voice", "kind": event.kind}
    if event.text:
        payload["text"] = event.text
    if event.name:
        payload.update(name=event.name, callId=event.call_id)
    if event.args:
        payload["args"] = {k: v for k, v in event.args.items() if k != "delegation_id"}
    if event.result is not None:
        payload["result"] = event.result
    if event.data:
        payload["data"] = event.data
    return payload
