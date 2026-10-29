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

from . import knowing
from .openai_realtime import Perception, RealtimeSession, text_item
from .partner_tools import STARTED_TOOLS, PartnerTools

SNAPSHOT_WAIT_S = 1.5  # the studio answers a snapshot-request within this or the look goes on without it
OFFER_WAIT_S = 5.0  # a reconnect waits this long for the browser's new offer
SCREEN_CHARS = 1000  # the screen now as text, at most
NOTICE_TAG_S = 120.0  # a researcher's finish is matched to the narrator's lines naming it within this long
BRIEF_CHARS = 3000
REATTACH_AGAIN_S = 10.0
STORM_S, STORM_RECONNECTS = 60.0, 3  # more than this many reconnects in this long: Talk ends, and says so  # a reattached sideband dropping again within this: the call is gone, so a new one


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
        self._finished: dict[str, tuple] = {}  # researcher id -> (event, at): tags the narrator's lines about it
        self._tools_named: tuple = ()
        self.reconnecting: asyncio.Task | None = None
        self.fault: str | None = None
        self._talk_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()  # one new call at a time, whoever asks (this server, or the studio)
        self._latest_offer: str | None = None
        self._notes_task: asyncio.Task | None = None
        self._released: set[int] = set()  # turns whose key was released (or cancelled): their perception is fixed
        self._reattached_at = -1e9  # a sideband reattached that drops again this soon means its call has ended
        self._reconnects: list[float] = []  # when each recent reconnect began (a storm stops Talk)
        self._dropped_because = ""

    # --- binding -----------------------------------------------------------------------------------
    def bind(self, session: RealtimeSession) -> None:
        self.session = session
        session.turn_items = self.turn_items
        session.on_speech_started = self.capture
        session.history = self.history
        session.tag_notice = self.tag_notice
        session.happened = self.happened  # the app's own events (App.turned_on) reach it through brain.voice
        session.project_changed = self.project_changed  # another Project opened (App._session_event)
        session.running_facts = self.tools.running_facts
        session.heard = self.heard
        if knowing.UserActions.enabled():  # the user's own actions as silent context (knowing.py)
            from .. import activity

            found = activity.sink(self.hub)
            if found is not None:
                async def tell(text: str) -> None:
                    if self.session is not None and not self.session.closed:
                        await self.session.add(text_item("system", text))
                self._user_actions = knowing.UserActions(tell)
                found.listeners.append(self._user_actions.line)
        session.started_tools = STARTED_TOOLS  # think and the slow tools: an acknowledgement now, the result later
        session.on_point = lambda targets: self._spawn(self.marks([{"target": t, "style": "box"} for t in targets],
                                                                 timeout=1.0))
        session.on(self.on_event)
        self.lines.voice = session
        self._tools_named = tuple(self.tools.names())
        research = getattr(getattr(self.brain, "services", None), "research", None)
        if research is not None and isinstance(getattr(research, "on_done", None), list):
            research.on_done.append(self._research_done)
            self._research = research
        with contextlib.suppress(RuntimeError):  # no running loop (a direct construction in a test): nothing to watch
            self._notes_task = asyncio.get_running_loop().create_task(self._chat_notes())

    def close(self) -> None:
        if self._notes_task is not None:
            self._notes_task.cancel()
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

    def heard(self, n: int) -> None:
        """A sign it heard you (spec, section 1): the studio plays its soft cue on `heard` as a released turn begins,
        never a word; timed here as sent."""
        self.log({"kind": "partner-heard", "n": n, "cueT": time.time()})
        if self.send is not None:
            self._spawn(self.send({"type": "heard", "n": n}))

    def turn(self) -> int:
        return self.session.n if self.session is not None else 0

    def spawn(self, coro) -> asyncio.Task:
        return self._spawn(coro)

    def pictures(self, n: int) -> list:
        """Turn n's two pictures as taken at its key-down (camera, window), for think's backend."""
        seen = self._seen.get(n) or {}
        out = [self._camera_image(seen)] if seen else []
        if seen.get("window") is not None:
            out.append(self._window_image(seen))
        return [p for p in out if p is not None]

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
        elif kind == "drive-shown" and self.tools.hands is not None:  # Hands' cue arrived (hands.py)
            self.tools.hands.shown(message)
        elif kind == "takeover" and self.tools.hands is not None:  # the user's mouse, Escape or talk key
            await self.tools.hands.takeover(message)
        elif kind == "offer" and message.get("id"):
            if str(message["id"]) in self._waiting:
                self._answer(str(message["id"]), message)  # the new offer this server asked for (reoffer)
            else:
                # The studio's own new peer connection (its WebRTC failed or stayed down): a new call, answered with
                # that offer's id; it retries every 3.5 s, and only the newest offer is answered.
                self._latest_offer = str(message["id"])
                self._spawn(self._studio_offer(message))
        elif kind == "voice-phase" and self.session is not None:
            self.session.studio_phase(str(message.get("phase") or ""))
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
        if self.tools.hands is not None:
            self.tools.hands.fresh_look()  # control may come back to the partner: this turn looks afresh
        seen["screen"] = self.screen_now()
        board = getattr(self.brain, "board", None)
        with contextlib.suppress(Exception):
            seen["when_asked"] = board.when_asked() if board is not None else None
        seen["at"] = time.time()
        loop = asyncio.get_running_loop()
        seen["camera_task"] = loop.run_in_executor(None, view_frame, self.hub, 768)
        seen.setdefault("sent", set())
        for old in [k for k in self._seen if k < n - 2]:
            self._seen.pop(old, None)
        self._spawn(self.refresh_brief())
        if self.session is not None and self.session.talk == "hold":
            self._spawn(self._early(n))
        if self.session is not None and self.session.talk == "open":
            self._spawn(self._open_window(n))

    async def _early(self, n: int) -> None:
        """Turn n's perception into the conversation while its key is held, each part as it is ready: the note at once,
        the camera when its grab is done, the window when the studio's picture comes (got_snapshot). What is not in by
        the release goes then (turn_items). Each part is claimed before it is sent, so none goes twice."""
        session = self.session
        end = self.clock() + 0.5
        while session.n != n and self.clock() < end:  # the key-down's own task has not run yet
            await asyncio.sleep(0.01)
        seen = self._seen.get(n)
        if seen is None or session.n != n or n in self._released:
            return
        if "note" not in seen["sent"]:
            seen["sent"].add("note")
            if not await session.early(n, note=self._note(n, seen)):
                seen["sent"].discard("note")
        camera = seen.get("camera_task")
        if camera is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(camera), 1.0)
        image = self._camera_image(seen)
        if image is not None and "camera" not in seen["sent"] and n not in self._released:
            seen["sent"].add("camera")
            if not await session.early(n, image=image, detail="auto"):
                seen["sent"].discard("camera")
        window = seen.get("window")
        if window is not None:
            await self._early_window(n, seen)

    async def _early_window(self, n: int, seen: dict) -> None:
        if "window" in seen["sent"] or n in self._released or "note" not in seen["sent"]:
            return  # sent, too late, or the note's own early send has not run (it sends the window after it)
        seen["sent"].add("window")
        if not await self.session.early(n, image=self._window_image(seen), detail="auto"):
            seen["sent"].discard("window")

    @staticmethod
    def _note(n: int, seen: dict) -> str:
        when = time.strftime("%H:%M:%S", time.localtime(seen.get("at") or time.time()))
        return f"Turn {n} at {when}. Screen now: {seen.get('screen') or 'no report from the studio'}."

    def _camera_image(self, seen: dict):
        camera = seen.get("camera_task")
        if camera is None or not camera.done() or camera.cancelled() or camera.exception() is not None \
                or camera.result() is None:
            return None
        jpeg, frame_id, crop = camera.result()
        self.last_frame = {"id": frame_id, "crop": crop}
        return (jpeg, f"Camera, frame {frame_id}: what the microscope showed when the user pressed talk")

    @staticmethod
    def _window_image(seen: dict):
        window = seen["window"]
        return (window["jpeg"], f"Hooke's window when the user pressed talk ({window.get('how') or 'composed'})")

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
                    seen = self._seen.setdefault(n, {"window": None})
                    seen["window"] = shot
                    if shot is not None and "sent" in seen and self.session is not None and self.session.talk == "hold":
                        self._spawn(self._early_window(n, seen))
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
        if self.tools.hands is not None:  # the conversation's pointing history ("that one")
            self.tools.hands.point([str(m.get("target") or "") for m in marks])
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
        """What of turn n's key-down perception is not in the conversation yet, as it is now: never waited for. Each
        part is claimed here, synchronously, so an early send still running never sends it again."""
        seen = self._seen.get(n) or {}
        sent = seen.setdefault("sent", set())
        images, details = [], {}
        facts = {"camera": False, "window": None, "screenNowChars": len(seen.get("screen") or "")}
        camera = self._camera_image(seen) if seen else None
        facts["camera"] = camera is not None
        if camera is not None and "camera" not in sent:
            sent.add("camera")
            images.append(camera)
        window = seen.get("window")
        if window is not None:
            age = round(time.time() * 1000 - window["at"]) if isinstance(window.get("at"), (int, float)) else None
            facts["window"] = {"how": window.get("how"), "ageMs": age}
            if "window" not in sent:
                sent.add("window")
                image = self._window_image(seen)
                images.append(image)
                details[image[1]] = "auto"
        note = ""
        if "note" not in sent:
            sent.add("note")
            note = self._note(n, seen) if seen else f"Turn {n}. Screen now: no report from the studio."
        missing = ([] if camera is not None else ["No fresh camera frame this turn."]) + (
            [] if window is not None else ["No window picture this turn."])
        if missing:
            note = (note + " " if note else "") + " ".join(missing)
        return Perception(note, images, facts, details)

    def when_asked(self, n: int):
        """What was pointed at when turn n's key went down (board.WhenAsked), or None."""
        return (self._seen.get(n) or {}).get("when_asked")

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

    async def project_changed(self) -> None:
        """Another Project is open (a voice switch_session or new_project, or the user's own switch, which Talk no
        longer holds back): what it pointed at in the Project left is cleared, and the brief is the new Project's now,
        not at the next key press. The voice's binding is the studio connection, not a Project, so its tools already
        act in the Project open; the recap that says which one is App._session_event's."""
        await self.clear_marks()
        await self.refresh_brief()

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
            self._dropped_because = str((event.data or {}).get("message") or "")[:300]
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

    def tag_notice(self, text: str) -> dict | None:
        """A narrator line is about a researcher that finished lately only when its own facts name that researcher
        (the announcer's facts carry `"researchId": "<rid>"`); timing alone tags nothing. Every researcher that finished
        in the last NOTICE_TAG_S is looked for: the announcer's line can come many seconds after (it opens the work
        first), and two can finish close together (the part-2.5 bench: an untagged line said a researcher twice)."""
        now = self.clock()
        self._finished = {rid: seen for rid, seen in self._finished.items() if now - seen[1] <= NOTICE_TAG_S}
        found = next((rid for rid in self._finished if f'"researchId": {json.dumps(rid)}' in text), None)
        if found is None:
            return None
        rid, event = found, self._finished[found][0]
        mark = f"researcher:{rid}"

        async def before() -> None:
            await self.marks([{"target": mark, "style": "box"}], timeout=0.5)
        return {"event": event, "ref": rid, "mark": mark, "before": before}

    def _research_done(self, session) -> None:
        """A researcher finished or failed: said here at once, once, pointing at its card (the announcer's own line,
        which comes seconds later after it has opened the work beside the view, is the same event: told to the voice
        as context, never said again; the bench: 7.4 s from finished to spoken when the announcer's line was waited for)."""
        if getattr(session, "purpose", "research") != "research":
            return
        status = str(getattr(session, "status", "") or "")
        event = "researcher-done" if status in ("done", "partial") else "researcher-failed"
        rid = str(getattr(session, "rid", "") or "")
        self._finished[rid] = (event, self.clock())
        from ..untrusted import block

        result = getattr(session, "result", None) or {}
        facts = {"researcher": getattr(session, "name", rid), "id": rid, "status": status}
        text = json.dumps(facts, ensure_ascii=False)
        if isinstance(result, dict) and result.get("one_liner"):
            # The researcher's own words come from the web: information, inside the untrusted wrapper (ADR 0002).
            text += "\nIts finding, in its own words:\n" + block(result["one_liner"], tag="researchers", source="web",
                                                                limit=600)
        self._spawn(self.happened(event, text, ref=rid, mark=f"researcher:{rid}"))

    # --- reconnecting ------------------------------------------------------------------------------
    async def reconnect(self, why: str) -> None:
        """The sideband reattached when the call is alive; otherwise a new call from the browser's new offer. Said
        to the user only if it happened while the voice was speaking (one quiet line)."""
        async with self._reconnect_lock:
            await self._reconnect(why)

    async def _studio_offer(self, message: dict) -> None:
        """The studio made a new peer connection of its own: a new call for it, answered with its offer's id (a late
        answer to an older offer is never applied to the newer peer)."""
        async with self._reconnect_lock:
            if message["id"] != self._latest_offer or self.session is None or self.session.closed:
                return  # a newer offer came while waiting: that one is answered
            try:
                await self.session.reconnect(str(message.get("sdp") or ""), why="studio")
                if self.send is not None:
                    await self.send({"type": "voice", "kind": "answer",
                                     "data": {"sdp": self.session.answer_sdp, "id": message["id"]}})
            except Exception as exc:  # the studio retries, and says so if it cannot
                self.log({"kind": "partner-reconnect-failed", "reason": "studio",
                          "error": f"{type(exc).__name__}: {exc}"[:300]})

    async def _reconnect(self, why: str) -> None:
        session = self.session
        now = self.clock()
        self._reconnects = [t for t in self._reconnects if now - t < STORM_S] + [now]
        if len(self._reconnects) > STORM_RECONNECTS:
            # A connection that keeps dropping is not reconnected for ever (a model that refuses the session drops it
            # every few seconds: the bench of gpt-realtime-2.1-mini made 14 calls in a minute): ended, and said.
            self.log({"kind": "partner-reconnect-failed", "reason": "storm", "count": len(self._reconnects),
                      "error": self._dropped_because})
            if self.send is not None:
                with contextlib.suppress(Exception):
                    await self.send({"type": "voice", "kind": "error", "data": {"message": (
                        "The voice connection kept dropping, so Talk stopped. Press Talk to start again.")}})
            with contextlib.suppress(Exception):
                await session.close()
            self.reconnecting, self.fault = None, None
            return
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
                await self.send({"type": "voice", "kind": "answer",
                                 "data": {"sdp": session.answer_sdp, "id": offer.get("id")}})
        except Exception as exc:
            self.log({"kind": "partner-reconnect-failed", "reason": why, "error": f"{type(exc).__name__}: {exc}"[:300]})
            with contextlib.suppress(Exception):
                await session.close()
        finally:
            self.reconnecting, self.fault = None, None

    async def _chat_notes(self) -> None:
        """What happens in typed chat while talking (the brain's voice notes: "the user typed ... and the app answered
        ..."), told to the voice as it happens, so you can type, talk, then type. GPT-Live reads them with its view
        notes; this voice has no view notes."""
        while True:
            await asyncio.sleep(0.3)
            session = self.session
            notes = getattr(self.brain, "voice_notes", None)
            if session is None or session.closed or not isinstance(notes, list) or not notes or session._sb is None:
                continue
            taken = list(notes)
            del notes[:len(taken)]
            for text in taken:
                with contextlib.suppress(Exception):
                    await session.inject(text, respond=False, source="chat")

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
