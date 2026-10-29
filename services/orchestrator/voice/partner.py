"""The one-brain voice's side of the app: what it sees each turn, the Project brief, the studio link, the shared
conversation, lines nobody asked for and reconnecting (PARTNER-CONTRACT; docs/architecture/seeing-and-acting.md).

`Partner` binds one `RealtimeSession` (voice/openai_realtime.py) to the app for one Talk session:
  - **seeing, at key-down:** the camera's eyepiece crop (`scene_tools.view_frame`, grabbed off the loop the moment the
    key goes down), the studio's window picture (it sends `{type:"snapshot", id:"talk-<n>"}` unsolicited right after
    `{type:"talk", down:true, n}`) and the screen now as text (the accepted studio's report: `context._voice_windows`).
    Release uses what is there; nothing is fetched after it;
  - **the Project brief:** `project_state.brief`, refreshed at each key-down and replaced in the session only when it
    changed (it holds no times, so an unchanged Project gives the same text);
  - **the studio link:** `snapshot-request` (act, then look) and `marks`, each answered by the studio
    (`snapshot`, `marks-shown`) within its bound or not at all;
  - **one conversation with chat:** the user's words and the voice's are filed as the GPT-Live path files them (the
    same journal lines, `SpokenRequests`' caption lines), never handed to its hands (the model acts itself);
  - **lines nobody asked for:** a researcher finished or failed (the research manager's `on_done`), a long tool done,
    a capability turned on (`happened`, called by the app), a reconnect: one sentence when nobody is talking, pointing
    at the card;
  - **reconnecting:** a call that ended asks the studio for a new offer (`reoffer`); a sideband that dropped is
    reattached; either way the same session object goes on, seeded again.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import itertools
import json
import os
import time
from typing import Any, Awaitable, Callable

from .openai_realtime import Perception, RealtimeSession
from .partner_tools import PartnerTools

SNAPSHOT_WAIT_S = 1.5  # the studio answers a snapshot-request within this or the look goes on without it
OFFER_WAIT_S = 5.0  # a reconnect waits this long for the browser's new offer
SCREEN_CHARS = 1000  # the screen now as text, at most
NOTICE_TAG_S = 10.0  # a researcher's finish is matched to the narrator's line about it within this long
BRIEF_CHARS = 3000
REATTACH_AGAIN_S = 10.0  # a reattached sideband dropping again within this: the call is gone, so a new one


class Partner:
    def __init__(self, hub, brain, dialogue=None, *, client=None, send: Callable[[dict], Awaitable[None]] | None = None,
                 log: Callable[[dict], None] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        from ..brain.speech import SpokenRequests

        self.hub, self.brain, self.dialogue, self.client = hub, brain, dialogue, client
        self.send = send  # to the studio over /ws/voice
        self.log = log or (lambda record: None)
        self.clock = clock
        self.session: RealtimeSession | None = None
        self.tools = PartnerTools(brain, hub, self, client=client, dialogue=dialogue)
        # The journal's lines, as the GPT-Live path writes them; its hands are never used (the model acts itself).
        self.lines = SpokenRequests(brain, dialogue, client=client) if client is not None else SpokenRequests(brain, dialogue)
        self.lines.user_line.caption = self.lines.reply_line.caption = True
        self.lines.user_line.join = ""
        self.last_frame: dict | None = None  # {id, crop} of the camera picture the model saw last
        self._seen: dict[int, dict] = {}  # turn -> what was captured at its key-down
        self._heard: dict[int, str] = {}
        self._heard_at: dict[int, asyncio.Event] = {}
        self._waiting: dict[str, asyncio.Future] = {}  # snapshot-request, marks and reoffer ids -> the studio's reply
        self._ids = itertools.count(1)
        self._tasks: set = set()
        self._research = None
        self._research_seen: tuple | None = None  # (rid, event, at): tags the narrator's line about it
        self._tools_named: tuple = ()
        self.reconnecting: asyncio.Task | None = None
        self.fault: str | None = None
        self._talk_lock = asyncio.Lock()
        self._released: set[int] = set()  # turns whose key was released (or cancelled): their perception is fixed
        self._reattached_at = -1e9  # a sideband reattached that drops again this soon means its call has ended

    # --- binding -----------------------------------------------------------------------------------
    def bind(self, session: RealtimeSession) -> None:
        self.session = session
        session.turn_items = self.turn_items
        session.on_speech_started = self.capture
        session.history = self.history
        session.tag_notice = self.tag_notice
        session.happened = self.happened  # the app's own events (App.turned_on) reach it through brain.voice
        session.on(self.on_event)
        self.lines.voice = session
        self._tools_named = tuple(self.tools.names())
        research = getattr(getattr(self.brain, "services", None), "research", None)
        if research is not None and isinstance(getattr(research, "on_done", None), list):
            research.on_done.append(self._research_done)
            self._research = research

    def close(self) -> None:
        if self._research is not None:
            with contextlib.suppress(ValueError):
                self._research.on_done.remove(self._research_done)
        for future in self._waiting.values():
            if not future.done():
                future.cancel()
        self.lines.close()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def turn(self) -> int:
        return self.session.n if self.session is not None else 0

    # --- the studio's messages ---------------------------------------------------------------------
    async def command(self, message: dict) -> bool:
        """One message from the studio for the partner; False when it is not one."""
        kind = message.get("type")
        if kind == "talk":
            if message.get("down") and not message.get("cancel") and self.session is not None:
                # What the turn sees is taken now, as the message arrives, before anything awaits: the studio's window
                # picture follows at once on the same socket and must find its turn (p25-rt-studio's review).
                n = message.get("n") if isinstance(message.get("n"), int) else self.session.n + 1
                self.capture(n)
            elif self.session is not None:
                self._released.add(message.get("n") if isinstance(message.get("n"), int) else self.session.n)
            # In order, and never holding up the socket's reader (a release waits for its tail and its response).
            self._spawn(self._talk_in_order(message))
        elif kind == "snapshot":
            self.got_snapshot(message)
        elif kind == "marks-shown":
            self._answer(str(message.get("id") or ""), message)
        elif kind == "offer" and message.get("id"):
            self._answer(str(message["id"]), message)
        elif kind == "bench" and os.environ.get("MICROMIND_BENCH_FAULTS") == "1":
            await self.bench_fault(str(message.get("fault") or ""))
        else:
            return False
        return True

    async def _talk_in_order(self, message: dict) -> None:
        async with self._talk_lock:
            try:
                await self.talk(message)
            except Exception as exc:  # a lost sideband is reconnected by the session's own watch; said in the log
                self.log({"kind": "partner-talk-failed", "error": f"{type(exc).__name__}: {exc}"[:300]})

    async def talk(self, message: dict) -> None:
        session = self.session
        n = message.get("n") if isinstance(message.get("n"), int) else None
        if message.get("cancel"):
            await session.talk_cancel(n)
            self._seen.pop(session.n, None)
        elif message.get("down"):
            if session.speaking():
                self._spawn(self.clear_marks())
            await session.talk_down(n)
            await self._tools_changed()
        else:
            await session.talk_up(n)

    def capture(self, n: int) -> None:
        """What this turn sees, taken now: the camera off the loop, the screen as text, the brief refreshed."""
        from .scene_tools import view_frame

        seen = self._seen.setdefault(n, {"window": None})  # a window picture that came first is kept
        seen["screen"] = self.screen_now()
        seen["at"] = time.time()
        loop = asyncio.get_running_loop()
        seen["camera_task"] = loop.run_in_executor(None, view_frame, self.hub, 768)
        for old in [k for k in self._seen if k < n - 2]:
            self._seen.pop(old, None)
        self._spawn(self.refresh_brief())
        if self.session is not None and self.session.talk == "open":
            self._spawn(self._open_window(n))

    async def _open_window(self, n: int) -> None:
        """Open mic has no key-down: the window picture is asked for as the user starts speaking."""
        shot = await self.snapshot()
        if shot is not None and n in self._seen:
            self._seen[n]["window"] = shot

    def got_snapshot(self, message: dict) -> None:
        sid = str(message.get("id") or "")
        shot = None
        if message.get("jpeg") and not message.get("error"):
            with contextlib.suppress(Exception):
                shot = {"jpeg": base64.b64decode(message["jpeg"]), "how": message.get("how"), "at": message.get("at"),
                        "w": message.get("w"), "h": message.get("h")}
        if sid.startswith("talk-"):
            with contextlib.suppress(ValueError):
                n = int(sid.split("-", 1)[1])
                if n not in self._released and n >= self.turn():  # after release it is too late for that turn: dropped
                    self._seen.setdefault(n, {"window": None})["window"] = shot
            return
        self._answer(sid, shot)

    def _answer(self, rid: str, value) -> None:
        future = self._waiting.get(rid)
        if future is not None and not future.done():
            future.set_result(value)

    async def _ask(self, message: dict, timeout: float):
        rid = message["id"]
        future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = future
        try:
            if self.send is None:
                return None
            await self.send(message)
            return await asyncio.wait_for(future, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionError, RuntimeError):
            return None
        finally:
            self._waiting.pop(rid, None)

    async def snapshot(self, max_side: int = 1280, timeout: float = SNAPSHOT_WAIT_S) -> dict | None:
        return await self._ask({"type": "snapshot-request", "id": f"look-{next(self._ids)}", "maxSide": max_side}, timeout)

    async def marks(self, marks: list[dict], *, frame: dict | None = None, until: Any = "speech-end",
                    timeout: float = 1.0) -> dict | None:
        message = {"type": "marks", "id": f"mk-{next(self._ids)}", "marks": marks, "until": until,
                   **({"frame": frame} if frame else {})}
        sent = time.time()
        shown = await self._ask(message, timeout)
        self.log({"kind": "partner-marks", "id": message["id"], "targets": [m.get("target") for m in marks],
                  "sentT": sent, "shownT": time.time() if shown is not None else None,
                  "resolved": (shown or {}).get("resolved")})
        return shown

    async def clear_marks(self) -> None:
        if self.send is not None:
            with contextlib.suppress(Exception):
                await self.send({"type": "marks-clear"})

    # --- what a turn sees --------------------------------------------------------------------------
    def screen_now(self) -> str:
        """The studio's report as text: its Tab, windows and which is in front (the accepted studio's own report)."""
        screen = getattr(self.hub, "screen", None)
        if screen is None:
            return ""
        try:
            if self.client is not None:
                from ..brain.context import _voice_windows
                from .route import passive_workspace_pin

                lines = _voice_windows(passive_workspace_pin(screen, self.client))
            else:
                lines = list(screen.lines())
        except Exception:
            return ""
        return "; ".join(lines)[:SCREEN_CHARS]

    async def camera(self, max_side: int = 768):
        from .scene_tools import view_frame

        return await asyncio.get_running_loop().run_in_executor(None, view_frame, self.hub, max_side)

    def turn_items(self, n: int) -> Perception:
        """What turn n saw at its key-down, as it is now: never waited for."""
        seen = self._seen.get(n) or {}
        images, facts = [], {"camera": False, "window": None, "screenNowChars": len(seen.get("screen") or "")}
        camera = seen.get("camera_task")
        frame_id = None
        if camera is not None and camera.done() and not camera.cancelled() and camera.exception() is None \
                and camera.result() is not None:
            jpeg, frame_id, crop = camera.result()
            self.last_frame = {"id": frame_id, "crop": crop}
            images.append((jpeg, f"Camera, frame {frame_id}: what the microscope showed when the user pressed talk"))
            facts["camera"] = True
        window = seen.get("window")
        if window is not None:
            images.append((window["jpeg"], f"Hooke's window when the user pressed talk ({window.get('how') or 'composed'})"))
            age = round(time.time() * 1000 - window["at"]) if isinstance(window.get("at"), (int, float)) else None
            facts["window"] = {"how": window.get("how"), "ageMs": age}
        when = time.strftime("%H:%M:%S", time.localtime(seen.get("at") or time.time()))
        note = (f"Turn {n} at {when}. Screen now: {seen.get('screen') or 'no report from the studio'}. "
                + (f"Camera frame {frame_id}." if frame_id is not None else "No fresh camera frame.")
                + ("" if window is not None else " No window picture this turn."))
        return Perception(note, images, facts)

    async def words(self, n: int, timeout: float) -> str:
        """What the user said on turn n (its transcription), waiting at most `timeout` for it."""
        if n in self._heard:
            return self._heard[n]
        event = self._heard_at.setdefault(n, asyncio.Event())
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout)
        return self._heard.get(n, "")

    # --- the Project brief ----------------------------------------------------------------------------
    async def brief(self) -> tuple[str, str]:
        """The Project as the brief says it (project_state.brief), or its full text cut, or the voice recap; with its
        revision (the text's digest: the brief holds no times)."""
        text = ""
        try:
            from .. import project_state
            from ..resources import resources_of

            project = resources_of(self.hub).session()
            state = await project_state.state_of(self.hub).read(project, project_state.OWNER)
            brief = getattr(project_state, "brief", None)
            text = brief(state, max_chars=BRIEF_CHARS) if brief is not None else project_state.text(state)[:BRIEF_CHARS]
        except Exception:
            with contextlib.suppress(Exception):
                text = self.brain.voice_recap()[:BRIEF_CHARS]
        return text, hashlib.sha256(text.encode()).hexdigest()[:16]

    async def refresh_brief(self) -> None:
        text, revision = await self.brief()
        if text and self.session is not None and not self.session.closed:
            with contextlib.suppress(Exception):
                await self.session.set_brief(text, revision)

    def history(self) -> list[tuple[str, str]]:
        """The last 8 exchanges of the shared conversation, typed or spoken, for a new call."""
        out = []
        with contextlib.suppress(Exception):
            for turn in self.brain.conversation.window(8):
                if turn.role in ("user", "assistant") and turn.text.strip():
                    out.append((turn.role, turn.text.strip()[:1200]))
        return out

    async def _tools_changed(self) -> None:
        """A capability turned on or off since the last turn: the session's tools follow the registry."""
        named = tuple(self.tools.names())
        if named != self._tools_named and self.session is not None:
            self._tools_named = named
            with contextlib.suppress(Exception):
                await self.session.update_tools(self.tools.specs())

    # --- the shared conversation -------------------------------------------------------------------
    def on_event(self, event) -> None:
        """Each session event, as the GPT-Live path files it: the user's words and the voice's, one line each."""
        conversation = self.brain.conversation
        if event.kind == "input_transcript" and event.data.get("interim"):
            self.lines.user_line.add(event.data.get("delta") or "")
        elif event.kind == "input_transcript":
            text = event.text.strip()
            n = event.data.get("n") if isinstance(event.data.get("n"), int) else self.turn()
            self._heard[n] = (self._heard.get(n, "") + " " + text).strip()
            self._heard_at.setdefault(n, asyncio.Event()).set()
            with contextlib.suppress(Exception):
                if hasattr(conversation, "was_heard"):
                    conversation.was_heard(text)  # the user's own words, for the checks of what they explicitly asked
                if not self.lines.user_line.text:
                    self.lines.user_line.add(text)
                turn = conversation.add_once("user", "voice", text, asked=self.lines.user_line.asked)
                self.lines.asked = conversation.asked_of(turn)
                self.lines.user_line.close()
        elif event.kind == "output_transcript":
            with contextlib.suppress(Exception):
                self.lines.said(event.text)
        elif event.kind in ("generation_complete", "interrupted"):
            with contextlib.suppress(Exception):
                self.lines.reply_line.close()
        elif event.kind == "go_away" and self.reconnecting is None:
            reason = str((event.data or {}).get("reason") or "close")
            self.reconnecting = self._spawn(self.reconnect(self.fault or reason))

    # --- lines nobody asked for --------------------------------------------------------------------
    async def happened(self, event: str, text: str, *, ref: str | None = None, mark: str | None = None) -> None:
        if self.session is None or self.session.closed:
            return
        before = None
        if mark:
            async def before() -> None:
                await self.marks([{"target": mark, "style": "box"}], timeout=0.5)
        await self.session.proactive(text, event=event, ref=ref, before=before, mark=mark)

    def tag_notice(self) -> dict | None:
        """The narrator's line just offered is about the researcher that just finished: its event, card and mark."""
        seen, self._research_seen = self._research_seen, None
        if seen is None or self.clock() - seen[2] > NOTICE_TAG_S:
            return None
        rid, event = seen[0], seen[1]
        mark = f"researcher:{rid}"

        async def before() -> None:
            await self.marks([{"target": mark, "style": "box"}], timeout=0.5)
        return {"event": event, "ref": rid, "mark": mark, "before": before}

    def _research_done(self, session) -> None:
        """A researcher finished or failed. One asked for by voice is said by the app's own announcer, through the
        narrator (tagged here so it points at the card); any other is said here, once."""
        if getattr(session, "purpose", "research") != "research":
            return
        status = str(getattr(session, "status", "") or "")
        event = "researcher-done" if status in ("done", "partial") else "researcher-failed"
        rid = str(getattr(session, "rid", "") or "")
        self._research_seen = (rid, event, self.clock())
        if (getattr(session, "origin", None) or {}).get("channel") == "voice":
            return
        result = getattr(session, "result", None) or {}
        facts = {"researcher": getattr(session, "name", rid), "id": rid, "status": status,
                 **({"finding": result.get("one_liner")} if isinstance(result, dict) and result.get("one_liner") else {})}
        self._research_seen = None
        self._spawn(self.happened(event, json.dumps(facts, ensure_ascii=False), ref=rid, mark=f"researcher:{rid}"))

    # --- reconnecting ------------------------------------------------------------------------------
    async def reconnect(self, why: str) -> None:
        """The sideband reattached when the call is alive; otherwise a new call from the browser's new offer. Said
        to the user only if it happened while the voice was speaking (one quiet line)."""
        session = self.session
        try:
            if why == "close" and self.clock() - self._reattached_at > REATTACH_AGAIN_S:
                try:
                    await session.reconnect(None, why=why)
                    self._reattached_at = self.clock()
                    return
                except Exception:
                    pass  # the call itself ended: a new one
            offer = await self._ask({"type": "reoffer", "id": f"re-{next(self._ids)}", "why": why}, OFFER_WAIT_S)
            if not offer or not offer.get("sdp"):
                raise ConnectionError("the studio sent no new offer")
            await session.reconnect(str(offer["sdp"]), why=why)
            if self.send is not None:
                await self.send({"type": "voice", "kind": "answer", "data": {"sdp": session.answer_sdp}})
        except Exception as exc:
            self.log({"kind": "partner-reconnect-failed", "reason": why, "error": f"{type(exc).__name__}: {exc}"[:300]})
            with contextlib.suppress(Exception):
                await session.close()
        finally:
            self.reconnecting, self.fault = None, None

    async def bench_fault(self, fault: str) -> None:
        """The journey bench's faults (MICROMIND_BENCH_FAULTS=1 only, never in a packaged build)."""
        session = self.session
        if fault == "close-provider" and session is not None:
            self.fault = "bench"
            with contextlib.suppress(Exception):
                await session._hangup(session.call_id)
            with contextlib.suppress(Exception):
                await session._sb.close()
        elif fault == "fail-next-tool":
            self.tools.fail_next = "the bench made this tool fail (a stated test fault)"
