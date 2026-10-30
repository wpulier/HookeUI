"""The App: everything the orchestrator is made of, built and wired once.

`App.build` holds the wiring that used to sit in server.create_app: the hub, the day and its
conversations (day/), what the chat showed (dialogue.py), the subject in view
(day/subject.py), the brain and its tools, the researchers, the narrator and the announcer
(announcer.py), the result viewer, the magnifier, the descriptions and the arena. The
server (server.py) adds the HTTP and WebSocket routes on top; the studio's commands are in
commands.py.

A session is the project, not the day (ADR 0017). There is one way another session opens, a new one or
one switched to (`new_session`, `switch_session`; the studio's header and sessions rail, voice or chat): the
store says "session-opened" and `_session_event` clears what belonged to the one left (outlines, the magnifier,
the viewer, descriptions, the subject). Midnight does nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from functools import partial
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent import Agent
from .announcer import Announcer
from .arena import Arena
from .masks import Masker
from .molmo import MolmoPointer
from .board import Board
from .capture import RUN, captures
from .brain.loop import Brain
from .brain.narrator import Line, Narrator
from .brain.state import Conversation
from .brain import toolbox
from .brain.actions import outline_seen
from .brain.toolbox import Services
from .datasets import DatasetCollector
from .datasets.rig import Rig, at_start
from .day import DayStore, SessionStore
from .day.memory import Memory
from .day.subject import SubjectTracker
from .dialogue import Dialogue
from .dissect import Dissector
from .enrich import Enricher
from .hub import Hub
from . import learner, looplag
from .keys import load_keys
from .objtrack import ObjectFollower
from .magnifier import Magnifier
from .paths import Paths
from .presenter import Presenter
from .presentations import PresentationContext, PresentationError, Presentations, SourceRef
from .quiz import Judgment, Quiz
from .research import ResearchManager
from .research.desk import Desk
from .resources import Index, Results
from .screen import Screen
from .workspace_observation import ClientRef, ObservationError
from .ticket import current
from .sight import Sight
from .scale import Scale
from .library import Library, is_asset_id
from .coach import Coach
from .slidemap import SlideMap
from .dust import DustMap
from .sweep import SweepBuffer
from .seen import SeenToday
from .taught import TaughtLabels, gpu_embed
from .zeroshot import ZeroShotHint, gpu_rank
from .clip import ClipBuffer
from .watcher import Watcher
from .outline_check import OutlineCheck
from . import activity, tools, execution
from .day.catalogue import days_before, opened_sources, product_of

LAND_S = 20.0  # at most this long, a session opened is not looked at while the studio puts its source on stage
SESSIONS_EVERY_S = 2.0  # what the open session holds is sent again at most this often (the chip, its menu, the rail)
# While a download only moves on (no row changed its state), the capabilities are built again at most this often; between,
# each progress tick sends the last ones with their download rows renewed (App.catalog_progress). L7b, 2026-10-20: a
# 1.2 GB download built the 0.5 MB wire twice per 0.5 s tick and stalled the loop 3.2 s, then 11.8 s, under load.
PROGRESS_FULL_S = 5.0
READINESS_REFRESH_S = 20.0  # the kept readiness facts are read again off the loop this often (keep_readiness)


def _change_settings(app, changes: dict, by: str) -> list[str]:
    """Settings changed from now on by the brain (`set_default`), as the studio's menus change them."""
    from .commands import change_settings

    return change_settings(app, changes, by)


def session_clock() -> Callable[[], float]:
    """The session store's clock: the time. In a test's or the sim's home a file (paths.clock_offset) may move it by
    that many seconds, read again at most twice a second, so the sim can cross midnight without waiting for it
    (midnight does nothing: ADR 0017). The live app's is the time."""
    from .paths import clock_offset

    path = clock_offset()
    if path is None:
        return time.time
    state = {"checked": -1e9, "offset": 0.0}

    def clock() -> float:
        now = time.monotonic()
        if now - state["checked"] >= 0.5:
            state["checked"] = now
            try:
                state["offset"] = float(path.read_text().strip() or 0.0)
            except (OSError, ValueError):
                state["offset"] = 0.0
        return time.time() + state["offset"]

    return clock


def last_used_words(at: float, now: float) -> str:
    """When a session was last used, as the studio says it: "2:10 pm" today, "yesterday 8:12 pm", "Oct 9"."""
    when, today = time.localtime(at), time.localtime(now)
    clock = time.strftime("%-I:%M %p", when).lower()
    if time.strftime("%Y-%m-%d", when) == time.strftime("%Y-%m-%d", today):
        return clock
    if time.strftime("%Y-%m-%d", when) == days_before(now, 1):
        return f"yesterday {clock}"
    return time.strftime("%b %-d", when)


def thread_title(day: DayStore, tid: str) -> str:
    """A conversation's title as the studio shows it ("Conversation 2" until named); "" if gone."""
    thread = day.threads.get(tid)
    return (thread.title or f"Conversation {thread.n}") if thread is not None else ""


def _held_clips(hub) -> list:
    """The footage held for researchers now (held.py), any session's."""
    from .held import held_of

    return list(held_of(hub).clips.values())


def restore_prefs(settings, day: DayStore) -> list[dict]:
    """What a restart keeps of the user's preferences (day.prefs, kept until changed: day/prefs.py): whether the user
    leads, the look of outlines, how to explain (learner.py: level, hints first), the research cap, and the models
    chosen from now on (defaults.py, the user's Q15), each checked as runnable; returns those, said at start. The app
    and the acting bench's restarts both restore them here."""
    from . import defaults as kept_defaults

    settings.user_leads = bool(day.prefs.get("userLeads", settings.user_leads))
    settings.update({k: day.prefs[k] for k in ("outlineColor", "style", *learner.PREFS) if k in day.prefs})
    if "researchCap" in day.prefs:  # set by the user (studio, voice or chat): kept until changed, None too
        settings.research_cap = day.prefs["researchCap"]
    return kept_defaults.restore(settings, day)


class _LazyDownloads:
    """The app's download (or install) queue, made on first use (App.downloads, App.installs), reached through a getter:
    Services is built before the App object exists (2026-10-12: holding `app` itself held None, and get_model failed on
    the user's yes)."""

    def __init__(self, app, queue: str = "downloads") -> None:
        self.app = app  # () -> the App
        self.queue = queue

    def __getattr__(self, name: str):
        return getattr(getattr(self.app(), self.queue), name)


@dataclass
class App:
    paths: Paths
    hub: Hub
    day: DayStore
    conversation: Conversation
    dialogue: Dialogue
    subjects: SubjectTracker
    agent: Agent
    research: ResearchManager
    services: Services
    brain: Brain
    narrator: Narrator
    sight: Sight
    presenter: Presenter
    magnifier: Magnifier
    memory: Memory
    announcer: Announcer
    enrich: Enricher
    arena: Arena
    board: Board
    slidemap: SlideMap | None = None
    taught: TaughtLabels | None = None
    quiz: Quiz | None = None
    seen: SeenToday | None = None
    masker: Masker | None = None
    verdicts: object = None  # capabilities.Verdicts: the user's verdicts (ADR 0007)
    molmo: MolmoPointer | None = None
    datasets: DatasetCollector | None = None  # Record's dataset (datasets/): collected while the user records
    rig: Rig | None = None  # the microscope as the user describes it (datasets/rig.py)
    _tasks: list = field(default_factory=list)
    _stopping: bool = field(default=False, repr=False)
    _catalog_cleanup: set = field(default_factory=set, repr=False)
    _environment_observer: object = field(default=None, repr=False)
    _workspace_capture_authority: Callable | None = field(default=None, repr=False)
    # The sessions list's sending (sessions_changed): its timer, when it was last sent (the loop's clock), the sends
    # in flight, and which researchers were working when last seen (a start or a finish sends it at once).
    _sessions_timer: object = field(default=None, repr=False)
    _landing: object = field(default=None, repr=False)  # lets go of a hold no studio lifted (hold_until_landed)
    # The population a reopened session's layer showed when it was left, shown again by its first landing (reopen_layer),
    # and that showing while its files are checked.
    _reopen_layer: str | None = field(default=None, repr=False)
    _reopening: object = field(default=None, repr=False)
    _sessions_sent: float = field(default=-1e9, repr=False)
    _sessions_sending: set = field(default_factory=set, repr=False)
    _research_working: dict = field(default_factory=dict, repr=False)
    _key_admin: object = field(default=None, repr=False)
    _loop: object = field(default=None, repr=False)  # the app's event loop, once started
    _pages: dict = field(default_factory=dict, repr=False)  # open_page's waits: id -> the studio's reply (page-opened)
    # Catalog changes asked for from other threads (request_catalog_changed): at most one waits on the loop at a time.
    _catalog_asked: dict = field(default_factory=lambda: {"pending": False, "full": False, "progress": False,
                                                          "built": -1e9, "states": None}, repr=False)
    _catalog_lock: object = field(default_factory=threading.Lock, repr=False)
    _capabilities_sent: dict | None = field(default=None, repr=False)  # the last full capabilities built for studios

    @classmethod
    def build(cls, bridge_url: str, paths: Paths | None = None, use_gpu: bool = True,
              *, project_locations=None) -> "App":
        """Build one host; an explicit location resolver enables isolated portability qualification.

        This internal dependency is not accepted from HTTP, saved files or an
        environment toggle. Ordinary startup retains its existing storage policy.
        The built host owns the resolver until its producers have stopped.
        """
        paths = paths or Paths.from_env()
        if project_locations is not None:
            from .project_roots import ProjectLocationError
            if project_locations.legacy.resolve() != paths.sessions.resolve():
                raise ProjectLocationError('Project locations must belong to this host’s session store')
        try:
            return cls._build(bridge_url, paths, use_gpu, project_locations=project_locations)
        except BaseException:
            if project_locations is not None:
                project_locations.close()
            raise

    @classmethod
    def _build(cls, bridge_url, paths, use_gpu, *, project_locations):
        from .scientific_import_transaction import recover
        from .scientific_projects import ProjectStore
        recover(ProjectStore(paths.results / "projects"), paths.sessions)
        hub = Hub(bridge_url, use_gpu=use_gpu)
        hub.execution = execution.ExecutionLog.from_env(publish=hub.broadcast_execution)
        # The session and its conversations (day/store.py, ADR 0017): the one the app was last in, rebuilt from its
        # journal, so a restart comes back to the same session and conversation.
        day = SessionStore.resume(root=paths.sessions, clock=session_clock(), locations=project_locations)
        # Every session's journal read once now, before the event loop runs: after this the list only reads where a
        # journal grew (day/catalogue.py), so a reading on the loop (a new id, the session wire) stays small.
        day.catalogue.list()
        hub.execution_day = day
        hub.local_only = lambda: day.local_only
        conversation = Conversation(store=day)
        dialogue = Dialogue(day, hub.broadcast, paths.conversation_log)
        # Activity (activity.py): what happened, newest first, filed as lines of the conversation it happened in.
        hub.activities = activity.Activity(hub, dialogue)
        records = dialogue.today_in_log()
        conversation.restore(records)  # the first start with a journal: today's chat log is imported once
        dialogue.import_log(records)  # ...and so are the lines it showed
        # Who leads, the look of outlines, how to explain, the research cap, and the models chosen from now on,
        # each checked as runnable (said below).
        # The keys first: a saved choice of a cloud model is judged runnable by its key, and was said "can't run now
        # (needs GEMINI_API_KEY)" on every restart when the keys loaded only at start (live 2026-10-11).
        load_keys(paths.keys_file)
        try:  # entries that bring their own adapter are choices before the kept ones are restored (env_runner.py)
            from . import env_runner

            env_runner.register()
        except Exception:  # noqa: BLE001 - registered again on the first catalog change
            pass
        restored_defaults = restore_prefs(hub.settings, day)
        # A second stack started for a side-by-side bench may start on other settings (MICROMIND_SETTINGS, JSON:
        # group -> option, bench/voice_journeys/stack.py); refused ones are logged like any settings change.
        started_with = os.environ.get("MICROMIND_SETTINGS", "").strip()
        if started_with:
            try:
                problems = hub.settings.update(json.loads(started_with))
            except ValueError as exc:
                problems = [f"MICROMIND_SETTINGS is not JSON: {exc}"]
            started_with = {"kind": "settings-at-start", "settings": started_with, "problems": problems}
        # How big things are (scale.py): the objective the user named, kept until changed.
        scale = Scale(day)
        hub.scale = scale
        hub.scene.sizer = hub.size_of

        capabilities_objective = {"value": scale.objective}  # the objective the studios' capabilities were last built with

        def scale_changed() -> None:
            hub.scene.version += 1  # every outline's size changed: the scene is sent again
            info = hub.latest_info
            hub.broadcast(scale.wire(info.fov.radius if info is not None and info.fov is not None else None))
            # The menus and the Capabilities page read the objective from the capabilities (`preferences`), and only
            # that: they are built again (170-260 ms on the loop, L1b's measure) when it changed, not when an asset was
            # put on stage or a calibration kept (sim 2026-10-20: 313-759 ms stalls from `use_asset` and `_save`).
            if looplag.off_loop() and scale.objective == capabilities_objective["value"]:
                return
            capabilities_objective["value"] = scale.objective
            hub.broadcast(app.capabilities_wire())

        scale.listeners.append(scale_changed)
        agent = Agent(hub, conversation, interaction_log=paths.interaction_log,
                      tags=lambda: {"day": day.sid, "thread": day.active_id})
        if started_with:
            agent.log(started_with)
        # Classifying's vision models (classify.py) are the agent's own clients: one key change drops them all, and a
        # stand-in agent stands in for them too. Read at each call, so a replaced `agent.vision` is the one used.
        hub.vision_for = lambda option: agent.vision(option)
        # Labels the user teaches (taught.py, option TAUGHT): the user's, from every session (ADR 0017), written per
        # date with the session they were taught in; every new outline cropped as it appears and, while TAUGHT is
        # on, matched on the heavy GPU worker.
        taught = TaughtLabels(paths.taught, today=lambda: day.date, embed=partial(gpu_embed, hub) if use_gpu else None,
                              log=agent.log, hub=hub, session=lambda: day.sid)
        taught.watch(hub.scene)
        agent.taught = taught
        # Seen one like this before? (seen.py, option SEEN): every outline named, in any session (ADR 0017), with the
        # same embedding, its name, time, session and conversation; naming a click is told the most alike one.
        seen = SeenToday(paths.seen, paths.interaction_log.parent, today=lambda: day.date, thread=lambda: day.active_id,
                         title=lambda tid: thread_title(day, tid), taught=taught, hub=hub, log=agent.log,
                         session=lambda: day.sid, catalogue=day.catalogue)
        seen.watch(hub.scene)
        agent.seen = seen
        # MedSigLIP's zero-shot ranking of the subject's candidate names when naming a click (zeroshot.py,
        # option ZEROSHOT), on the heavy GPU worker: one line of data, the model decides the name.
        agent.zeroshot = ZeroShotHint(partial(gpu_rank, hub) if use_gpu else None, settings=lambda: hub.settings,
                                      subject=lambda: hub.scene.context.get("specimen"), log=agent.log)
        hub.follower = ObjectFollower(hub, log=agent.log)  # option TRACK "objects" (orchestrator/objtrack.py)
        # How late the event loop runs, with its stack when it stalls (looplag.py), and the computer's readings
        # each time one crosses the contract's lines (machine.py): both logged, permanently.
        from .looplag import LoopLag

        hub.loop_lag = LoopLag(log=agent.log, readings=hub.machine.snapshot)
        hub.machine.log = agent.log
        from .identity import Identity

        # Things followed over time (session knowledge K2): individuals, links and observations of the session, from
        # every date it touched, read again when another is opened (ADR 0017). Following ends with the run it was
        # asked in (outline ids start again at o1), so nothing is followed at start.
        hub.identity = Identity(paths.identity, today=lambda: day.date, hub=hub, log=agent.log, store=day)
        # Evidence (contract 8): what operations the user's requests made found, kept with their views,
        # saved per date and read back from the session's journal at start and when another session is opened
        # (ADR 0017: history, never shown live); and the layer that shows a population over the live view, each
        # member once its own pixels are checked (contract 3).
        from .evidence import Evidence
        from .population import Layer

        hub.evidence = Evidence(hub, paths.evidence, day=day, log=agent.log)
        hub.layer = Layer(hub, hub.broadcast)

        def layer_journal(eid: str | None, session: str) -> None:
            # Journaled in the population's own session while it is the one open: hidden because another opened, the
            # session left still holds what its layer showed (App.reopen_layer shows it again there).
            if not session or session == day.sid:
                day.record_layer(eid)

        hub.layer.journal = layer_journal

        def evidence_changed(population) -> None:
            if hub.layer.shown == population.id:  # its save status or a revision, as the studio shows it
                hub.broadcast(population.wire(hub.layer.states()))

        hub.evidence.listeners.append(evidence_changed)
        # Motion tracks (M3.2, motiontracks.py): what track_motion measured and the follower's trails kept, saved
        # beside the populations and read back at start; their layer draws one live only where it is verified.
        from .motiontracks import TrackLayer, TracksStore

        hub.tracks_store = TracksStore(hub, paths.evidence, day=day, log=agent.log)
        hub.track_layer = TrackLayer(hub, hub.broadcast)
        magnifier = Magnifier(hub)
        # A researcher belongs to the session it was started in (ADR 0017); the manager reads a session's researchers
        # when it is opened, before the app's own listener (start) shows the list.
        research = ResearchManager(hub, conversation, root=paths.research, today=lambda: day.date,
                                   session=lambda: day.sid, store=day)
        day.listeners.append(research.session_event)
        research.log = agent.log  # lesson checks, stalls and claims checks (W6-3, W6-4)
        # Research traces explain retained work and survive performance-log pruning (ADR 0027).
        # Existing logs/transcripts remain readable in place until explicit collection.
        research.transcripts = paths.research / "transcripts"
        presenter = Presenter(hub, research, outcomes=day.outcomes_of, prefs=(day.prefs.get, day.set_pref))  # how the user did on lesson steps (quiz.py)
        subjects = SubjectTracker(day, conversation, hub.scene, hub.broadcast)
        subjects.held = lambda: hub.view_held  # the studio covers the live view: a look in flight is not the subject
        # Footage held for the open session's researchers counts among what it made while held (Made in this session
        # lists it; the session list does not, it is memory only): the same rule as the index's (resources._clip_session).
        subjects.held_now = lambda: sum(1 for clip in list(_held_clips(hub)) if (getattr(clip, "session", "") or day.sid) == day.sid)
        brain: Brain | None = None  # the brain is made from the tools; these wait for it (and for the app)
        app: App | None = None
        # Not while the studio covers the live view to look back at the session (hub.view_held: its returning card).
        sight = Sight(hub, agent, wanted=lambda: not day.local_only and not hub.view_held and (bool(hub.clients) or (brain is not None and brain.voice is not None)),
                      compare_to=lambda: subjects.subject, user_named=subjects.user_named)
        hub.on_still_change.append(sight.still_changed)  # something new in a still view: look again
        # Pointing "masks" (masks.py): the settled view masked on the heavy GPU worker, ready for a
        # look to number; also while a blind comparison has a "masks" arm.
        arena: Arena | None = None
        masker = Masker(hub, log=agent.log,
                        wanted=lambda: bool(hub.clients) or (brain is not None and brain.voice is not None),
                        also=lambda: arena is not None and arena.enabled and any(a.endswith("/masks") or "/masks/" in a
                                                                                 for a in arena.arms.get("ask", [])))
        hub.on_still_change.append(masker.still_changed)
        agent.masks = masker
        # Pointing "molmo" (molmo.py): Molmo 2 on this Mac points at what was asked, in its own GPU
        # worker; the settled view is read ahead. Also while a blind comparison has a "molmo" arm.
        molmo = MolmoPointer(hub, log=agent.log,
                             wanted=lambda: bool(hub.clients) or (brain is not None and brain.voice is not None),
                             also=lambda: arena is not None and arena.enabled and any(a.endswith("/molmo") or "/molmo/" in a
                                                                                      for a in arena.arms.get("ask", [])))
        hub.on_still_change.append(molmo.still_changed)
        agent.molmo = molmo
        # Helping the user get a good view (coach.py): a focus meter (a number), and, only with the
        # QUALITY gate (code judging the view), a written word on a bad view, spoken only while the
        # voice is on. By default ("tell") the models judge the view from the measured numbers. A sweep
        # that changed the eyepiece disc's size is the phone's own lens, not the knob (COACH "lens").
        coach = Coach(hub.broadcast,
                      speak=lambda text: narrator.offer(Line(text, "suggestion")) if brain is not None and brain.voice else None,
                      quiet=lambda: hub.settings.quieter or hub.settings.user_leads,
                      advise=lambda: hub.settings.quality == "gate", lens=lambda: hub.settings.coach == "lens",
                      log=agent.log)
        hub.on_frame.append(coach.frame)
        hub.coach = coach
        # The watcher (view_watch.py): what happens in the view (the slide settling on a new place, the focus drifting,
        # counted cells no longer where they were), noticed unasked from what the hub already measures, each kind told to
        # the one-brain voice by its rule (option WATCH); GPT-Live has no lines of this kind. Every event is logged.
        from .view_watch import ViewWatch

        def watch_told(event, rule: str) -> str:
            happened = getattr(getattr(brain, "voice", None), "happened", None) if brain is not None else None
            if not callable(happened):
                return "no voice"
            task = asyncio.get_running_loop().create_task(happened(
                event.kind, event.text(), ref=event.id, delivery=rule, marks=event.marks(), frame=event.frame_ref()))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # a closed voice says nothing; never raised
            return "voice"

        def um_per_px():
            reading = hub.scale_reading()
            return reading.um_per_px if reading is not None else None

        view_watch = ViewWatch(hub, deliver=watch_told, quiet=lambda: hub.settings.quieter or hub.settings.user_leads,
                               sweep=lambda: None if coach.sweeping_since is None else ("phone" if coach.phone else "knob"),
                               um_per_px=um_per_px, log=agent.log,
                               schedule=lambda read: asyncio.get_running_loop().call_soon(read))
        hub.on_frame.append(view_watch.frame)  # after the coach's: its sweep for this frame is known
        hub.view_watch = view_watch
        # The frames of the last turn of the focus knob, for a focus stack (sweep.py, enhance.py).
        sweep = SweepBuffer(hub.source.get)
        hub.on_frame.append(sweep.frame)
        research.hands.sweep = sweep  # a researcher's focus stack is of the user's own last turn of the knob
        narrator = Narrator(lambda: brain.voice if brain is not None else None, dialogue.show,
                            quieter=lambda: hub.settings.quieter or hub.settings.user_leads)
        # The last minute of the view (what_happened), and watching for something to happen (watch).
        clip = ClipBuffer(hub.source.get)
        hub.on_frame.append(clip.frame)
        # Footage a researcher asked for, held on the user's yes (held.py): a frame every half second, in
        # memory; the researcher is told when it is held, the user when it stopped early (wired below, with
        # the announcer), and its frames go when its researcher's run ends.
        from .held import held_of

        held = held_of(hub)
        held.log = agent.log
        held.store = day  # its clips' ids, and each journaled as a product of the session it was held in
        hub.on_frame.append(held.frame)
        # The slide map (slidemap.py): steady views pasted where they are on the slide, off the frame path.
        slidemap = SlideMap(hub.source.get, hub.broadcast)
        hub.on_frame.append(slidemap.frame)
        # Dust on the optics (dust.py, option DUST): views kept during pans, the flat field off the frame path.
        dust = DustMap(hub.source.get)
        hub.on_frame.append(dust.frame)
        agent.dust = dust
        from .brain.toolbox.time import look_question, motion_share

        def watched(w, text: str, fired: bool) -> None:
            import json

            facts = w.facts()
            origin = w.origin
            # The model's seen sentence remains an observation, separate from
            # the application's terminal status; both remain in the saved facts.
            summary = text
            if w.kind == "look" and w.event != "failed":
                summary = f"Watch for {w.what}: {w.event}, after {w.checks} checks over {w.seconds} s."
            row = dialogue.notice(producer={"kind": "watch", "id": w.id}, event=w.event,
                                  summary=summary, facts=facts, asked=origin.get("asked"),
                                  origin={"day": origin.get("day"), "thread": origin.get("thread"),
                                          "delegationId": origin.get("delegation_id"), "turnId": origin.get("turn_id"),
                                          "requestId": origin.get("request_id"), "actionId": origin.get("action_id")},
                                  tone="error" if w.event == "failed" else "info")
            index = getattr(hub, "resources", None)
            if fired and index is not None:  # what it saw is a result in the session's index (produces.py: watch)
                asked = w.origin.get("asked")
                index.results.add("watch", {"what": w.what, "kind": w.kind}, text, info=hub.latest_info,
                                  values={"checks": w.checks, "last": w.last, "seconds": w.seconds},
                                  thread=index.results._thread_of(asked),  # never another day's same-id one
                                  session=getattr(asked, "session", None))  # filed in the session it was asked in
            voice = origin.get("voice_owner")
            if voice is not None and not voice.closed and brain is not None and brain.voice is voice:
                narrator.offer(Line(json.dumps(row, ensure_ascii=False), "requested",
                                    delegation_id=origin.get("delegation_id"), asked=origin.get("asked"),
                                    delivery="interrupt" if fired else "when_idle", provenance="data",
                                    turn_id=origin.get("turn_id"), request_id=origin.get("request_id"),
                                    action_id=origin.get("action_id"), voice_owner=voice))

        watcher = Watcher(motion_share(hub), look_question(hub, agent), watched, log=agent.log)
        hub.on_still_change.append(watcher.appeared)

        async def ask_quietly(prompt: str) -> dict:
            """The day's memory asks the fast model (summaries, settled facts); never on a reply's path."""
            option = hub.settings.fast if hub.settings.fast != "off" else hub.settings.vision
            result = await agent.vision(option).ask([], prompt, max_output_tokens=800)
            return result.data or {}

        memory = Memory(day, conversation, ask=ask_quietly, research=research)
        from .bookmarks import Bookmarks

        bookmarks = Bookmarks(paths.bookmarks, today=lambda: day.date, store=day)  # places on the slide the user kept (M3)
        from .registrations import Registrations

        # Volumes the user adds and brainreg's registrations of them (phase 3's E5): each job's change said by the app.
        registrations = Registrations(paths.jobs, store=day, today=lambda: day.date, said=lambda record: app.job_said(record))
        from . import kinds as new_kinds

        services = Services(hub=hub, agent=agent, magnifier=magnifier, dissector=Dissector(hub, agent), research=research, scale=scale,
                            desk=Desk(research, shown=lambda: presenter.rid if presenter.open else None),
                            presenter=presenter, day=day, memory=memory, subjects=subjects, sight=sight, narrator=narrator,
                            outline_seen=partial(outline_seen, hub, lambda: sight.view),
                            publish_settings=lambda: app.publish_settings(), sweep=sweep,
                            clip=clip, watcher=watcher, slidemap=slidemap, history_log=paths.interaction_log, snapshots=paths.snapshots, bookmarks=bookmarks, registrations=registrations, log=agent.log,
                            capabilities=lambda: app.capabilities_wire(counts=False),  # the agent reads what the Capabilities page shows
                            capability_status=lambda: app.capability_status(),  # its awareness: the rows alone, fresh
                            # The Models page's download queue and its card, for the agent (brain/toolbox/catalog.py)
                            downloads=_LazyDownloads(lambda: app), installs=_LazyDownloads(lambda: app, "installs"), open_page=lambda client, page: app.open_page(client, page),
                            put_on_stage=lambda aid: app.put_on_stage(aid),
                            workspace_tabs=lambda ctx, args: app.hub.workspace_tabs.run(ctx, args),
                            observe=lambda model: app.observe(model, timeout=5.0),  # asked again before the agent answers
                            turn_on=lambda job, asked=None, expected=None: app.turn_on.start(job, asked=asked, expected=expected),  # Turn on, on the user's yes (turn_on tool)
                            kinds=new_kinds.available,  # new kinds of job the catalog added: run_capability (C6)
                            taught=taught, seen=seen,
                            # set_default changes settings as the Models menu does (one helper: commands.change_settings)
                            change_settings=lambda changes, by: _change_settings(app, changes, by))
        # The session's one index (resources.py; Amendments 2.7 and 3): every store read in, never copied, and
        # what tools measure or make kept as result entries, built from their runs.
        results = Results(paths.results, day=day, pictures=lambda key: magnifier.pictures.get(key))
        from .scientific_projects import ProjectStore
        from .paths import envs as environment_root
        projects = ProjectStore(paths.results / 'projects', runtime_root=environment_root())
        if project_locations is not None:
            from .project_stores import ProjectStores
            projects = ProjectStores(projects, project_locations)
        hub.screen = Screen(hub.broadcast)  # what the studio reports it shows; the verbs' changes (screen.py)
        hub.resources = Index(hub, results=results, projects=projects, research=research, snapshots=paths.snapshots, bookmarks=bookmarks,
                              registrations=registrations, identity=hub.identity, memory=memory, seen=seen, taught=taught,
                              scale=scale, datasets=paths.datasets, recordings=paths.recordings, day=day)
        # The library (library.py; ADR 0022): every recording and picture of the user's own, each an asset with its
        # metadata; a session's sources are those opened or made in it (sources.py). Test footage is never one.
        from .assets import store as assets_store
        from .paths import CORPUS_MANIFEST, assets_manifest

        def origin_of(kind: str, ident: str, at: float | None = None) -> str | None:
            """The session a dataset was made in: the one whose journal says so, else its date's (the index's rule)."""
            try:
                for info in day.catalogue.list():
                    if (kind, ident) in info.claims:  # its journal's "made" (a dataset is a source, not a product)
                        return info.id
                date = time.strftime("%Y-%m-%d", time.localtime(at)) if at else None
                return date if date and day.catalogue.get(date) is not None else None
            except Exception:
                return None

        # Its real recordings from the repository's manifest and the store's private one (the owner's, untracked).
        library = Library(paths.library, footage=assets_store().footage, manifest=assets_manifest(), corpus=CORPUS_MANIFEST,
                          datasets=paths.datasets,
                          snapshots=paths.snapshots, origin_of=origin_of)
        hub.library = library
        # Yours: the user's own and the examples a session has used (any session's sources, the catalogue's).
        library.used = lambda: {aid for info in day.catalogue.list() for aid in opened_sources(info)
                                if aid not in getattr(info, 'source_references', {})}
        # The library's looks (library_looks.py): one look at each new asset files it by what is in it, with the fast
        # vision model when it can run here (its key); what the running look saw while it was on stage is told as data.
        from .library_looks import LibraryLooks
        from .thumbnails import LIBRARY, thumbnails_of

        def look_vision():
            option = hub.settings.fast if hub.settings.fast != "off" else hub.settings.vision
            group = "fast" if hub.settings.fast != "off" else "vision"
            from . import options as option_groups

            try:
                return None if option_groups.unavailable(group, option) else agent.vision(option)
            except Exception:  # noqa: BLE001 - no vision here: the library waits, its assets "Not sure yet"
                return None

        hub.library_looks = LibraryLooks(
            library, lambda aid: thumbnails_of(hub).get(aid, LIBRARY, ""), look_vision,
            seen_on=lambda aid: [name for info in day.catalogue.list() for name, source in info.seen
                                 if source == aid and aid not in getattr(info, 'source_references', {})],
            log=agent.log, chosen=lambda: not day.local_only and hub.settings.library_looks == "on")
        hub.stage_meta = None  # the asset on stage's metadata, for the Board's line (brain/context.py); None on live
        hub.stage_image_json = None
        # The user's specimen for the asset on stage wins over the looks; naming the specimen while an asset is on stage
        # keeps it on the asset; setting the objective or calibrating while one is on stage describes it (scale.py).
        # What is on stage is what was put there (hub.stage_meta, set by the `source` command), not only the journal's
        # last source: after a session is opened, nothing is until the studio lands it on one.

        def staged() -> str | None:
            meta = hub.stage_meta
            return meta.get("id") if isinstance(meta, dict) and not meta.get('retained') and is_asset_id(meta.get("id")) else None

        subjects.pinned = lambda: (str((hub.stage_meta or {}).get('specimen') or '')
            if (hub.stage_meta or {}).get('retained') and (hub.stage_meta or {}).get('specimenBy') == 'user'
            else str(library.edits(staged()).get("specimen") or "") if staged() else "")
        subjects.on_named.append(lambda name: library.set_meta(staged(), {"specimen": name}, by="user") if staged() else None)
        scale.asset_set = lambda aid, fields: library.set_meta(aid, fields, by="user")

        def clearer_saved(entry: dict) -> None:
            """A clearer picture (enhance) is an asset of the session it was made in: real frames made sharper, at the
            camera's pixel scale, so with the objective then in use. So is a picture a capability made from the view
            (run_capability), with what made it (madeWith): the library offers it, and Download with capability."""
            made_picture = entry.get("tool") == "run_capability" and entry.get("madeWith")
            if (entry.get("tool") != "enhance" and not made_picture) or not entry.get("pictures") or results.root is None:
                return
            name, date = str(entry["pictures"][0]), str(entry.get("day") or "")
            optics = {"objective": float(scale.objective)} if scale.asset is None and scale.objective else (
                {k: v for k, v in (scale.asset or {}).items() if k != "id"})
            library.register(f"result:{date}/{entry['id']}/0", results.folder_for(date, entry.get("session")) / name, title=str(entry.get("title") or "Clearer picture"),
                             at=float(entry.get("at") or time.time()), origin=entry.get("session"), optics=optics)

        results.on_saved.append(clearer_saved)
        # The source owner's separately reviewed module must be integrated before
        # App construction: no placeholder or conversation-memory substitute.
        from .presentation_delivery import PreparedSources
        from .motiontracks import track_layer_of
        from .live_sources import LiveSources

        services.prepared_sources = PreparedSources(hub.resources, captures(hub).memory, RUN,
                                                   live=LiveSources(hub, track_layer_of(hub)))
        services.presentations = Presentations(
            resolve_source=services.prepared_sources.resolve, app_run=RUN,
            resolve_source_for=lambda ref, ctx: services.prepared_sources.resolve(ref, context=ctx),
            check_request=lambda ctx: app._workspace_request_guard(ctx),
            check_compensation=lambda ctx: app._workspace_request_guard(ctx, allow_cancelled=True))
        hub.presentations = services.presentations
        # Install real display owners before building their one tool declaration.
        # Like check_request above, this forwards to the App constructed below;
        # no request or source authority is acquired during construction.
        services.workspace_context = lambda socket, *, request_id, owner: app.workspace_context(
            socket, request_id=request_id, owner=owner)
        services.table_services = lambda: (hub.datatables, hub.table_derivatives)
        brain = Brain(hub, agent, toolbox.build(services), conversation)
        from .operation_retry import OperationRetries
        brain.registry.operation_retries = OperationRetries(hub, brain.registry)
        brain.services, brain.narrator, brain.sight, brain.presenter = services, narrator, sight, presenter
        hub.resources.registry = brain.registry
        async def keep_result(run, ctx):
            entry = await results.finish_run(run, ctx, hub)
            if entry is not None and run.resource:
                magnifier.identify_sources(run.picture_sources, run.resource)
        brain.registry.retaining.append(keep_result)
        # What is on screen, one state: every tool resolves "that" against it (board.py).
        board = Board(hub, magnifier, presenter, sight, log=agent.log)
        brain.board = services.board = board
        announcer = Announcer(dialogue, conversation, narrator, presenter, research, hub.scene,
                              voice=lambda: brain.voice, note_to_voice=brain.note_to_voice, log=agent.log, taught=taught)
        brain.report_late = announcer.late_tool
        brain.registry.progressed.append(announcer.progressed)
        from .held import wire as wire_held

        wire_held(held, research, announcer, agent.log)

        async def outline_again(capture, box, label: str, made_by: dict | None = None):
            """The outline check's repair (contract 7): an outline made by an explicit choice is outlined
            again with the implementation that made it, raw; one made by the default with option OUTLINE_REPAIR's
            model when it names one ("try one, then another"), raw, else by the default's route."""
            explicit = made_by is not None and made_by.get("policy") == "raw" and made_by.get("implementation")
            other = getattr(hub.settings, "outline_repair", "same")
            implementation = made_by["implementation"] if explicit else (other if other != "same" else None)
            r = await tools.segment(hub, implementation, capture.id, capture.jpeg,
                                    (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box=box, label=label, capture=capture,
                                    policy="raw" if implementation else "route", origin="background")
            # Its own made-by goes with it: what drew the outline again is what made it (known.chain, the check's log).
            return {"polygons": r.get("polygons"), "madeBy": r.get("madeBy")}

        def said_late(request, text: str, *, producer: dict | None = None, event: str = "repaired",
                      facts: dict | None = None) -> None:
            """File late application status and memory; never manufacture assistant prose.

            The outline check supplies its original ticket, without a voice owner.
            Such a notice persists with no speech; a current session is not proof
            that it owns that earlier request. An explicitly retained owner, when
            available, receives only factual context through the existing queue.
            """
            import json

            asked = getattr(request, "asked", None)
            origin = {}
            if request is not None:
                if getattr(request, "id", None) is not None:
                    origin["ticketId"] = request.id
                admission = getattr(request, "admission", None)
                request_id = getattr(admission, "request_id", None) or getattr(request, "request_id", None)
                if request_id is not None:
                    origin["requestId"] = request_id
                for attribute, key in (("turn_id", "turnId"), ("delegation_id", "delegationId")):
                    if getattr(request, attribute, None) is not None:
                        origin[key] = getattr(request, attribute)
            conversation.add("note", "system", text, asked=asked)
            row = dialogue.notice(producer=producer if producer is not None else {"kind": "outline-check"},
                                  event=event, summary=text, facts=facts if facts is not None else {},
                                  asked=asked, origin=origin)
            owner = getattr(request, "voice_owner", None)
            if getattr(request, "channel", "") == "voice" and owner is not None and not owner.closed:
                narrator.offer(Line(json.dumps(row, ensure_ascii=False), "requested", asked=asked,
                                    delivery="when_idle", provenance="data", voice_owner=owner,
                                    turn_id=origin.get("turnId"), request_id=origin.get("requestId"),
                                    action_id=origin.get("actionId"), delegation_id=origin.get("delegationId")))

        # The app's outlines checked after they are drawn, and outlined again when wrong (C2); a repair
        # that moved one is said (G4).
        OutlineCheck(hub, agent, outline_again, log=agent.log, tell=said_late)
        # The one runner of operations (contracts 4-6): what ran is recorded in the interaction log.
        from .operations import runner

        runner(hub).log = agent.log

        def choice_unused(request, unused: list) -> None:
            """A model chosen for a request (`use`) that nothing in it used is said, not dropped (contract 7)."""
            from .brain.toolbox.choosing import OPERATION_WORDS

            for c in unused:
                agent.log({"kind": "choice-unused", "ticket": request.id, **{k: v for k, v in c.items() if k != "used"}})
                said_late(request, f"{c.get('label') or c.get('implementation')} was chosen for that request, but nothing "
                                   f"in it was {OPERATION_WORDS.get(c['operation'], c['operation'])}, so it was not used",
                          producer={"kind": "model-choice", **({"id": c["implementation"]} if c.get("implementation") else {})},
                          event="unused-choice", facts={k: v for k, v in c.items()})

        from . import ticket as tickets

        tickets.UNUSED_CHOICE[:] = [choice_unused]
        def worker_problem(client, text: str) -> None:
            """A hung worker (contract 5) is said once, as a note in the conversation, and logged; its
            recovery ("") is logged too. Either way the capabilities are published again, so what
            needs it is offered or not as it is now."""
            if text:
                conversation.add("note", "system", text + "; work that needs it fails until it recovers")
                agent.log({"kind": "worker-hung", "problem": text})
            else:
                agent.log({"kind": "worker-recovered", "worker": client.name})
            hub.broadcast(app.capabilities_wire())

        # Models loading on the workers (loads.py): said in Activity (activity.model_loading: running, then done) and
        # on the request's step, measured (models/loads.json), kept within memory, and the defaults loaded once frames flow.
        from .loads import Loads

        hub.loads = Loads(paths.models / "loads.json", log=agent.log, hub=hub) if use_gpu else None
        def told(client) -> None:
            """A worker's problems said and its loads told (a big model's own host too, as it is made: hub.host_for)."""
            client.on_problem.append(lambda text, c=client: worker_problem(c, text))
            if hub.loads is not None:
                hub.loads.register(client)

        for client in (hub.gpu, hub.gpu_heavy, getattr(hub, "gpu_follow", None), hub.gpu_molmo):
            if client is not None:
                told(client)
        if hasattr(hub, "host_hooks"):
            hub.host_hooks.append(told)
        enrich = Enricher(hub, research, hub.broadcast, root=paths.research)

        async def judge(prompt: str) -> Judgment:
            """A click on a find step, judged by the fast model from the facts (quiz.py); no picture."""
            option = hub.settings.fast if hub.settings.fast != "off" else hub.settings.vision
            result = await agent.vision(option).ask([], prompt, max_output_tokens=600)
            return Judgment(result.data, result.model, result.error)

        def capture_quiz(client=None):
            """Bind the actual day, thread and voice (including absence) before judging."""
            owner = day
            asked = day.here()
            voice = brain.voice
            if client is not None and getattr(voice, "browser_client", None) is not client:
                voice = None
            option = hub.settings.fast if hub.settings.fast != "off" else hub.settings.vision

            def say_quiz(text: str | None, object_id: str, facts: dict) -> None:
                """Facts are activity; only the judge's unchanged feedback is assistant text."""
                facts = {**facts, "source": {"kind": "quiz-judge", "option": option, **facts.get("source", {})}}
                target = Quiz.reply_store(owner, asked)
                store, filing = target if target is not None else (owner, asked)

                def publish(line: dict) -> None:
                    # Notices carry their day on the wire. Chat's live routing
                    # has only a thread ID: older-day feedback stays in replay.
                    if store is day or line["type"] == "app-notice":
                        dialogue.publish(line)

                shown = dialogue if store is day else Dialogue(store, publish, log=dialogue.log)
                status = facts["judgeStatus"]
                summary = f"Quiz click: {facts['verdict']}" if status == "judged" else f"Quiz click: judge {status}"
                shown.notice(producer={"kind": "quiz", "id": facts["rid"]}, event="judgment",
                             summary=summary, facts=facts, asked=asked, filing_asked=filing, rid=facts["rid"],
                             tone="info" if status == "judged" else "error",
                             refs={"objectIds": [object_id], "quiz": facts})
                if target is not None:
                    store.add_turn("note", "system", json.dumps(facts, ensure_ascii=False),
                                   asked=asked, filing_asked=filing)
                    if text is not None:
                        store.add_turn("assistant", "chat", text, asked=asked, filing_asked=filing, verbatim=True,
                                       refs={"objectIds": [object_id], "quiz": facts})
                        shown.show({"type": "chat", "role": "assistant", "text": text,
                                    "objectIds": [object_id], "quiz": facts}, asked=filing)
                # Never let Narrator.offer stamp a newly installed voice onto
                # a callback whose original owner was absent or has closed.
                if (target is not None and voice is not None and not voice.closed
                        and brain.voice is voice
                        and day.current(getattr(asked, "routing_generation", asked.generation))):
                    narrator.offer(Line(json.dumps(facts, ensure_ascii=False), "requested", asked=asked,
                                        delivery="silent", provenance="data", voice_owner=voice))
                    if text is not None:
                        narrator.offer(Line(text, "requested", asked=asked, provenance="model", voice_owner=voice))

            return say_quiz

        quiz = Quiz(hub, presenter, day, ask=judge, capture=capture_quiz, sight_view=lambda: sight.view, log=agent.log)
        arena = Arena(hub, agent, votes_log=paths.votes_log)
        # Record's dataset (datasets/): while the user records, everything is copied into a dataset as it
        # happens, the camera's video included; Record and its Stop are its only edges (ADR 0009).
        from . import capabilities, licences

        # The microscope as the user describes it (outlives days): a field number they gave is the one
        # the scale uses, unless today's was given too.
        rig = Rig(paths.datasets)
        given = rig.facts.get("fieldNumber", {}).get("value")
        if given and scale.field_number_source != "user":
            try:
                scale.set_field_number(given, via="microscope facts")
            except ValueError:
                pass
        datasets = DatasetCollector(
            paths.datasets, bridge_health=lambda: hub.source.bridge_health, bridge_url=hub.source.bridge_url,
            broadcast=hub.broadcast, snapshot=lambda: app.snapshot(),
            scene=lambda: (hub.scene.version, hub.scene.to_wire()),
            preroll=lambda: list(clip.frames),
            sources=lambda: {"interactions": paths.interaction_log, "votes": paths.votes_log,
                             "conversation": paths.conversation_log, "journal": day.journal.path},
            about=lambda: {"build": capabilities.build(), "edition": licences.edition()},
            rig=lambda: at_start(rig, scale, hub.source.bridge_health, hub.latest_info, hub.source.fps()),
            log=agent.log)
        hub.datasets = datasets  # the brain and the voice are told while it is on (brain/context.py)
        agent.taps.append(datasets.interaction)
        agent.image_taps.append(datasets.keep_log_picture)
        arena.taps.append(datasets.vote)
        hub.on_frame.append(datasets.frame)
        magnifier.picture_taps.append(datasets.keep_picture)
        services.keep = datasets.keep_file
        hub.evidence.keep = datasets.keep_file  # a dataset being recorded keeps the evidence saved meanwhile
        hub.tracks_store.keep = datasets.keep_file  # and the motion tracks

        def scale_noted() -> None:
            info = hub.latest_info
            datasets.record("scale", scale.wire(info.fov.radius if info is not None and info.fov is not None else None))

        scale.listeners.append(scale_noted)
        app = cls(paths=paths, hub=hub, day=day, conversation=conversation, dialogue=dialogue, subjects=subjects,
                  agent=agent, research=research, services=services, brain=brain, narrator=narrator, sight=sight,
                  presenter=presenter, magnifier=magnifier, memory=memory, announcer=announcer, enrich=enrich,
                  arena=arena, board=board, slidemap=slidemap, taught=taught, quiz=quiz, seen=seen, masker=masker, molmo=molmo,
                  datasets=datasets, rig=rig)
        services.workspace_context = app.workspace_context
        app.registrations = registrations
        # Dataset tables (datatables.py): measured tables pooled and edited, each version with its rows; a correction of a
        # population a following table pooled makes its next version, debounced and read off the loop.
        from .datatables import DataTables

        hub.datatables = DataTables(app, paths.results / "tables")
        hub.evidence.listeners.append(hub.datatables.population_changed)
        from .analysis_math import analyze
        from .graph_math import graph
        from .table_derivatives import TableDerivatives
        hub.table_derivatives = TableDerivatives(hub.datatables, paths.results / "table-derivatives",
                                                {'analysis': analyze, 'graph': graph})
        from .workspace_tabs import WorkspaceTabs
        hub.workspace_tabs = WorkspaceTabs(app)
        from .experiments import experiments_of
        experiments_of(hub, day, app)  # experiments (Part 2.5, W5): their plans' cards, their runs and their results
        from .capabilities import Verdicts

        app.verdicts = Verdicts(paths.verdicts)
        from .model_checks import Checks

        # The app's own check of each model (model_checks.py, Slice 8): shown beside the verdicts, told to the models,
        # and `use`/`set_default` refuse a model whose last check on this build failed.
        hub.checks = Checks(paths.checks, version=app.verdicts.version)
        app._wire()
        # Every outline the app draws is named by the same step as a tapped one (option NAMING, tools.name_like_a_tap).
        naming: set = set()

        def namer(object_id: str) -> None:
            from .commands import name_drawn

            try:
                task = asyncio.get_running_loop().create_task(name_drawn(app, object_id))
            except RuntimeError:  # no loop (a test building the app): named when asked
                return
            naming.add(task)
            task.add_done_callback(naming.discard)

        hub.namer = namer
        app.say_resumed()  # the session it comes back to: its conversations are here, its live outlines are not
        app._reopen_layer = day.layer_shown  # its cells' layer comes back on its picture, if the studio lands it there
        if restored_defaults:
            from . import defaults as kept_defaults

            # Said on a restart: what still holds (with its day when not today), and what can't run now (Q15).
            said = kept_defaults.words(restored_defaults, hub.settings, clock=day.clock)
            conversation.add("note", "system", said)
            dialogue.notice(producer={"kind": "model-choice"}, event="defaults-restored", summary=said,
                            facts={"restored": restored_defaults})
            agent.log({"kind": "defaults-restored", "restored": restored_defaults})
            for hook in list(kept_defaults.RESTORED):
                try:
                    hook(hub, restored_defaults)
                except Exception as e:  # a preload that fails never stops the app starting; it is logged
                    agent.log({"kind": "defaults-restored", "hookError": f"{type(e).__name__}: {e}"})
        return app

    def _wire(self) -> None:
        hub, research, brain = self.hub, self.research, self.brain
        from .commands import NativeManualCommands
        self.manual_commands = NativeManualCommands(self)
        screen = hub.screen
        from .scientific_reviews import ScientificReviews
        from . import workspace_views
        self.scientific_reviews = ScientificReviews(screen, hub.resources.projects,
            lambda project: workspace_views.read(self.day.elsewhere(project), schema=workspace_views.SCHEMA)['layout'],
            lambda: self.day.sid)
        screen_gone = screen.gone

        def workspace_gone(client):
            profile = screen._accepted.get(client)
            self.manual_commands.gone(client)
            self.scientific_reviews.gone(client)
            screen_gone(client)  # existing observer/acquisition cleanup remains its owner
            if profile is not None:
                self.services.presentations.disconnect(self._binding_context(profile.ref))

        screen.gone = workspace_gone  # same disconnect path used by server and reacceptance

        def saw(view) -> None:
            if self.subjects.observe(view):
                self.sight.recheck = True  # not sure the view changed yet: look again soon

        self.sight.listeners.append(saw)

        def subject_work(work, *args):
            # Session data can also change synchronously, with no running app.
            # Check the scheduler before constructing a coroutine.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(work(*args))

        if self.agent.zeroshot is not None:  # a subject MedSigLIP has names for: load it before a click needs it
            self.subjects.on_change.append(lambda old, new: subject_work(self.agent.zeroshot.warm))
        self.subjects.on_change.append(lambda old, new: subject_work(brain.note_to_voice,
            f"Context only, do not read aloud: the view changed. It now shows {new or 'something else'}"
            f"{f' (before: {old})' if old else ''}; talk about what is in view now."))
        self.day.listeners.append(self._session_event)
        if self.datasets is not None:
            self.day.listeners.append(self.datasets.journal)
        research.on_change.append(lambda session: hub.broadcast({"type": "research", "session": session.wire(),
                                                                 "spentToday": round(research.spent_today(), 4),
                                                                 "estimatedToday": round(research.estimated_today(), 4)}))
        research.on_change.append(self._research_changed)  # a researcher started or finished: the sessions list
        def workspace_research_finished(session):
            origin = (session.origin or {}).get('workspace')
            if origin:
                from .workspace_views import reconcile_research
                try:
                    reconcile_research(self.day.elsewhere(origin['session']), [session])
                except (ValueError, OSError):
                    pass  # The retained result is independent; workspace reconnect retries presentation.
        research.on_done.append(workspace_research_finished)
        research.on_done.append(lambda session: asyncio.ensure_future(self.announcer.research_finished(session)))
        research.on_presentation.append(self.announcer.presentation_finished)
        research.on_correction.append(self.announcer.correction)
        research.on_plan.append(self.announcer.research_plan)
        research.on_request.append(self.announcer.research_request)  # a researcher asks the user for evidence
        brain.on_answer.append(lambda result: self.enrich.mark_asked(result.get("objectIds")))
        activity.wire(self)  # setting changes, researchers' starts and tool runs, said in Activity

    @staticmethod
    def _binding_context(ref):
        # Registration/metadata only. This never admits a request or prepares bytes.
        return PresentationContext(ref.workspace_id, ref.client_id, ref.page_epoch,
                                   ref.connection_epoch, "server:workspace", ref.connection_epoch)

    def accept_workspace(self, socket, ref: ClientRef) -> None:
        """Trusted connection adapter only; reports and transport fields cannot call it."""
        if not isinstance(ref, ClientRef) or socket is None:
            raise ObservationError("CLIENT_MISMATCH", "workspace acceptance needs a socket and canonical ClientRef")
        try:
            hash(socket)
        except TypeError:
            raise ObservationError("CLIENT_MISMATCH", "a client identity dictionary is not a socket") from None
        screen, presentations = self.hub.screen, self.services.presentations
        if any(client is not socket and profile.ref == ref for client, profile in screen._accepted.items()):
            raise ObservationError("CLIENT_MISMATCH", "connection identity is already bound to another socket")
        peers = [client for client, profile in screen._accepted.items()
                 if client is not socket and profile.ref.workspace_id == ref.workspace_id]
        if socket not in screen._accepted and len(screen._accepted) - len(peers) >= presentations.max_workspaces:
            raise ObservationError("CLIENT_MISMATCH", "accepted workspace capacity reached")
        binding = self._binding_context(ref)
        presentations.connect(binding)  # validates bounded workspace admission before retiring peers
        for peer in peers:
            screen.gone(peer)
        screen.accept_client(socket, ref, app_run_id=RUN,
                             source_resolver=self._workspace_source,
                             closed_source_resolver=self._workspace_closed_source,
                             failed_source_resolver=self._workspace_failed_source,
                             capture_authority=self._workspace_capture_authority or
                             (lambda client, pending, reply: False))
        # accept_client retires an older binding through the same gone callback;
        # reconnect even when the explicitly accepted incarnation is unchanged.
        presentations.connect(binding)

        self.manual_commands.accepted(socket, screen._profile(socket))

    def workspace_context(self, socket, *, request_id, owner) -> PresentationContext:
        """Route the original request through the existing accepted Screen profile."""
        try:
            profile = self.hub.screen._profile(socket)
        except TypeError:
            raise ObservationError("CLIENT_MISMATCH", "a client identity dictionary is not an accepted socket") from None
        if profile.app_run_id != RUN:
            raise ObservationError("CLIENT_MISMATCH", "workspace belongs to another app run")
        for value in (request_id, owner):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ObservationError("INVALID_REPORT", "original request and owner need bounded identifiers")
        ref = profile.ref
        return PresentationContext(ref.workspace_id, ref.client_id, ref.page_epoch,
                                   request_id, owner, ref.connection_epoch)

    def _workspace_source(self, socket, supplied):
        profile = self.hub.screen._profile(socket)
        ctx = self.workspace_context(socket, request_id=profile.ref.connection_epoch, owner="server:metadata")
        lease = None
        store = self.services.prepared_sources
        try:
            lease = store.resolve(SourceRef.from_wire(supplied), context=ctx)
            return lease.source
        except PresentationError as error:
            refused = ObservationError("SOURCE_UNRESOLVED", "no exact prepared source grant for this connection")
            # Ended, not wrong: nothing is granted under this key for this connection any more (its window was
            # closed and its pin released, or it was never granted here; or a live source whose close took its
            # visibility, pinned only by that close's read-only borrow, so its live check fails `delivery_revoked`).
            # Screen leaves that window out of the report. Facts differing from a source still prepared, and a live
            # window still claiming to draw from a producer that stopped (its visibility owner is still there; the
            # studio says it failed, then `_workspace_failed_source` takes it), stay refused.
            refused.ended = (error.code in ("source_not_prepared", "delivery_revoked")
                             and not store.prepared_key(supplied.get("key"), ctx))
            raise refused from None
        finally:
            if lease is not None and lease.release is not None:
                lease.release()  # only this synchronous metadata borrow; window owns its pin

    def _workspace_failed_source(self, socket, supplied, rows):
        """Keep a failed window inspectable/closable without renewing its pixels."""
        from .presentation_delivery import _source_json

        profile = self.hub.screen._profile(socket)
        ctx = self.workspace_context(socket, request_id=profile.ref.connection_epoch, owner="server:failed-metadata")
        core, store = self.services.presentations, self.services.prepared_sources
        try:
            core.workspace_generation(ctx)
            projected = _source_json(supplied)
            original = None
            for row in rows:
                if not row["open"]:
                    original = self._workspace_closed_source(socket, supplied, [row])
                    continue
                state = core.get(ctx.workspace_id, row["id"])
                if (not state.open or row["workspaceId"] != ctx.workspace_id
                        or state.lifetime_generation != row["lifetimeGeneration"]
                        or state.source_generation != row["sourceGeneration"]
                        or state.revision != row["revision"]
                        or dict(state.property_versions) != row["propertyVersions"]
                        or dict(state.owners) != row["propertyOwners"]
                        or _source_json(state.source.to_wire()) != projected
                        or row.get("sourceToken") != row.get("load", {}).get("token")):
                    raise ObservationError("SOURCE_UNRESOLVED", "failed metadata differs from the original Core state")
                # Existing readonly close borrow checks exact accepted origin;
                # it grants neither visibility nor access to current pixels.
                lease = store.borrow(row.get("sourceToken"), ctx, closing=True)
                try:
                    if _source_json(lease.source.to_wire()) != projected:
                        raise ObservationError("SOURCE_UNRESOLVED", "failed metadata names another prepared source")
                    original = lease.source
                finally:
                    lease.release()
            if original is None:
                raise ObservationError("SOURCE_UNRESOLVED", "failed metadata has no original Core instance")
            return original
        except PresentationError:
            raise ObservationError("SOURCE_UNRESOLVED", "no exact failed source for this connection") from None

    def _workspace_closed_source(self, socket, supplied, rows):
        """Original closed Core metadata, granting no image access or visibility.

        Screen has excluded every represented current consumer before this call.
        Use the existing canonical source projection, including browser JSON
        number semantics, rather than borrowing a revoked prepared source.
        """
        from .presentation_delivery import _source_json

        profile = self.hub.screen._profile(socket)
        ctx = self.workspace_context(socket, request_id=profile.ref.connection_epoch, owner="server:closed-metadata")
        core = self.services.presentations
        try:
            core.workspace_generation(ctx)  # original accepted incarnation, no request/admission borrowed
            projected = _source_json(supplied)
            original = None
            for row in rows:
                state = core.get(ctx.workspace_id, row["id"])
                if (state.open or row["workspaceId"] != ctx.workspace_id
                        or state.lifetime_generation != row["lifetimeGeneration"]
                        or state.source_generation != row["sourceGeneration"]
                        or state.revision != row["revision"]
                        or dict(state.property_versions) != row["propertyVersions"]
                        or dict(state.owners) != row["propertyOwners"]
                        or _source_json(state.source.to_wire()) != projected):
                    raise ObservationError("SOURCE_UNRESOLVED", "closed metadata differs from the original Core state")
                original = state.source
            if original is None:
                raise ObservationError("SOURCE_UNRESOLVED", "closed metadata has no original Core instance")
            return original
        except PresentationError:
            raise ObservationError("SOURCE_UNRESOLVED", "no exact closed Core source for this connection") from None

    def _workspace_request_guard(self, ctx, *, allow_cancelled=False):
        ticket = current()
        why = self.manual_commands.guard(ticket, ctx, allow_cancelled=allow_cancelled)
        if why:
            return why
        origin = (ctx.workspace_id, ctx.client_id, ctx.client_epoch, ctx.connection_epoch)
        if not any((profile.ref.workspace_id, profile.ref.client_id, profile.ref.page_epoch,
                    profile.ref.connection_epoch) == origin for profile in self.hub.screen._accepted.values()):
            return "the originating connection is no longer accepted"
        if (ticket is None or ticket.closed or ticket.admission is None
                or ticket.admission.request_id != ctx.request_id):
            return "the original request is no longer active"
        if allow_cancelled:
            # Cancellation alone permits conditional undo; it must not mask a
            # changed session as superseded()'s cancellation-first answer would.
            why = ("another session was opened since the request was made"
                   if ticket.issued.get("session") != ticket.generations.value("session") else None)
        else:
            why = ticket.superseded("session")
        if why:
            return why
        if not self.day.current(getattr(ticket.asked, "routing_generation", getattr(ticket.asked, "generation", None))):
            return "the original request belongs to another session"
        return None

    # --- running ------------------------------------------------------------------------
    async def start(self) -> None:
        from . import looplag

        self._stopping = False
        looplag.warm_imports()  # what would stall the loop when first used, imported now off it (looplag.WARM_IMPORTS)
        self._tasks.append(asyncio.create_task(self.warm_vision()))
        from . import fiji_sandbox

        await asyncio.to_thread(fiji_sandbox.supported)
        load_keys(self.paths.keys_file)
        from .threads import apply_cv_threads, raise_this_thread

        apply_cv_threads()  # OpenCV's threads in this process (threads.py)
        raise_this_thread()  # the event loop: the frame path's scheduling class
        loop = self._loop = asyncio.get_running_loop()
        await self.start_environment_observations()
        from .result_entries import clear_exports

        self._tasks.append(asyncio.create_task(asyncio.to_thread(clear_exports)))  # portable copies left from before
        registrations = getattr(self, "registrations", None)
        if registrations is not None:
            registrations.post = lambda fn: loop.call_soon_threadsafe(fn)
            self._tasks.append(asyncio.create_task(self.recover_jobs()))
        if self.hub.loop_lag is not None:
            self.hub.loop_lag.start(loop)
        self.hub.machine.start(loop)
        await self.hub.start()
        self.enrich.start()
        self.sight.start()
        if self.masker is not None:
            self.masker.start()
        if self.molmo is not None:
            self.molmo.start()
        if self.agent.dust is not None:
            self.agent.dust.start()
        if self.hub.follower is not None:
            self.hub.follower.start()
        if self.datasets is not None:
            self.datasets.resume()  # a dataset that was on when the app stopped carries on
            self._tasks.append(asyncio.create_task(self.datasets.run_forever()))
        if self.taught is not None:
            self._tasks.append(asyncio.create_task(self.taught.warm()))  # the model, before a click needs it
        if self.agent.zeroshot is not None:
            self._tasks.append(asyncio.create_task(self.agent.zeroshot.warm()))
        if getattr(self.hub, "loads", None) is not None and os.environ.get("MICROMIND_PRELOAD", "1") != "0":
            self._tasks.append(asyncio.create_task(self.hub.loads.keep(self.hub)))  # the wanted set, ready, and memory eased (loads.py)
        self._tasks.append(asyncio.create_task(self.watch_mine()))  # your own models: shown and offered as the folder changes
        self._tasks.append(asyncio.create_task(self.observer.run_forever()))  # licences and keys, asked of the world (observed.py)
        self._tasks.append(asyncio.create_task(self.verify_downloads()))  # published files' content, checked again once
        from . import readiness

        if readiness.keeping_wanted() and readiness.keep(True):  # facts kept, dropped on change, read again off the loop
            self._tasks.append(asyncio.create_task(self.keep_readiness()))
        self._tasks.append(asyncio.create_task(self.check_catalog()))  # new capabilities arrive by themselves (catalog_news.py)
        self.maintainer.start()  # entries' updates under the policy a person recorded, off the loop (maintenance/)
        self._tasks.append(asyncio.create_task(self.carry_turn_on()))  # one press turns a job on (capability_shelf.py)
        if getattr(self.hub, "library_looks", None) is not None and self.hub.library_looks.enabled():
            self._tasks.append(asyncio.create_task(self.hub.library_looks.run_forever()))  # each new asset filed by a look

    async def keep_readiness(self, every: float = READINESS_REFRESH_S) -> None:
        """The kept readiness facts (readiness.KEPT_KINDS) read again in a thread every `every` s, each by its own read,
        so the loop reads facts already read (a package installed by hand, weights made in another process, files
        changed outside the app: none of them is an event)."""
        from . import readiness

        while True:
            await asyncio.sleep(every)
            try:
                await asyncio.to_thread(readiness.refresh_kept)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - read on the loop as before until the next refresh
                self.agent.log({"kind": "readiness-refresh", "error": f"{type(exc).__name__}: {exc}"[:200]})

    async def check_catalog(self, first_after: float | None = None, every: float = 3600.0) -> None:
        """New capabilities arrive by themselves (catalog_news.py, C1): a while after the start, then whenever a day has
        passed since the last check, the reviewed catalog is peeked at (peek_catalog: manifests and pictures only, never
        code). Nothing downloads, installs, runs or turns on: what is new is offered on the Models page and to the agent
        when asked."""
        from . import catalog_news

        await asyncio.sleep(catalog_news.FIRST_AFTER_S if first_after is None else first_after)
        while True:
            if not self.day.local_only and catalog_news.due():
                try:
                    await self.peek_catalog()
                except Exception:  # noqa: BLE001 - offline, or GitHub unreachable: said in Activity, tried again later
                    pass
            await asyncio.sleep(every)

    async def peek_catalog(self) -> dict:
        """The app's own daily look at the reviewed catalog (channels.peek): each entry's manifest and pictures, never an
        adapter, a lockfile or weights; what it brings is New for you, got by the user's Get it."""
        from . import activity, catalog_news, channels

        from research_worker import api_usage
        if self.day.local_only:
            raise api_usage.ProviderRefused(api_usage.LOCAL_ONLY)
        line = f"catalog-peek:{int(time.time())}"
        await asyncio.to_thread(catalog_news.state)  # what this Mac knew before the look: what it brings is new
        try:
            r = await asyncio.to_thread(channels.peek)
        except channels.CantDo as exc:
            why = str(exc)
            plain = ("its catalog repository wasn't found" if "not found" in why
                     else "there is no connection to its catalog" if any(w in why for w in ("resolve host", "connect", "timed out"))
                     else why.splitlines()[0][:160])
            says = f"Couldn't look for new capabilities: {plain}"  # plain words; git's own in the details
            await asyncio.to_thread(catalog_news.record_check, {"ok": False, "says": says})
            activity.activity(self.hub, "model", says, state="failed", id=line, details={"why": why[:600]})
            raise
        await asyncio.to_thread(catalog_news.record_peek, r)
        fresh = await asyncio.to_thread(lambda: [n for n in catalog_news.new() if not n.get("fetched")])
        says = (f"{len(fresh)} new capabilit{'y' if len(fresh) == 1 else 'ies'} in the catalog: "
                + ", ".join(n["name"] for n in fresh[:4]) + (", …" if len(fresh) > 4 else "")) if fresh else \
            "Nothing new in the catalog"
        activity.activity(self.hub, "model", f"The app looked at the catalog: {says[0].lower()}{says[1:]}", state="done",
                          id=line)
        self.hub.broadcast(self.capabilities_wire())
        return {**r, "says": says}

    async def update_catalog(self, by: str = "you", commit: str | None = None) -> dict:
        """The reviewed catalog at its newest commit (channels.update: manifests and adapters, read only, never run),
        recorded for New for you (catalog_news.record_check), said in Activity and on the Models page. `by` "you" (the
        button) or "app" (the daily check). Raises channels.CantDo with its words."""
        from gpu_worker import manifests as catalog

        from . import activity, catalog_news, channels

        line = f"catalog-update:{int(time.time())}"
        kind = "command" if by == "you" else "model"
        self.channel_update = {"state": "running", "says": "Checking the reviewed catalog for new capabilities…"}
        activity.activity(self.hub, kind, "Checking the reviewed catalog for new capabilities", state="running", id=line)
        self.hub.broadcast(self.capabilities_wire())
        await asyncio.to_thread(catalog_news.state)  # what this Mac knew before the check: what it brings is new
        try:
            r = await asyncio.to_thread(lambda: channels.update(commit=commit))
        except channels.CantDo as exc:
            says = f"Couldn't check for new capabilities: {exc}"
            self.channel_update = {"state": "failed", "says": says}
            await asyncio.to_thread(catalog_news.record_check, {"ok": False, "says": says})
            activity.activity(self.hub, kind, says, state="failed", id=line)
            self.hub.broadcast(self.capabilities_wire())
            raise
        names = [getattr(catalog.get(i), "name", i) for i in r["added"]]
        says = (f"New in the catalog: {', '.join(names)}" if names else "Nothing new to turn on in the catalog") + \
            (f"; {len(r['later'])} more need an app update" if r["later"] else "") + \
            f" (the reviewed catalog at {r['commit'][:7]})"
        self.channel_update = {"state": "done", "says": says}
        await asyncio.to_thread(catalog_news.record_check, {"ok": True, **r, "says": says})
        activity.activity(self.hub, kind, says if by == "you" else f"The app checked the catalog: {says[0].lower()}{says[1:]}",
                          state="done", id=line)
        self.catalog_changed()
        return {**r, "says": says}

    @property
    def turn_on(self):
        """The presses of Turn on being carried through (capability_shelf.TurnOn)."""
        if getattr(self, "_turn_on", None) is None:
            from .capability_shelf import TurnOn

            self._turn_on = TurnOn(self)
        return self._turn_on

    async def carry_turn_on(self, every: float | None = None) -> None:
        """While a Turn on is open, its next step every few seconds (a download done, a key saved, an approval come
        through), and the studios told how it stands. Nothing runs while none is open."""
        from .capability_shelf import ADVANCE_S

        observed_at = 0.0
        while True:
            await asyncio.sleep(ADVANCE_S if every is None else every)
            if self.hub.clients and time.monotonic() - observed_at >= 30:
                try:
                    await self.refresh_shelf_observations()
                except Exception as exc:
                    self.agent.log({'kind': 'catalog-outcomes', 'error': f'{type(exc).__name__}: {exc}'})
                observed_at = time.monotonic()
            try:
                await self.offer_catalog_news()
            except Exception as exc:
                self.agent.log({'kind': 'catalog-news', 'error': f'{type(exc).__name__}: {exc}'})
            if self.turn_on.intents:
                try:
                    self.turn_on.advance()
                    self.hub.broadcast(self.capabilities_wire())
                except Exception as exc:  # noqa: BLE001 - tried again on the next tick; what failed is logged
                    self.agent.log({"kind": "turn-on", "error": f"{type(exc).__name__}: {exc}"})

    async def refresh_shelf_observations(self) -> None:
        from .capability_shelf import observations
        self._shelf_observations = await asyncio.to_thread(observations, self.hub)

    async def offer_catalog_news(self) -> bool:
        """One relevant suggestion, only between requests; durable app facts, with optional idle model narration."""
        from . import catalog_news, ticket
        from .untrusted import inline

        def idle():
            voice = self.brain.voice
            return (not self._stopping and not ticket.working(self.hub) and not self.hub.settings.quieter
                    and not self.hub.settings.user_leads and (voice is None or voice.idle_since() >= 1))
        if not idle():
            return False
        asked = self.day.here()
        # Use the page's same ready/check filtering, including genuine updates to already enabled entries.
        rows = await asyncio.to_thread(lambda: catalog_news.unmentioned(
            self.capabilities_wire(counts=False)['setup']['news']['new']))
        if not rows or not idle() or not self.day.current(asked.generation):
            return False
        row = rows[0]
        name = inline(row['name'], 120)
        says = f"{'Update available' if row.get('update') else 'New for this specimen'}: {name}. Open Capabilities to review it; nothing has been turned on."
        self.dialogue.notice(producer={"kind": "catalog-news", "id": row['key']}, event='offered', summary=says,
                             facts=row, asked=asked)
        await asyncio.to_thread(catalog_news.mark_mentioned, row)
        voice = self.brain.voice
        if voice is not None and not voice.closed:
            self.narrator.offer(Line(catalog_news.words_for_agent([row]), 'suggestion', asked=asked,
                                      voice_owner=voice, notify=True,
                                      still=lambda: self.day.current(asked.generation) and idle()))
        return True

    def turned_on(self, job: str, says: str, *, asked=None, line: str | None = None) -> None:
        """A job a press turned on is on: said once in Activity and in the conversation. `line`: the Activity line that
        already said it (an install queue's "on again"), replaced in place rather than said twice."""
        from . import activity

        activity.activity(self.hub, "model", says, state="done", id=line or f"turn-on:{job}:{int(time.time())}", asked=asked)
        try:
            self.dialogue.notice(producer={"kind": "turn-on", "id": job}, event="on", summary=says, facts={"job": job}, asked=asked)
        except Exception as exc:  # noqa: BLE001 - Activity said it; the conversation's line is logged when it fails
            self.agent.log({"kind": "turn-on", "error": f"{type(exc).__name__}: {exc}"})
        # The one-brain voice says it in one line when nobody is talking (voice/partner.py `happened`); GPT-Live has none.
        happened = getattr(getattr(getattr(self, "brain", None), "voice", None), "happened", None)
        if callable(happened):
            with contextlib.suppress(RuntimeError):  # no running loop (a test's direct call): nothing to say it on
                asyncio.get_running_loop().create_task(happened("capability-on", says, ref=job))

    async def activate_mine(self, key: str, *, asked=None, attempt=None) -> dict:
        """An owned setup check of a user's weights on the public synthetic example; never private bench footage."""
        from gpu_worker import manifests
        from . import activity, capability_example, mine, owned_process

        m = manifests.mine().get(key)
        if m is None:
            raise ValueError("there is no model of yours by that name")
        asked = asked or self.day.here()
        line = f"check:{m.id}:{__import__('uuid').uuid4().hex}"
        activity.activity(self.hub, "model", f"Checking {m.name} on the public synthetic example", id=line,
                          state="running", ref={"model": key}, asked=asked)
        try:
            frame = await owned_process.off_loop(capability_example.picture, self.paths)
            check = await owned_process.off_loop(mine.activate, key, frame, attempt=attempt)
            steps = [("✓ " if step.get("ok") else "✗ ") + step.get("says", "") for step in check.get("steps", [])]
            activity.activity(self.hub, "model", f"{m.name}: " + ("functional check passed" if check.get("ok") else
                              "its check did not pass"), id=line, state="done" if check.get("ok") else "failed",
                              ref={"model": key}, details={"lines": steps}, asked=asked)
            await self.sync_mine()
            return check
        except asyncio.CancelledError:
            activity.activity(self.hub, "model", f"Check of {m.name} stopped", id=line, state="failed", asked=asked)
            raise
        except (mine.CantDo, owned_process.Stopped) as exc:
            activity.activity(self.hub, "model", f"Could not check {m.name}: {exc}", id=line, state="failed", asked=asked)
            raise

    def news_about(self) -> list[str]:
        """What this session looks at, in words, for which new capabilities fit (catalog_news.relevant): its specimen and
        its name."""
        out = []
        scene = getattr(self.hub, "scene", None)
        specimen = (getattr(scene, "context", None) or {}).get("specimen") if scene is not None else None
        if specimen:
            out.append(str(specimen))
        name = getattr(getattr(self, "day", None), "name", None)
        if isinstance(name, str) and name:
            out.append(name)
        return out

    async def verify_downloads(self, first_after: float = 60.0) -> None:
        """Hash published files in a separate background child, so the live view's threads keep their time.
        Cancellation/stop waits for that owned child's termination and reap; only complete JSON refreshes the catalog.
        """
        import json
        import subprocess
        import sys

        from . import downloads, owned_process
        from .paths import SERVICES
        from .thumbnails import background

        def check():
            process = subprocess.Popen(background([sys.executable, "-m", "orchestrator.downloads", "verify"]),
                                       cwd=str(SERVICES), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
            self._verifying = process
            try:
                with owned_process.watching(process, downloads.VERIFY_S, "Catalog file verification"):
                    out, _ = process.communicate()
                if process.returncode:
                    raise ValueError(f"verification exited {process.returncode}")
                found = json.loads(out)
                if not isinstance(found, dict) or not all(isinstance(v, str) for v in found.values()):
                    raise ValueError("verification returned no complete file report")
                return found
            finally:
                owned_process.terminate(process)
                self._verifying = None

        self._verification_task = asyncio.current_task()
        try:
            await asyncio.sleep(first_after)
            found = await owned_process.off_loop(check)
        except (OSError, ValueError, owned_process.Stopped) as exc:
            self.agent.log({"kind": "catalog-verification", "state": "failed", "error": str(exc)[:300]})
            return
        finally:
            self._verification_task = None
        if any(found.values()):
            self.catalog_changed()

    async def start_environment_observations(self) -> None:
        from .env_observations import Observer

        self._environment_observer = Observer(
            lambda: self.hub.broadcast(self.capabilities_wire()),
            lambda exc: self.agent.log({"kind": "environment-observation", "error": f"{type(exc).__name__}: {exc}"}))
        await self._environment_observer.refresh()

    async def watch_mine(self) -> None:
        """Your own models (mine.py): the folder read again every RESCAN_S off the loop; when it changed, the turned-on
        ones are the choices under Cells, and every studio sees the Models page again. Never runs anything an entry names."""
        from . import mine, envs

        last = None
        while True:
            try:
                # Poll even when receipt signatures did not change: an interpreter, off marker or lock
                # can disappear independently. Explicit mutations also request an immediate refresh.
                if self._environment_observer is not None:
                    await self._environment_observer.refresh()
                seen = await asyncio.to_thread(lambda: (mine.signature(), envs.signature()))
                if seen != last:
                    last = seen
                    await self.sync_mine()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a folder that can't be read is said on the page, never fatal
                self.agent.log({"kind": "mine-rescan", "error": f"{type(exc).__name__}: {exc}"[:300]})
            await asyncio.sleep(mine.RESCAN_S)

    def resume_defaults(self) -> list[dict]:
        """Kept choices that can run again (a download, an install or a key since a restart fell back to the default)
        are the user's choice again now, with what a change needs started, and said (defaults.resume)."""
        from . import defaults
        from .commands import after_change

        back = defaults.resume(self.hub.settings, self.day)
        if not back:
            return back
        changes = {r["group"]: r["option"] for r in back}
        problems = self.hub.settings.update(changes)
        after_change(self, changes, problems)
        self.publish_settings()
        said = defaults.resumed_words(back, clock=self.day.clock)
        self.conversation.add("note", "system", said)
        self.dialogue.notice(producer={"kind": "model-choice"}, event="defaults-resumed", summary=said,
                             facts={"resumed": back})
        self.agent.log({"kind": "defaults-resumed", "resumed": back})
        return back

    def _retire_catalog_client(self, client) -> None:
        """The choice is already withdrawn; reap its owned child off the loop, including at shutdown."""
        from . import owned_process

        async def reap():
            try:
                await owned_process.off_loop(client.stop)
            except Exception as exc:  # noqa: BLE001 - report cleanup failure without an unobserved task exception
                self.agent.log({"kind": "catalog-worker-stop", "state": "failed", "error": str(exc)[:300]})

        task = asyncio.create_task(reap())
        self._catalog_cleanup.add(task)
        task.add_done_callback(self._catalog_cleanup.discard)

    async def recover_jobs(self) -> None:
        """At start, off the loop (ending a job a crash left running waits for it: jobs.end): every job that was running
        when the app stopped is interrupted and said, never resumed by itself (the review of 2026-10-13, finding 4)."""
        registrations = getattr(self, "registrations", None)
        if registrations is not None:
            for record in await asyncio.to_thread(registrations.interrupted):
                self.job_said(record)

    def job_said(self, record: dict) -> None:
        """A job's state, said where the user sees it, from any thread: one Activity line per job, updated in place
        (queued, running, done, failed, cancelled, interrupted); once it ends, a notice in the conversation it was asked
        in, with what it found."""
        def say() -> None:
            from . import activity

            registrations = self.registrations
            state = {"done": "done", "failed": "failed", "cancelled": "failed", "interrupted": "failed"}.get(record["state"], "running")
            activity.activity(self.hub, "command", registrations.words(record), state=state, id=f"job:{record['id']}",
                              ref={"kind": "job", "id": record["id"]})
            if record["state"] in ("done", "failed", "cancelled", "interrupted"):
                from .day.model import Asked

                self.dialogue.notice(producer={"kind": "job", "id": record["id"]}, event=record["state"],
                                     summary=registrations.words(record),
                                     facts={k: record.get(k) for k in ("id", "state", "volume", "seconds")},
                                     asked=Asked(record.get("thread") or "", 0, day=record.get("session"),
                                                 session=record.get("session"), stale=True),
                                     tone="info" if record["state"] == "done" else "error")
            self.hub.broadcast(self.capabilities_wire())

        loop = getattr(self, "_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(say)

    def catalog_changed(self) -> None:
        """Something the catalog depends on changed (a download, an answer from outside, your models, a check of the
        files, an install): entries that bring their own adapter are choices once proven (env_runner.register), kept
        choices that can run again apply, and every studio gets the capabilities again. On the loop."""
        from . import readiness

        readiness.invalidate("catalog changed")  # the kept facts (readiness.KEPT_KINDS) are read again
        if self._environment_observer is not None:
            self._environment_observer.request()
        try:
            from . import env_runner

            if env_runner.register(settings=self.hub.settings, retire=self._retire_catalog_client)["reset"]:
                self.publish_settings()
        except Exception as exc:  # noqa: BLE001 - the rest still runs; what failed is logged
            self.agent.log({"kind": "env-runner", "error": f"{type(exc).__name__}: {exc}"})
        from . import model_checks
        model_checks.invalidate()
        try:
            self.resume_defaults()
        except Exception as exc:  # noqa: BLE001 - the studio is still told; what failed is logged
            self.agent.log({"kind": "defaults-resumed", "error": f"{type(exc).__name__}: {exc}"})
        try:  # the agent's tools offer what can be chosen now, not what could at start
            from .brain import toolbox

            changed = toolbox.refresh(self.brain.registry, self.brain.services)
            if changed:
                self.agent.log({"kind": "tools-refreshed", "tools": changed})
        except Exception as exc:  # noqa: BLE001 - the tools keep their lists; what failed is logged
            self.agent.log({"kind": "tools-refreshed", "error": f"{type(exc).__name__}: {exc}"})
        if getattr(self, "_turn_on", None) is not None and self._turn_on.intents:
            try:  # a download or an install a press started finished: its next step now (capability_shelf.py)
                self._turn_on.advance()
            except Exception as exc:  # noqa: BLE001 - carried on by carry_turn_on's next tick
                self.agent.log({"kind": "turn-on", "error": f"{type(exc).__name__}: {exc}"})
        self.hub.broadcast(self.capabilities_wire())

    def request_catalog_changed(self, *, progress: bool = False) -> None:
        """catalog_changed, asked for from any thread (a download's or an install's): at most one waits on the loop, and
        it reads the state as it is when it runs, so the latest wins. `progress`: only a download's bytes moved on; the
        capabilities are built again at most every PROGRESS_FULL_S, and between, the last ones go out with their
        download rows renewed. `MICROMIND_LOOP_WORK=on-loop`: every change queues a whole catalog_changed, as before."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if not looplag.off_loop():
            loop.call_soon_threadsafe(self.catalog_changed)
            return
        with self._catalog_lock:
            asked = self._catalog_asked
            asked["progress" if progress else "full"] = True
            if asked["pending"]:
                return
            asked["pending"] = True
        try:
            loop.call_soon_threadsafe(self._catalog_due)
        except RuntimeError:  # the loop closed meanwhile
            with self._catalog_lock:
                self._catalog_asked["pending"] = False

    def _catalog_due(self) -> None:
        with self._catalog_lock:
            asked = self._catalog_asked
            full, progress = asked["full"], asked["progress"]
            asked.update(pending=False, full=False, progress=False)
        now = time.monotonic()
        if full or self._capabilities_sent is None or now - asked["built"] >= PROGRESS_FULL_S:
            asked["built"] = now
            self.catalog_changed()
        elif progress:
            self.catalog_progress()

    def catalog_progress(self) -> None:
        """A download moved on: every studio gets the last capabilities with the download rows (setup.downloads, each
        Models row's `download`) and the disk as they are now, a few ms, instead of the whole wire built again."""
        from . import downloads as downloads_

        wire = self._capabilities_sent
        rows = self.downloads.wire()
        by_model = {r.get("model"): r for r in rows}
        models = [{**m, "download": by_model[m["download"].get("model")]}
                  if isinstance(m.get("download"), dict) and m["download"].get("model") in by_model else m
                  for m in wire.get("models", [])]
        sent = {**wire, "setup": {**wire.get("setup", {}), "downloads": rows, "disk": downloads_.disk()}, "models": models}
        self._capabilities_sent = sent
        self.hub.broadcast(sent)

    async def sync_mine(self) -> dict:
        """Your turned-on models as choices now (mine.register), said when a setting that chose one went back to the
        default, and the capabilities sent again."""
        from . import mine

        changed = await asyncio.to_thread(mine.register, settings=self.hub.settings)
        if changed["reset"]:
            self.publish_settings()
            self.conversation.add("note", "system", "Cells went back to the default: the model of yours it used is off now")
        self.catalog_changed()
        return changed

    async def warm_vision(self) -> None:
        """The clients the first looks use (the selected vision model's and the voice's sight's), made now off the loop
        (vision._Client.warm), so the first look neither stalls the loop nor waits for them."""
        from .options import VISION

        settings = self.hub.settings
        for option in dict.fromkeys((getattr(settings, "vision", None), getattr(settings, "sight", None))):
            if option in VISION and self.agent.available(option):
                warm = getattr(self.agent.vision(option), "warm", None)
                if warm is not None:
                    await warm()

    async def stop(self) -> None:
        try:
            await self._stop()
        finally:
            media = getattr(self, '_project_stage_media', None)
            if media is not None:
                media.retire()
            if getattr(self.day, 'locations', None) is not None:
                # Completion/cancellation is not proof all writer threads ended.
                # Fence later admission and hold leases until process teardown.
                self.day.locations.retire()

    async def _stop(self) -> None:
        self._stopping = True
        self.hub.identity.io.retire()
        if getattr(self, "scientific_uploads", None) is not None:
            await asyncio.to_thread(self.scientific_uploads.close)
        from . import readiness

        readiness.keep(False)
        if getattr(self, "_maintainer", None) is not None:
            await asyncio.to_thread(self._maintainer.stop)  # its attempt ends with everything it started
        if self._environment_observer is not None:
            await self._environment_observer.close()
        if getattr(self, "_turn_on", None) is not None:
            await self._turn_on.close()
        if self._sessions_timer is not None:
            self._sessions_timer.cancel()
            self._sessions_timer = None
        sending = tuple(self._sessions_sending)
        for task in sending:
            task.cancel()
        if sending:
            await asyncio.gather(*sending, return_exceptions=True)
        if getattr(self, "workspace_transport", None) is not None:
            self.workspace_transport.save_all()
        self.manual_commands.retire("the app stopped")
        for error in await self.scientific_reviews.close():
            self.agent.log({'kind': 'scientific-review', 'state': 'failed', 'error': error})
        self.hub.scene.generations.bump("session", why="the app stopped")
        self.services.presentations.reset_all()
        for client in tuple(self.hub.screen._accepted):
            self.hub.screen.gone(client)
        for task in self._tasks:
            task.cancel()
        verifying = getattr(self, "_verification_task", None)
        if verifying is not None:
            verifying.cancel()
            await asyncio.gather(verifying, return_exceptions=True)  # its child is reaped before stop returns
        from . import env_runner, fiji_bindings

        installs = getattr(self, "_installs", None)
        if installs is not None and not await asyncio.to_thread(installs.stop):
            self.agent.log({"kind": "catalog-install-stop", "state": "failed", "error": "install cleanup still running"})
        await asyncio.to_thread(env_runner.stop)
        if self._catalog_cleanup:
            await asyncio.gather(*tuple(self._catalog_cleanup))
        await asyncio.to_thread(fiji_bindings.WORKER.stop)
        if self.datasets is not None:
            self.datasets.close()  # written out; it stays on, and carries on at the next start
        self.sight.stop()
        if self.masker is not None:
            self.masker.stop()
        if self.molmo is not None:
            self.molmo.stop()
        self.enrich.stop()
        if self.agent.dust is not None:
            self.agent.dust.stop()
        if self.hub.follower is not None:
            self.hub.follower.stop()
        if self.hub.loop_lag is not None:
            self.hub.loop_lag.stop()
        self.research.stop()
        if getattr(self, "registrations", None) is not None:
            await asyncio.to_thread(self.registrations.stop)  # its jobs ended, recorded interrupted
        await self.hub.stop()
        from .looplag import LINES

        await asyncio.to_thread(LINES.flush)  # every log line handed in is in its file before stop returns

    # --- the session ----------------------------------------------------------------------
    def _session_event(self, event: dict) -> None:
        """Conversations opened or resumed: the studio shows them, and the voice is told which
        conversation it is in now (its recap), so nothing from the other one leaks in. Another session
        (a new one, or one switched to: ADR 0017) also clears the live view of what belonged to the one left."""
        kind = event.get("type")
        if kind == "session-opened":
            # Another session opened by the user (new or gone back to) is not looked at until the studio has put its
            # source on stage (commands.py `source`, or `view {held: false}`): until then the stage still shows what the
            # last session showed (live 2026-10-12 18:20:53: a look at it named that session's specimen in this one).
            # A start is looked at at once, and so is a session opened with no studio there to land it.
            self.hold_until_landed(event.get("why") in ("new", "switch") and bool(self.hub.clients))
            # Nothing is on stage for it until the studio lands it on a source (commands.py `source`): the objective
            # menu's scale, and no stage line.
            self.hub.stage_meta = None
            self.hub.stage_image_json = None
            if getattr(self.hub, "scale", None) is not None and self.hub.scale.asset is not None:
                self.hub.scale.use_asset(None)
            if self.datasets is not None:  # a dataset being collected goes on, across sessions: the change is in it
                self.datasets.record("dataset", {"event": "session-opened", "session": event.get("session"),
                                                 "previous": event.get("previous"), "why": event.get("why")})
            # A session gone back to shows its cells' layer again once its landing puts their picture on stage.
            self._reopen_layer = self.day.layer_shown if event.get("why") == "switch" else None
            self._clear_view()
            # This is lifecycle data; Dialogue.marker still owns only log export. Which session opened, the one left
            # and why (new, switch) let the studio choose what the view shows: a new session the live camera, a
            # returning one what it showed last (the studio's own choice: the orchestrator never knows what the feed is).
            cleared = {"type": "session-cleared", "session": event.get("session"), "previous": event.get("previous"),
                       "why": event.get("why")}
            self.hub.broadcast(cleared)
            self.dialogue.marker(dict(cleared))
            for wire in self.experiments_wire():  # the Project opened's experiments, as its cards
                self.hub.broadcast(wire)
            if event.get("why") == "switch":
                self.say_resumed()
        if kind == "quiz-outcome":
            self.presenter.outcome_changed(event.get("rid"))  # the lesson's tally, in the studio
        if kind in ("thread-opened", "thread-switched", "thread-renamed", "session-opened"):
            self.hub.broadcast(self.day.wire())
            if kind != "thread-renamed":
                self.hub.broadcast(self.dialogue.history())
                if self.hub.execution.enabled:
                    self.hub.broadcast_execution(self.execution_wire())
                voice = self.brain.voice
                if voice is not None and not voice.closed and self.day.local_only:
                    asyncio.ensure_future(voice.close())
                elif voice is not None and not voice.closed:
                    asyncio.ensure_future(voice.inject(self.brain.voice_recap(), respond=False, source="recap",
                                                       facts={"why": kind}))
                    # The one-brain voice's Project brief follows at once (voice/partner.py project_changed).
                    changed = getattr(voice, "project_changed", None)
                    if kind == "session-opened" and asyncio.iscoroutinefunction(changed):
                        asyncio.ensure_future(changed())
        # The sessions list (the header chip's menu and the sessions rail): at once when a session opens or is named,
        # and, when what the open session holds changes (a product, a source, a researcher, a conversation), at most
        # every 2 s.
        if kind == "session-renamed":
            self.hub.broadcast(self.subjects.wire())
            self.sessions_changed(at_once=True)
        elif kind == "session-opened":
            self.sessions_changed(at_once=True)
        elif kind in ("research-linked", "thread-opened") or (kind in ("made", "evidence-saved", "tracks-saved", "result")
                                                              and product_of(event) is not None) or (
                kind == "source" and event.get("op") in ("opened", "removed", "restored")):
            self.sessions_changed()

    def _clear_view(self) -> None:
        self.manual_commands.retire("another session was opened")
        scene = self.hub.scene
        # Requests of the session left change nothing on the screen of the one opened (ticket.py, ADR 0010);
        # what they found stays filed with the session they were made in.
        scene.generations.bump("session", "outline:user", "outline:app", "cell", "part", "magnifier", "track", "motion",
                               why="another session was opened")
        request = current()
        if request is not None:
            # The request that opened it ("go back to the leaf session: what did we conclude?") goes on in the session
            # it opened: its answer is written there (brain/loop.py). What it changed on the old screen stays retired.
            for ticket in getattr(request, "members", [request]):
                ticket.renew("session")
        self.services.presentations.reset_all()
        for oid in [o.id for o in scene.objects.values() if o.trial is None]:
            scene.remove(oid)
        self.hub.layer.hide()  # the population of the session left stays filed with it, not shown
        from .motiontracks import track_layer_of

        track_layer_of(self.hub).hide()  # and its motion tracks too
        self.magnifier.close()
        self.presenter.reset_all()
        from .held import held_of

        held_of(self.hub).clear()  # footage held for the researchers of the session left
        from .showing import showings_of

        if showings_of(self.hub).current is not None and showings_of(self.hub).current.active:
            # A showing is stopped as by "stop": what it dimmed or hid is put back (the view is cleared besides).
            import asyncio

            from .commands import run_tool

            async def stop_for_the_session() -> None:
                result = await run_tool(self, "show_me", {"stop": True, "why": "another session was opened"})
                if result.get("summary"):  # said, as Stop's own summary is
                    self.hub.broadcast({"type": "hint", "text": f"Another session: {result['summary']}"})

            try:
                asyncio.get_running_loop().create_task(stop_for_the_session())
            except RuntimeError:  # no loop (a test): nothing on screen to put back
                showings_of(self.hub).ended()
        if self.slidemap is not None:
            self.slidemap.clear()
        self.enrich.entries.clear()
        self.subjects.reset()
        if self.seen is not None:
            self.seen.reset_run()  # read again; what was seen in any session stays (ADR 0017: seen-before is yours)
        self.brain.voice_notes.clear()  # what the chat said before is not news for the next Talk
        self.hub.broadcast(self.research_list())
        self.hub.broadcast(self.enrich.wire_full())

    def new_session(self, name: str = "", by: str = "user", *, local_only: bool = False, guide: str | None = None) -> dict:
        """A new session, only when the user asks (the header, the sessions rail, voice or chat; ADR 0017): it
        opens with one empty conversation and the live view cleared ("session-opened"). Nothing moves or is
        archived: the session left stays as it was, and a researcher still working there finishes in it."""
        previous = self.day.sid
        sid = self.day.new(name, by, local_only=local_only, guide=guide)
        return {"session": sid, "previous": previous}

    def switch_session(self, sid: str) -> dict:
        """Open another session (KeyError when there is none such): its conversations, memory and work come back;
        its live outlines do not (the view starts fresh, said). Its cells' layer comes back only on the picture they were
        found on, once the studio lands it there (`reopen_layer`)."""
        previous = self.day.sid
        self.day.switch_session(sid)
        return {"session": self.day.sid, "previous": previous}

    def say_resumed(self) -> None:
        """One line when a session with history is loaded (opened from the list, or the one a restart comes back
        to): its live outlines are not restored (ADR 0017), and the app says so where the studio shows it."""
        day = self.day
        if not any(t.turns or t.lines for t in day.threads.values()):
            return  # nothing of it to come back to
        info = day.catalogue.get(day.sid)
        last = info.last_active if info is not None and info.last_active else day.created
        self.dialogue.notice(producer={"kind": "session", "id": day.sid}, event="resumed",
                             summary=f"Back in '{day.label}' (last used {last_used_words(last, day.clock())}). Its "
                                     "outlines are in its pictures; the live view starts fresh.",
                             facts={"liveOutlines": False, "lastUsed": round(last, 3)}, origin={"day": day.sid})

    def rename_session(self, sid: str | None, name: str, by: str = "user") -> bool:
        """Name a session: the one open now through its store (its listeners say it), another in its own journal
        (`elsewhere`: nothing of it is told to this one's listeners, so the list is sent here). KeyError when there is
        no such session; False when nothing changed."""
        store = self.day.elsewhere(sid or self.day.sid)
        done = store.rename_session(name, by=by)
        if done and store is not self.day:
            self.sessions_changed(at_once=True)
        return done

    def keep_session(self) -> None:
        """Keep working here: the app's suggestion of a new session is dismissed (journaled, so it is not made again
        for that specimen: day/subject.py), and the studio's dot and row go."""
        suggested = (self.day.suggestion or {}).get("specimen") or ""
        self.day.keep_session(specimen=str(suggested or self.hub.scene.context.get("specimen") or ""))
        self.subjects.publish(self.subjects.wire())

    # --- the sessions list (the header chip's menu and the sessions rail) ---------------------
    def working_by_session(self) -> dict[str, int]:
        """Researchers working now, by the session they were asked in (its id)."""
        counted = getattr(self.research, "working_by_session", None)
        if callable(counted):
            return dict(counted())
        from .research.manager import ACTIVE

        out: dict[str, int] = {}
        for session in list(self.research.sessions.values()):
            if session.purpose == "research" and session.status in ACTIVE and session.sitting:
                out[session.sitting] = out.get(session.sitting, 0) + 1
        return out

    def material_input_adjustments(self, infos) -> dict:
        """Off-loop canonical inventory projection, scanning each backing store once."""
        from .project_material import additional_inputs, attach_usage
        index = getattr(self.hub, "resources", None)
        if index is None or index.projects is None:
            return {}
        groups = index.projects.scan_sessions(info.id for info in infos)
        result = {}
        for info in infos:
            saved, problems = groups[info.id]
            entries = [index.project_entry(item) for item in saved]
            unreadable = [*problems, *attach_usage(entries, index.projects)]
            result[info.id] = {'additional': additional_inputs(entries, info),
                               'incomplete': bool(unreadable)}
        return result

    def sessions_wire(self, infos=None, working: dict | None = None, inputs: dict | None = None) -> dict:
        """Every session, the most recently used first (protocol.ts SessionsList): each the catalogue's own row
        (day/catalogue.py: what Made in this session lists, its researchers, its conversations) with the researchers
        working in it now. Its inputs use the shared material selections, including Study assignments and pinned
        dependencies, plus journal sources not already represented by an exact retained input. Reads off the
        event loop (`sessions_message`)."""
        infos = self.day.catalogue.list() if infos is None else infos
        working = self.working_by_session() if working is None else working
        inputs = self.material_input_adjustments(infos) if inputs is None else inputs
        rows = []
        for info in infos:
            row = {**info.wire(), "working": int(working.get(info.id, 0))}
            if info.id == self.day.sid:  # the open session's, as its own wire counts it (held footage too)
                row["counts"] = self.subjects.counts(info)
            if inputs.get(info.id):
                row["counts"] = {**row["counts"], "sources": row["counts"].get("sources", 0) + inputs[info.id]['additional']}
                if inputs[info.id]['incomplete']:
                    row["counts"]['sourcesIncomplete'] = True
            rows.append(row)
        # Where every Project is saved on this Mac, said with ~ (thesis question 12: the Projects page and Help say it).
        root = str(self.paths.sessions).replace(str(Path.home()), "~", 1)
        return {"type": "sessions", "current": self.day.sid, "sessions": rows, "root": root}

    async def sessions_message(self) -> dict:
        """The sessions list, its journals and scientific inputs read in a thread."""
        working = self.working_by_session()
        def read():
            infos = self.day.catalogue.list()
            return infos, self.material_input_adjustments(infos)
        infos, inputs = await asyncio.to_thread(read)
        return self.sessions_wire(infos, working, inputs)

    def hold_until_landed(self, held: bool, expires: bool = True) -> None:
        """The view held (no looks, nothing taken as the subject) until a source lands, or let go. A session's opening
        holds it for at most LAND_S, so a studio that never lands (closed mid-switch) never stops the app looking; the
        studio's own hold (a new session's home: `expires` false) lasts until the studio lets go."""
        self.hub.view_held = held
        if self._landing is not None:
            self._landing.cancel()
            self._landing = None
        if held and expires:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._landing = loop.call_later(LAND_S, lambda: self.hold_until_landed(False))
        elif getattr(self, "sight", None) is not None:
            self.sight.recheck = True  # seen again: the running look looks soon

    # --- a session's sources (sources.py, library.py; ADR 0022) ----------------------------------------------------
    async def stage_changed(self, asset=None, fields: set | None = None) -> None:
        """A source put on stage (commands.py `source`): the asset's own magnification is the scale (none when it has
        none), its user-given specimen is the subject at once (else the next look names it), and the Board's stage line
        says what it is (`hub.stage_meta`). None: the live camera, the objective menu's scale. `fields`: only these of
        the asset on stage changed (asset-meta): the subject is said again only when its specimen did."""
        hub = self.hub
        meta = asset.metadata() if asset is not None else None
        previous = getattr(hub, "stage_meta", None)
        if ((previous or {}).get("id"), (previous or {}).get("retained")) != ((meta or {}).get("id"), (meta or {}).get("retained")):
            from . import ticket
            from .population import layer_of

            ticket.generations(hub).bump("cell", why="the source on stage changed", by=ticket.current())
            layer_of(hub).hide()
        hub.stage_meta = meta
        from .source_images import descriptor

        hub.stage_image_json = descriptor(asset, self.day.sid)
        if fields is None:
            self.reopen_layer(asset)
        if hub.scale is not None:
            hub.scale.use_asset(asset.id if asset is not None else None,
                                asset.scale_optics() if asset is not None else None,
                                read_only=bool((meta or {}).get('retained')))
        if fields is None or "specimen" in fields:
            if fields is None:
                self.subjects.source_changed(asset.user_specimen() if asset is not None else "")
            elif asset is not None and asset.user_specimen():
                self.subjects.name(asset.user_specimen())
        self.broadcast_sources(await self.session_sources(self.day.sid))

    def reopen_layer(self, asset) -> None:
        """The first source put on stage in a session opened again (switched back to, or the one a restart comes back
        to): the population its cells' layer showed when it was left is shown again, as Data's Show on picture shows one,
        when this is the picture its cells were found on (their `sourceImage`). The live camera and any other source
        start fresh (ADR 0010: nothing is drawn on a view it was not found or checked on). Off the landing: its retained
        files are checked in a thread, and the stage is never kept waiting."""
        eid, self._reopen_layer = self._reopen_layer, None
        if not eid or asset is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        import contextvars

        # The app's own showing, not the landing request's: no request's ticket governs it.
        self._reopening = loop.create_task(self._show_layer_again(eid), context=contextvars.Context())

    async def _show_layer_again(self, eid: str) -> bool:
        from . import ticket
        from .apply import apply
        from .brain.registry import ToolFailed
        from .evidence import evidence_of
        from .population import drawn_on_stage, layer_of

        hub, sid = self.hub, self.day.sid
        evidence, layer, generations = evidence_of(hub), layer_of(hub), ticket.generations(hub)
        population = evidence.get(eid)
        if population is None:
            return False
        issued = (generations.value("cell"), generations.value("session"))

        def due() -> bool:
            # Still this session, its cells untouched since (no clear, no other source), nothing else shown, and the
            # picture on stage the one they were found on.
            return (self.day.sid == sid and (generations.value("cell"), generations.value("session")) == issued
                    and layer.shown is None and bool(population.view.get("sourceImage")) and bool(population.live())
                    and drawn_on_stage(hub, population))

        if not due():
            return False
        try:
            await asyncio.to_thread(evidence.require_retained, population)
        except ToolFailed:
            return False  # its files are missing or changed: it stays in Data, which says so when it is opened
        if not due():
            return False
        return (await apply(hub, lambda _: layer.show(population), scope="cell")).ok

    async def session_sources(self, sid: str) -> dict:
        """Session `sid`'s sources (sources.rows): the open session's from its store, another's from its journal, each
        with the other sessions that use it (every session's journal, read by the catalogue)."""
        from . import sources

        locations = getattr(self.day, 'locations', None)
        portable = await asyncio.to_thread(locations.project, sid) if locations is not None else None
        if portable is not None:
            from .project_stage_media import sources as retained_sources
            def read_retained():
                record = next((i for i in self.day.catalogue.list() if i.id == sid), None)
                if record is None:
                    return {"session": sid, "onStage": None, "sources": [], "removed": []}
                return retained_sources(self.hub.resources.projects, sid, record)
            return await asyncio.to_thread(read_retained)

        library = getattr(self.hub, "library", None)
        if library is None:
            return {"session": sid, "onStage": None, "sources": [], "removed": []}
        assets, infos = await asyncio.to_thread(lambda: (library.assets(), self.day.catalogue.list()))
        record = self.day if sid == self.day.sid else next((i for i in infos if i.id == sid), None)
        if record is None:
            return {"session": sid, "onStage": None, "sources": [], "removed": []}
        return sources.rows(sid, assets, record, infos)

    def broadcast_sources(self, listed: dict) -> None:
        """Every studio is told the open session's sources (the Sources section)."""
        if listed.get("session") == self.day.sid:
            self.hub.broadcast({"type": "sources", **listed})

    def sessions_changed(self, at_once: bool = False) -> None:
        """The sessions list changed: every studio is sent it with the open session's own wire (its counts), at once
        (a session opened or named, a researcher started or finished) or at most every SESSIONS_EVERY_S (what the open
        session holds). Nothing without a running loop: a studio that connects reads it then."""
        if self._stopping:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is not None and not self._loop.is_closed():  # filed from a thread: said on the app's loop
                self._loop.call_soon_threadsafe(lambda: self.sessions_changed(at_once))
            return
        if self._sessions_timer is not None:
            if not at_once:
                return  # one is due; it reads what is there then
            self._sessions_timer.cancel()
        wait = 0.0 if at_once else max(0.0, self._sessions_sent + SESSIONS_EVERY_S - loop.time())
        self._sessions_timer = loop.call_later(wait, self._send_sessions)

    def _send_sessions(self) -> None:
        self._sessions_timer = None
        if self._stopping:
            return
        self._sessions_sent = asyncio.get_running_loop().time()
        task = asyncio.ensure_future(self._broadcast_sessions())
        self._sessions_sending.add(task)
        task.add_done_callback(self._sessions_sending.discard)

    async def _broadcast_sessions(self) -> None:
        try:
            message = await self.sessions_message()
        except Exception as exc:  # the list is not worth a failure: the next change or connect sends it
            self.agent.log({"kind": "sessions-list", "error": f"{type(exc).__name__}: {exc}"[:300]})
            return
        self.hub.broadcast(self.subjects.wire())
        self.hub.broadcast(message)

    def _research_changed(self, session) -> None:
        """A researcher's change: a start or a finish changes which sessions have work going on (the rail's dot)."""
        if getattr(session, "purpose", "") != "research":
            return
        from .research.manager import ACTIVE

        working = session.status in ACTIVE
        if self._research_working.get(session.rid) != working:
            seen = session.rid in self._research_working
            self._research_working[session.rid] = working
            if seen or working:
                self.sessions_changed(at_once=True)

    # --- what a studio is sent --------------------------------------------------------------
    def settings_wire(self) -> dict:
        """The settings' values, for what draws with them (outline style, the voice link). What can be
        chosen, and why not, is only in the capability list (capabilities_wire): the menus draw that."""
        return {"type": "settings", "settings": self.hub.settings.to_wire()}

    async def put_on_stage(self, aid: str) -> dict:
        """One of the user's assets on stage, as the studio's Add does (the `source` command, held at its start): it is
        the session's from then on (ADR 0022's amendment). The library's add_asset tool."""
        from .commands import source, source_retain

        sid = self.day.sid
        kept = await source_retain(self, None, {'asset': aid, 'session': sid})
        return await source(self, None, {"open": aid, "paused": True, 'session': sid,
                                        **({'retained': kept['retained']} if kept.get('retained') else {})})

    async def open_page(self, client, page: str, timeout: float = 4.0) -> dict | None:
        """Ask the studio that made the request (every studio when none did) to open a page ("#models/<id>") and wait for
        it to say it shows it (`page-opened`): {"visible": False} when that studio is in the background. None: no
        studio answered in time."""
        import json
        import uuid

        pid = uuid.uuid4().hex[:12]
        future = asyncio.get_running_loop().create_future()
        self._pages[pid] = future
        message = json.dumps({"type": "open-page", "id": pid, "page": page})
        targets = [client] if client is not None and client in self.hub.clients else list(self.hub.clients)
        for target in targets:
            target.offer(message)
        try:
            return await asyncio.wait_for(future, timeout) if targets else None
        except asyncio.TimeoutError:
            return None
        finally:
            self._pages.pop(pid, None)

    def page_opened(self, pid: str, reply: dict) -> None:
        future = self._pages.get(pid)
        if future is not None and not future.done():
            future.set_result(reply)

    @property
    def observer(self):
        """What the app asks the outside world (observed.py): a gated model's access, whether a key works; when an answer
        changes, every studio is sent the Models page again."""
        if getattr(self, "_observer", None) is None:
            from .observed import Observer

            self._observer = Observer(changed=self.catalog_changed, allowed=lambda: not self.day.local_only)
        return self._observer

    async def observe(self, model: str | None = None, *, stale: float | None = None, timeout: float = 8.0) -> None:
        """Ask again now: one model's outside requirements, or everything older than `stale` (or due). Waits at most
        `timeout`; the rest is told when it comes."""
        observer = self.observer
        only = observer.for_model(model) if model else None
        task = asyncio.ensure_future(observer.sweep(only=only, force=bool(model), stale=stale))
        self._tasks.append(task)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            pass

    @property
    def downloads(self):
        """The models folder's downloads (downloads.py): one at a time, in a thread; each change sends the studio the
        capabilities wire again (on the app's loop), and a download waits while a click is being answered."""
        if getattr(self, "_downloads", None) is None:
            from .downloads import Downloads

            def changed() -> None:
                # A tick that only moved bytes on (every row in the state it was) is progress (request_catalog_changed).
                states = tuple((r.get("model"), r.get("state")) for r in self._downloads.wire())
                with self._catalog_lock:
                    progress, self._catalog_asked["states"] = states == self._catalog_asked["states"], states
                self.request_catalog_changed(progress=progress)

            gpu = getattr(self.hub, "gpu", None)
            self._downloads = Downloads(self.hub, changed=changed,
                                        busy=lambda: gpu is not None and gpu.clicks_waiting())
        return self._downloads

    @property
    def maintainer(self):
        """Capability maintenance (maintenance/worker.py): a thread starting one low-priority attempt process at a time
        for entries whose recorded policy is review or auto; each step it takes is a catalog change."""
        if getattr(self, "_maintainer", None) is None:
            from .maintenance.worker import Maintainer

            self._maintainer = Maintainer(changed=self.request_catalog_changed,
                                          allowed=lambda: "this session is local only" if self.day.local_only else "")
        return self._maintainer

    @property
    def installs(self):
        """The environments the app makes (envs.py): one at a time, in a thread at background priority; each change
        is a catalog change (kept choices that can run again apply; every studio gets the capabilities again)."""
        if getattr(self, "_installs", None) is None:
            from .envs import Installs

            self._installs = Installs(self.hub, changed=self.request_catalog_changed)
        return self._installs

    @property
    def key_admin(self):
        """Adding, replacing and removing keys from the studio (key_admin.py)."""
        if self._key_admin is None:
            from .key_admin import KeyAdmin

            self._key_admin = KeyAdmin(self)
        return self._key_admin

    def environment_display(self):
        from contextlib import nullcontext
        from . import env_observations

        observer = self._environment_observer
        # Synchronous CLI/test construction predates start; live App.start installs the off-loop
        # observer before accepting traffic. Execution never enters this display scope.
        return env_observations.display(observer.snapshot) if observer is not None else nullcontext()

    def capabilities_wire(self, counts: bool = True) -> dict:
        """Everything the app can do, who does it, what powers it, whether it can run here and the
        user's verdicts (capabilities.py): the Toolbox draws it (with `counts`: the Keys band's "+N things it can do"), the agent
        reads it (without: they cost a pass per missing key)."""
        from . import readiness

        # Only raw environment facts are retained. Current settings, keys, queues, checks and
        # readiness are resolved on the loop for every response.
        with self.environment_display(), readiness.batch():
            wire = self._capabilities_wire(counts)
        if counts:
            self._capabilities_sent = wire  # what a download's progress tick renews (catalog_progress)
        return wire

    def capability_status(self) -> dict:
        """What the agent's awareness reads (brain/context._awareness): the capability rows of `capabilities_wire`, made
        by the same resolver fresh on every read (keys, checks, readiness, installed identity), without the Models
        page's setup, rows, news, shelf and drafts, which only the Capabilities page and the catalog tools read."""
        from . import readiness

        with self.environment_display(), readiness.batch():
            return {"capabilities": self._capability_rows(counts=False)["capabilities"]}

    def _capability_rows(self, counts: bool) -> dict:
        from . import capabilities

        verdicts = self.verdicts
        checks = getattr(self.hub, "checks", None)
        keys = load_keys(self.paths.keys_file)
        wire = capabilities.wire(self.hub.settings, keys, self.brain.registry,
                                 verdicts.folded() if verdicts is not None else {},
                                 preferences={"objective": getattr(self.hub.scale, "objective", None)},
                                 pending=len(verdicts.pending()) if verdicts is not None else 0,
                                 version=verdicts.version if verdicts is not None else "",
                                 problems=self.worker_problems(),
                                 checks=checks.latest() if checks is not None else None,
                                 running=checks.running if checks is not None else None, counts=counts)
        return wire

    def _capabilities_wire(self, counts: bool) -> dict:
        keys = load_keys(self.paths.keys_file)
        wire = self._capability_rows(counts)
        wire["setup"]["keys"] = self.key_admin.facts()  # where each key comes from and its last test; never a value
        wire["setup"]["downloads"] = self.downloads.wire()  # each download this run: its state, bytes, and why it failed
        from . import downloads as downloads_

        wire["setup"]["disk"] = downloads_.disk()  # the models folder's free space: Turn on's sheet says whether it fits
        wire["setup"]["turning"] = self.turn_on.wire()
        wire["setup"]["installs"] = self.installs.wire()  # each environment made this run (envs.py)
        from .maintenance import status as maintenance

        wire["setup"]["maintenance"] = maintenance.wire()  # each maintained entry's update state (ticket 59)
        from . import channels

        wire["setup"]["channels"] = channels.wire()  # where entries come from besides the app's own
        if getattr(self, "channel_update", None):
            wire["setup"]["channelUpdate"] = self.channel_update  # the last Check for catalog updates
        from . import catalog_view

        # The Models page: every catalog entry, why it is on or off as facts with their references, what it is used
        # for and its group (catalog_view.py); entries that can't be read, apart.
        wire["models"] = catalog_view.rows(wire["roles"], wire["setup"]["keys"], keys, wire["setup"]["downloads"])
        observer = self.observer
        if observer.checking:  # what is being asked of the outside world right now: its card says "checking…"
            for row in wire["models"]:
                row["checking"] = any(f"{k}:{t}" in observer.checking for k, t in observer.for_model(row["id"]))
        wire["setup"]["catalogProblems"] = catalog_view.problems()
        from gpu_worker import manifests as catalog

        # Where your own entries live on this instance (MICROMIND_CATALOG / MICROMIND_ASSETS move it): Add your own says it.
        wire["setup"]["mineFolder"] = catalog_view.home_path(catalog.mine_root())
        from . import catalog_news

        # New for you (C1): what arrived in the catalog that this Mac hasn't known, offered, never turned on by itself.
        ready = [r["id"] for r in wire["models"] if r.get("status") == "ready"]
        wire["setup"]["news"] = {"new": catalog_news.new(about=self.news_about(), ready=ready),
                                 "last": catalog_news.last_check(), "automatic": catalog_news.automatic()}
        from . import capability_shelf

        # What it can do, by job (C2): one card each, its colour, its options and the one press that turns it on.
        wire["setup"]["shelf"] = capability_shelf.cards(wire["roles"], wire["setup"]["downloads"],
                                                        wire["setup"]["installs"], self.turn_on.wire(),
                                                        capabilities=wire["capabilities"],
                                                        outcomes=getattr(self, '_shelf_observations', {}))
        from . import drafts

        wire["setup"]["drafts"] = drafts.wire()
        from . import own_trials
        wire["setup"]["ownTrials"] = own_trials.wire()
        from . import mine

        wire["setup"]["loose"] = mine.loose()  # files dropped straight into your folder, offered to be made entries
        return wire

    def worker_problems(self) -> dict[str, str]:
        """Roles whose chosen implementation's worker is hung (contract 5): unavailable, with why."""
        from .operations import OPERATIONS, worker_client

        out = {}
        for op in OPERATIONS.values():
            if op.selected_by is None:
                continue
            chosen = getattr(self.hub.settings, op.selected_by, None)
            impl = next((i for i in op.implementations if i.option == chosen), None)
            client = worker_client(self.hub, impl.worker, start=False) if impl is not None else None
            problem = getattr(client, "problem", lambda: "")() if client is not None else ""
            if problem:
                out[op.selected_by] = problem
        return out

    def publish_settings(self) -> None:
        """Settings changed: every studio gets them, and the capabilities they change (what is off); Activity says what
        changed and who changed it."""
        self.hub.broadcast(self.settings_wire())
        self.hub.broadcast(self.capabilities_wire())
        sink = activity.sink(self.hub)
        if sink is not None:
            sink.settings_published()

    def research_list(self) -> dict:
        return {"type": "research-list", **self.research.list_wire()}

    def snapshot(self, client=None) -> list[dict]:
        """Everything a studio that (re)connects is sent, in order: it then shows exactly the
        app's state, the active conversation as it was shown last."""
        info = self.hub.latest_info
        radius = info.fov.radius if info is not None and info.fov is not None else None
        return [{"type": "hello", "health": self.hub.health()}, self.settings_wire(), self.capabilities_wire(),
                self.hub.scale.wire(radius),
                self.hub.coach.wire(),
                {"type": "arena", **self.arena.state()}, {"type": "scene", **self.hub.scene.to_wire()},
                *self.layer_wires(),
                self.magnifier.wire(), self.enrich.wire_full(), (self.presenter.for_client(client) if client is not None else self.presenter).wire(),
                *([self.slidemap.wire()] if self.slidemap is not None else []), self.subjects.wire(),
                *([self.datasets.wire()] if self.datasets is not None else []),
                *([self.rig.wire()] if self.rig is not None else []),
                self.research_list(), *self.experiments_wire(), self.day.wire(), self.dialogue.history(),
                self.execution_wire()]

    def experiments_wire(self) -> list[dict]:
        """The open Project's experiments, as cards (experiments.py); none when there are none."""
        experiments = getattr(self.hub, "experiments", None)
        if experiments is None:
            return []
        try:
            wire = experiments.wire_list(self.day.sid)
        except Exception:  # noqa: BLE001 - a store that cannot be read never fails a studio's connect
            return []
        return [wire] if wire["experiments"] else []

    def layer_wires(self) -> list[dict]:
        """The population and the motion tracks shown over the live view (if any) and their layers' state, as
        a studio that (re)connects needs them."""
        layer = self.hub.layer
        population = layer.population()
        tracks = self.hub.track_layer
        item = tracks.item()
        return ([population.wire(layer.states())] if population is not None else []) + [layer.wire()] + \
            ([tracks.item_wire or self.hub.tracks_store.wire(item)] if item is not None else []) + [tracks.wire()]

    def execution_wire(self) -> dict:
        return {"type": "execution-list", "enabled": self.hub.execution.enabled, "generation": self.day.generation,
                "entries": self.hub.execution.references(generation=self.day.generation)}

    @property
    def keys_file(self) -> Path:
        return self.paths.keys_file
