"""The conductor: Hooke starting, steering and stopping outside agents on this Mac (ADR 0029).

A launch is one outside agent (Codex, Claude Code, an ACP agent such as Grok Build) that the user asked for in Hooke. It
gets what any connected agent gets, made for it alone: its own door connection (door/connections.py), contribute
access to one Project, and one Study assignment. It works through the door like any connected agent; its report lands
in the Study as the agent's own, awaiting the researcher's review.

The conductor holds each launch's status, transcript and pending asks, and the rules:
- Hooke's messages to an agent are marked as Hooke's (adapter.marked), never the user's consent.
- Every permission an agent asks for goes to the user; nothing here answers one on its own.
- A launch ends with its connection revoked and its Project access withdrawn, whether it finished, failed or was
  stopped, and when Hooke quits. Nothing reconnects it.
- Spend an agent reports counts against the day's research allowance; Claude Code is held to its launch's budget.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import programs as installed
from .adapter import Adapter, Spec, marked, short
from .brief import first_message, instructions
from ..paths import REPO
from .transport import Gone, ProcessTransport, child_env

MOST_LIVE = 4  # agents working at once
MOST_ALIVE = 6  # programs kept running, finished ones included (they answer replies)
KEPT = 40  # launches kept in the list
TRANSCRIPT = 300  # entries kept per launch
PEEK = 6  # entries each list message carries; the rest is read on demand
CLAUDE_BUDGET_USD = 3.0
LIVE_STATES = ("starting", "working", "idle", "done")  # its program is running (done: finished, still answering)


class Refused(Exception):
    """A launch or a verb Hooke won't do now: `says` what happened, `next` what the user can do."""

    def __init__(self, says: str, *, next: str | None, status: int = 409):
        super().__init__(says)
        self.says, self.next, self.status = says, next or "", status


@dataclass
class Launch:
    id: str
    program: str
    label: str
    task: str
    project: str
    project_name: str
    by: str  # studio | voice: who asked, as Hooke knows it
    at: float
    state: str = "starting"  # starting | working | idle | done | failed | stopped
    study: str = ""
    assignment: str = ""
    connection: str = ""
    folder: str = ""
    session: str | None = None  # the program's own conversation id
    model: str | None = None
    billing: str | None = None
    budget_usd: float | None = None
    cost_usd: float | None = None
    tokens: int | None = None
    day: str = ""
    ended_at: float | None = None
    why: str = ""  # how it ended, or why it failed
    report: dict = field(default_factory=dict)  # {returned, claims} as the Study says
    asks: list = field(default_factory=list)
    transcript: list = field(default_factory=list)
    live: str = ""  # what it is saying right now, while it streams
    n: int = 0
    cli: str = ""  # Hooke's own `hooke` command as this launch was given it: said as "hooke"

    @property
    def status(self) -> str:
        """The words the user sees: working, needs you, done, failed (and stopped)."""
        if self.asks and self.state in LIVE_STATES:
            return "needs-you"
        return {"starting": "working", "working": "working", "idle": "needs-you"}.get(self.state, self.state)

    def line(self) -> str:
        if self.asks:
            return f"Asks to {self.asks[0]['title'].lower()}"
        if self.state == "idle":
            return "Interrupted: reply, or stop it"
        if self.state in ("starting", "working"):
            last = next((e for e in reversed(self.transcript) if e["role"] in ("agent", "tool")), None)
            return short(last["text"], 120) if last else "Starting"
        if self.state == "done":
            return "Report returned for review" if self.report.get("returned") else "Finished: no report returned yet"
        return short(self.why or self.state, 120)

    def wire(self, *, full: bool = False) -> dict:
        return {"id": self.id, "program": self.program, "label": self.label, "task": self.task,
                "status": self.status, "state": self.state, "line": self.line(), "project": self.project,
                "projectName": self.project_name, "study": self.study, "assignment": self.assignment,
                "connection": self.connection, "by": self.by, "at": self.at, "endedAt": self.ended_at,
                "model": self.model, "billing": self.billing, "budgetUsd": self.budget_usd, "costUsd": self.cost_usd,
                "tokens": self.tokens, "why": self.why, "report": self.report, "asks": self.asks,
                "transcript": self.transcript if full else self.transcript[-PEEK:], "live": self.live[-400:],
                "folder": self.folder if full else None}


_SHELL = re.compile(r"""^/bin/(?:ba|z)?sh -l?c (['"])(.*?)(?:\1)?(…?)$""", re.S)  # a cut-short line keeps its …


def tidy(text: str, launch: "Launch") -> str:
    """An agent's line as a person reads it: Hooke's own command said as `hooke`, its folder as "its folder", and a
    shell's wrapper left out. Nothing else is changed."""
    text = text or ""
    if ": " in text and text.split(": ", 1)[1].startswith("/bin/"):
        head, body = text.split(": ", 1)
        inner = _SHELL.match(body)
        text = f"{head}: {inner.group(2)}{inner.group(3)}" if inner else text
    else:
        inner = _SHELL.match(text)
        text = inner.group(2) + inner.group(3) if inner else text
    if launch.cli:
        text = text.replace(f"'{launch.cli}'", "hooke").replace(launch.cli, "hooke")
    text = re.sub(r"(hooke) --agent [a-z-]+", r"\1", text)
    if launch.folder:
        text = text.replace(launch.folder, "its folder")
    text = text.replace(str(REPO / ".agents/skills/hooke/SKILL.md"), "Hooke's guide")
    home = str(Path.home())
    return text.replace(home + "/", "~/")


def adapter_for(program: str, path: str) -> Adapter:
    from .acp import gemini, grok
    from .claude import Claude
    from .codex import Codex

    async def factory(argv, cwd, env):
        transport = ProcessTransport(argv, cwd=cwd, env=env)
        await transport.open()
        return transport

    made = {"codex": lambda: Codex(factory, path), "claude-code": lambda: Claude(factory, path),
            "grok": lambda: grok(factory, path), "gemini": lambda: gemini(factory, path)}.get(program)
    if made is None:
        raise Refused(f"Hooke doesn't know how to start {program}.", next="choose Codex, Claude Code or Grok Build", status=400)
    return made()


class Conductor:
    def __init__(self, home, side, *, make_adapter: Callable[[str, str], Adapter] = adapter_for,
                 find: Callable[[str], str | None] | None = None, clock=time.time):
        self.root = Path(home) / "agents"
        self.path = self.root / "launches.json"
        self.side = side  # the rest of Hooke a launch needs (hooke_side.py)
        self.make_adapter = make_adapter
        self.find = find or (lambda program: installed.find(installed.BY_ID[program]))
        self.clock = clock
        self.launches: dict[str, Launch] = {}
        self.adapters: dict[str, Adapter] = {}
        self._tasks: set[asyncio.Task] = set()
        self._admission = asyncio.Lock()
        self._dirty = False
        self._flush: asyncio.Task | None = None
        self._programs: list[dict] | None = None
        self._read()

    # --- the kept list --------------------------------------------------------------------------------------------
    def _read(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        for row in raw.get("launches", []) if isinstance(raw, dict) else []:
            try:
                launch = Launch(**row)
            except TypeError:
                continue
            if launch.state in LIVE_STATES:
                # Its program ended with Hooke's last run: said as that, never as still working.
                launch.state = "done" if launch.state == "done" else "stopped"
                launch.why = launch.why or "Hooke closed while it was working"
                launch.asks = []
                launch.ended_at = launch.ended_at or self.clock()
            self.launches[launch.id] = launch

    def warm(self) -> None:
        """Make Hooke's own `hooke` command ready before the first launch needs it (a source checkout builds it once):
        only on a Mac where an agent's program is installed. Blocking: called off the event loop."""
        if any(self.find(p.id) for p in installed.PROGRAMS):
            self.side.cli()

    def recover(self) -> None:
        """A launch's connection never outlives its program (ADR 0029): at start, once the door is there, withdraw any
        a crash left, with its credential file. Done again harmlessly."""
        for launch in self.launches.values():
            if launch.connection and launch.id not in self.adapters:
                self.side.end(launch.project, launch.connection)
                try:
                    (Path(launch.folder).parent / "credential").unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass

    def _write(self) -> None:
        rows = sorted(self.launches.values(), key=lambda x: x.at)[-KEPT:]
        self.launches = {r.id: r for r in rows}
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"format": 1, "launches": [asdict(r) for r in rows]}, stream)
        temporary.replace(self.path)

    def changed(self) -> None:
        """Say it to the studios and keep it, at most five times a second."""
        self._dirty = True
        if self._flush is None or self._flush.done():
            try:
                self._flush = asyncio.get_running_loop().create_task(self._send())
            except RuntimeError:
                self._dirty = False
                self._save()

    async def _send(self) -> None:
        await asyncio.sleep(0.2)
        self._dirty = False
        await asyncio.to_thread(self._save)
        self.side.broadcast(self.message())

    def _save(self) -> None:
        try:
            self._write()
        except OSError:
            pass  # the list on screen is still right; it is kept again with the next change

    def message(self) -> dict:
        return {"type": "agents", "agents": self.wire(), "programs": self._programs or []}

    def wire(self) -> list[dict]:
        return [launch.wire() for launch in sorted(self.launches.values(), key=lambda x: -x.at)]

    def get(self, lid: str) -> Launch:
        launch = self.launches.get(lid)
        if launch is None:
            raise Refused("There is no such agent.", next="reload the Agents list", status=404)
        return launch

    def find_one(self, words: str | None) -> Launch:
        """The launch the user means, by id, program or label; the newest running one when they name none."""
        rows = sorted(self.launches.values(), key=lambda x: -x.at)
        if not words:
            live = [r for r in rows if r.state in LIVE_STATES]
            if len(live) == 1:
                return live[0]
            if not live:
                raise Refused("No outside agent is running.", next="start one first")
            raise Refused("Several agents are running.", next="say which: " + ", ".join(r.label for r in live))
        text = words.strip().lower()
        for r in rows:
            if text in (r.id.lower(), r.program, r.label.lower()) or text in r.label.lower():
                return r
        raise Refused(f"No agent called {words}.", next="ask what's running")

    def summary(self) -> list[str]:
        """What every outside agent is doing, a few words each, as the user would say it (the newest first; stopped ones
        are left out)."""
        out = []
        for r in sorted(self.launches.values(), key=lambda x: -x.at):
            if r.status == "needs-you":
                out.append(f"{r.label} is waiting for you to allow it to {r.asks[0]['title'].lower()}" if r.asks else
                           f"{r.label} stopped its step and waits for your reply")
            elif r.status == "working":
                out.append(f"{r.label} is working on: {short(r.task, 80)}")
            elif r.status == "done":
                out.append(f"{r.label} finished" + (", and its report is back in its Study for your review, not yet an "
                                                    "accepted finding" if r.report.get("returned")
                                                    else ", with no report back yet"))
            elif r.status == "failed":
                out.append(f"{r.label} failed: {short(r.why, 100)}")
        return out

    def waiting(self, words: str | None) -> tuple[Launch, dict]:
        """The ask the user means: the named agent's oldest, else the only agent asking."""
        asking = [r for r in sorted(self.launches.values(), key=lambda x: x.at) if r.asks and r.id in self.adapters]
        if words:
            asking = [r for r in asking if words.strip().lower() in (r.program, r.label.lower(), r.id)
                      or words.strip().lower() in r.label.lower()]
        if not asking:
            raise Refused("No outside agent is asking for anything now.", next=None)  # no next: nothing waits
        if len(asking) > 1:
            raise Refused("More than one agent is asking.", next="say which: " + ", ".join(r.label for r in asking))
        return asking[0], asking[0].asks[0]

    async def programs(self, *, fresh: bool = False) -> list[dict]:
        if self._programs is None or fresh:
            self._programs = await asyncio.to_thread(installed.installed)
        return self._programs

    # --- starting -------------------------------------------------------------------------------------------------
    async def start(self, program: str, task: str, *, project: str | None = None, by: str = "studio",
                    budget_usd: float | None = None) -> Launch:
        # Capture the requested Project before discovery or waiting for another admission.
        # Capture only the identity here; catalogue validation stays off the live event loop.
        project = project or self.side.current_project()
        if not project:
            raise Refused("Open a Project first: the agent works in one Project.", next="open or make a Project", status=409)
        async with self._admission:
            return await self._start(program, task, project=project, by=by, budget_usd=budget_usd)

    async def _start(self, program: str, task: str, *, project: str, by: str,
                     budget_usd: float | None) -> Launch:
        known = installed.BY_ID.get(program)
        if known is None:
            raise Refused(f"Hooke can't start {program}.", next="choose Codex, Claude Code, Grok Build or Gemini CLI", status=400)
        task = (task or "").strip()[:4000]
        if not task:
            raise Refused("Say what the agent should do.", next="give it a task", status=400)
        busy = [r for r in self.launches.values() if r.state in ("starting", "working", "idle")]
        if len(busy) >= MOST_LIVE:
            raise Refused(f"{len(busy)} agents are already working.", next="stop one first, then start this one")
        finished = sorted((r for r in self.launches.values() if r.state == "done" and r.id in self.adapters),
                          key=lambda r: r.at)
        while len(busy) + len(finished) >= MOST_ALIVE:  # a finished agent still running its program makes room
            await self.stop(finished.pop(0).id, "Hooke ended it after it finished, to make room for another")
        path = await asyncio.to_thread(self.find, program)
        if not path:
            raise Refused(f"{known.label} isn't installed on this Mac.", next=known.how, status=409)
        cli = await asyncio.to_thread(self.side.cli)
        if cli is None:
            raise Refused("Hooke's own `hooke` command isn't available in this copy of Hooke.",
                          next="build it (docs/testing/agent-connection.md) or set HOOKE_CLI", status=409)
        sid, name = await asyncio.to_thread(self.side.project, project)
        budget = self._budget(program, budget_usd)
        lid = "ag_" + secrets.token_hex(8)
        launch = Launch(id=lid, program=program, label=known.label, task=task, project=sid, project_name=name,
                        by=by, at=self.clock(), budget_usd=budget, day=self.side.today(), cli=cli)
        folder = self.root / "launches" / lid
        try:
            work = folder / "work"
            await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
            os.chmod(folder, 0o700)
            launch.folder = str(work)
            launch.connection, token = await asyncio.to_thread(self.side.connect, known.label, known.kind, lid, sid)
            credential = folder / "credential"
            descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(token)
            launch.study, launch.assignment = await asyncio.to_thread(
                self.side.assign, sid, lid, f"{known.label}: {short(task, 80)}", task, budget)
        except Refused:
            self._abandon(launch, folder)
            raise
        except Exception as error:  # a Hooke-side refusal (Study, access) said as itself
            self._abandon(launch, folder)
            raise Refused(f"Hooke couldn't set up {known.label}'s assignment: {short(str(error), 200)}",
                          next="try again, or start it in another Project") from error
        self.launches[lid] = launch
        self.side.hold(lid, budget or 0.0)
        self._note(launch, "hooke", marked(task))
        self.side.activity(f"Starting {known.label}: {short(task, 80)}", state="running", id=f"agent:{lid}",
                           ref={"agent": known.label})
        spec = Spec(cwd=str(work), brief=first_message(
            task=task, cli=cli, kind=known.kind, project=sid, project_name=name, study=launch.study,
            assignment=launch.assignment, folder=str(work), skill=self.side.skill(), harness=known.label,
            launch=lid),
            instructions=instructions(label=known.label), budget_usd=budget, hooke=cli, skill=self.side.skill(),
            env=child_env({"HOOKE_AGENT": known.kind, "HOOKE_CONNECTION_FILE": str(credential),
                           "HOOKE_DOOR_URL": self.side.door_url(), "PATH": installed.search_path()}))
        self._spawn(self._run(launch, path, spec))
        self.changed()
        return launch

    def _budget(self, program: str, asked: float | None) -> float | None:
        if program != "claude-code":
            return None  # Codex and ACP agents report tokens, not dollars: they hold no dollar budget here
        budget = float(asked) if asked else CLAUDE_BUDGET_USD
        left = self.side.allowance_left()
        if left is not None:
            if left < 0.25:
                raise Refused(f"Today's research allowance has ${max(0.0, left):.2f} left.",
                              next="raise it in Settings, or start the agent tomorrow")
            budget = min(budget, left)
        return round(budget, 2)

    def _abandon(self, launch: Launch, folder: Path) -> None:
        if launch.connection:
            self.side.end(launch.project, launch.connection)
        try:
            (folder / "credential").unlink(missing_ok=True)
        except OSError:
            pass

    def _spawn(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, launch: Launch, path: str, spec: Spec) -> None:
        adapter = self.make_adapter(launch.program, path)
        self.adapters[launch.id] = adapter
        pump = asyncio.get_running_loop().create_task(self._pump(launch, adapter))
        try:
            await adapter.start(spec)
        except Exception as error:  # the program refused to start: said as itself, and cleaned up
            why = short(str(error) or type(error).__name__, 300)
            launch.state = "failed"  # first: its program's exit is then never said instead of this
            await adapter.stop()
            self._end(launch, "failed", f"{launch.label} didn't start: {why}")
        await pump

    async def _pump(self, launch: Launch, adapter: Adapter) -> None:
        async for event in adapter.events():
            try:
                await self._on(launch, event)
            except Exception as error:  # one unreadable event never stops the transcript
                self._note(launch, "note", f"Hooke couldn't read one of its messages ({short(str(error), 120)})")

    # --- what an agent says ---------------------------------------------------------------------------------------
    def _note(self, launch: Launch, role: str, text: str, **extra) -> dict:
        launch.n += 1
        entry = {"n": launch.n, "role": role, "text": short(tidy(text, launch), 4000), "at": self.clock(), **extra}
        launch.transcript.append(entry)
        del launch.transcript[:-TRANSCRIPT]
        return entry

    async def _on(self, launch: Launch, event: dict) -> None:
        kind = event["type"]
        if kind == "session":
            launch.session = event.get("session") or launch.session
            launch.model = event.get("model") or launch.model
            launch.billing = event.get("billing") or launch.billing
        elif kind == "turn":
            state = event.get("state")
            if state == "working":
                if launch.state not in ("failed", "stopped"):
                    launch.state = "working"
            else:
                launch.live = ""
                if state == "completed":
                    launch.state = "done"
                    await self._observe(launch)
                    self.side.activity(f"{launch.label} finished: {launch.line()}", id=f"agent:{launch.id}",
                                       ref={"agent": launch.label})
                    answer = next((e["text"] for e in reversed(launch.transcript) if e["role"] == "agent"), "")
                    turns = launch.n
                    self.side.say({"agent": launch.label, "event": "finished its turn", "task": short(launch.task, 160),
                                   "report": "back in its Study for the user's review" if launch.report.get("returned")
                                   else "not returned yet", "its own words": short(answer, 500),
                                   "say it": "once, in one or two sentences: what it found, and that its report waits "
                                             "for the user's review"},
                                  still=lambda: launch.state == "done" and launch.n == turns)
                elif state == "interrupted":
                    launch.state = "idle"
                    self._note(launch, "note", "Interrupted")
                else:
                    why = event.get("error") or "Its turn failed"
                    launch.state = "failed"
                    adapter = self.adapters.get(launch.id)
                    try:
                        if adapter is not None:
                            await adapter.stop()
                    finally:
                        self._end(launch, "failed", why)
                    self.side.say({"agent": launch.label, "event": "failed", "why": short(why, 200),
                                   "what Hooke did": "ended it and revoked its connection; what it kept stays"},
                                  urgent=True)
        elif kind == "delta":
            launch.live = (launch.live + (event.get("text") or ""))[-2000:]
        elif kind == "text":
            launch.live = ""
            item = event.get("item")
            old = next((e for e in reversed(launch.transcript) if item and e.get("item") == item), None)
            if old is not None:
                old["text"], old["final"] = short(tidy(event.get("text") or "", launch), 4000), bool(event.get("final"))
            else:
                self._note(launch, "agent", event.get("text") or "", item=item, final=bool(event.get("final")))
        elif kind == "tool":
            item = event.get("item")
            old = next((e for e in reversed(launch.transcript) if item and e.get("item") == item), None)
            if old is not None:
                old["state"] = event.get("state")
                if event.get("title"):
                    old["text"] = tidy(event["title"] + (f": {event['detail']}" if event.get("detail") else ""), launch)
            else:
                title = event.get("title") or "Used a tool"
                self._note(launch, "tool", title + (f": {event['detail']}" if event.get("detail") else ""),
                           item=item, state=event.get("state"))
        elif kind == "permission":
            ask = {k: event.get(k) for k in ("id", "title", "detail", "reason", "always")}
            ask["detail"], ask["reason"] = tidy(ask["detail"] or "", launch), tidy(ask["reason"] or "", launch)
            ask["at"] = self.clock()
            launch.asks.append(ask)
            pid = ask["id"]
            # Its reason is in its own words ("May I run…"): said as the voice's own, it sounds like Hooke asking
            # (will-agents run 3), so the voice is told whose ask it is.
            self.side.say({"agent": launch.label, "event": "asks your permission", "to": ask["title"].lower(),
                           "what": short(ask["detail"], 200), f"{launch.label}'s reason, in its words": short(ask["reason"], 200),
                           "how the user answers": "yes or no by voice (answer_agent with their own words), or on "
                                                   "its approval card; Hooke never answers for them",
                           "say it": f"once, in one short sentence that names {launch.label} as the one asking and asks "
                                     f"the user whether to allow it (\"{launch.label} wants to …; allow it?\"); never "
                                     "as your own request, never \"May I\""},
                          still=lambda: any(a["id"] == pid for a in launch.asks))
            self._note(launch, "ask", f"to {ask['title'].lower()}: {ask['detail']}", item=ask["id"])
            self.side.activity(f"{launch.label} asks to {ask['title'].lower()}", state="running",
                               id=f"agent:{launch.id}:{ask['id']}", ref={"agent": launch.label},
                               details={"lines": [short(ask["detail"], 300)]})
        elif kind == "resolved":
            pid = event.get("id")
            if any(a["id"] == pid for a in launch.asks):
                launch.asks = [a for a in launch.asks if a["id"] != pid]
                outcome = event.get("outcome")
                words = {"allowed": "You allowed it", "declined": "You declined it",
                         "withdrawn": "It no longer asks", "cancelled": "No longer asked"}.get(outcome, outcome)
                self._note(launch, "note", words, item=pid)
                self.side.activity(f"{launch.label}: {words.lower()}", state="done" if outcome != "declined"
                                   else "failed", id=f"agent:{launch.id}:{pid}", ref={"agent": launch.label})
        elif kind == "usage":
            if event.get("tokens") is not None:
                launch.tokens = event["tokens"]
            if event.get("costUsd") is not None:
                launch.cost_usd = round(float(event["costUsd"]), 4)
                self.side.spent(launch.id, launch.cost_usd, launch.billing, launch.model, launch.budget_usd)
        elif kind == "warning":
            self._note(launch, "note", event.get("text") or "A warning")
        elif kind == "exit":
            if launch.ended_at is None:
                why = launch.why or event.get("why") or "it ended"
                tail = "; ".join(event.get("stderr") or [])[-300:]
                self._end(launch, "failed" if launch.state != "done" else "done",
                          f"Its program ended ({why})" + (f": {tail}" if tail and launch.state != "done" else ""))
            self.adapters.pop(launch.id, None)
        self.changed()

    async def _observe(self, launch: Launch) -> None:
        """What the Study says the agent returned: read from the Study, never from what the agent said."""
        try:
            launch.report = await asyncio.to_thread(self.side.observe, launch.project, launch.study,
                                                    launch.assignment, launch.connection)
        except Exception:
            launch.report = {"returned": False, "claims": 0, "unread": True}

    def _end(self, launch: Launch, state: str, why: str) -> None:
        if launch.ended_at is not None:
            return  # ended once, said once
        launch.state, launch.why, launch.asks = state, why, []
        launch.ended_at = self.clock()
        launch.live = ""
        if launch.connection:
            self.side.end(launch.project, launch.connection)
        self.side.release(launch.id)
        try:
            (Path(launch.folder).parent / "credential").unlink(missing_ok=True)
        except OSError:
            pass
        if state != "done":
            self._note(launch, "note", why)
            self.side.activity(f"{launch.label} {'stopped' if state == 'stopped' else 'failed'}: {short(why, 160)}",
                               state="done" if state == "stopped" else "failed", id=f"agent:{launch.id}",
                               ref={"agent": launch.label})
        self.changed()

    # --- the user's verbs -----------------------------------------------------------------------------------------
    def _adapter(self, launch: Launch) -> Adapter:
        adapter = self.adapters.get(launch.id)
        if adapter is None or adapter.ended or launch.state in ("failed", "stopped"):
            raise Refused(f"{launch.label} has ended.", next="start a new agent to continue")
        return adapter

    async def note(self, lid: str, text: str, *, by: str = "studio") -> str:
        launch = self.get(lid)
        text = (text or "").strip()
        if not text:
            raise Refused("Say what to tell it.", next="type a message", status=400)
        adapter = self._adapter(launch)
        relayed = marked(text)
        try:
            how = await adapter.send(relayed)
        except Gone as error:
            raise Refused(f"{launch.label} has ended.", next="start a new agent to continue") from error
        self._note(launch, "hooke", relayed, how=how, by=by)
        if launch.state == "done" or launch.state == "idle":
            launch.state = "working"
        self.changed()
        return how

    async def interrupt(self, lid: str) -> bool:
        launch = self.get(lid)
        stopped = await self._adapter(launch).interrupt()
        self.changed()
        return stopped

    async def answer(self, lid: str, permission: str, allow: bool, *, always: bool = False) -> None:
        """The user's own answer, from the Agents panel: the only way an ask is ever answered."""
        launch = self.get(lid)
        if not any(a["id"] == permission for a in launch.asks):
            raise Refused("That ask is no longer waiting.", next="reload the Agents list")
        try:
            await self._adapter(launch).answer(permission, allow, always=always)
        except KeyError as error:
            raise Refused("That ask is no longer waiting.", next="reload the Agents list") from error

    async def stop(self, lid: str, why: str = "You stopped it") -> Launch:
        launch = self.get(lid)
        adapter = self.adapters.get(lid)
        if launch.state in ("failed", "stopped") and adapter is None:
            return launch
        launch.state = "stopped"  # first: its program's exit is then never read as a failure
        if adapter is not None:
            await adapter.stop()
        self._end(launch, "stopped", why)
        return launch

    async def close(self) -> None:
        """Hooke is quitting: every agent's program is stopped and its connection revoked."""
        for lid in [lid for lid, r in self.launches.items() if r.state in LIVE_STATES or lid in self.adapters]:
            try:
                await asyncio.wait_for(self.stop(lid, "Hooke closed"), 10)
            except Exception:
                pass
        for task in list(self._tasks):
            task.cancel()
        self._save()

    def spent_on(self, day: str) -> float:
        """What agents reported spending on `day`, for the day's research allowance (research/ledger.py)."""
        return sum(r.cost_usd or 0.0 for r in self.launches.values() if r.day == day)
