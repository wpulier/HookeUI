"""The studio's commands (JSON over /ws), one handler each.

A handler gets the app, the client and the command, and returns what goes into the reply
({"type": "result", "for": kind, "requestId": ...}); a ToolError is the user's error message.
What the brain can also do (start a conversation, go back to one) runs the brain's own tool,
so a button and a spoken request do exactly the same thing.

  ask, click            a question; a click (or a dragged box) outlines (blind A/B/C trials while arena is on)
  settings, arena, scores, vote
  cells, clear, remove, select, focus, card-open, reveal, debug, timing, scale
  magnifier             the studio's dots, zoom, level, close, a researcher's picture
  presenter             a researcher's work beside the view: show, close
  slidemap              the slide map panel: open, close (the brain's show_map)
  session               the header chip's menu and the sessions rail: new, switch, rename, keep (ADR 0017)
  thread                the conversation menu: new, switch, rename
  dataset               Record's dataset: on with Record, off with its Stop (only the user; ADR 0009)
  dataset-rename, dataset-reveal, dataset-trash   the Datasets tab: name one, show it in the Finder, trash it
  rig                   the Datasets tab's microscope facts (a field number given is the scale's too)
  accept-correction, dismiss-correction, pin-note
  research-followup, research-cancel
  action, action-cancel the Actions panel: run one of the agent's capabilities by hand; Stop that run
  tool                  run one brain tool directly (tests and the scenario harness)
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import contextlib
from dataclasses import dataclass, field
import json
import math
import time
from typing import Awaitable, Callable

from . import activity, tools, execution, user_stop
from .arena import scoreboard
from .day import sid as sids
from .brain.registry import ToolContext, ToolFailed
from .hub import Client
from . import learner
from .labels import label_key
from .research import ResearchRefused
from .scale import area_px

Handler = Callable[["App", Client, dict], Awaitable[dict]]  # noqa: F821 (app.App)
COMMANDS: dict[str, Handler] = {}

MIN_BOX_PX = 2.0  # frame px: a dragged box narrower than this has nothing inside to outline


@dataclass(eq=False)
class _ManualCommand:
    body: str
    cursor: "_ManualCursor"
    correlation: str
    sequence: int | None
    day_generation: object
    issued_session: int
    workspace_generation: int
    ticket: object = None
    context: object = None
    driver: object = None
    run: object = None
    revoked: str | None = None
    error: str | None = None
    settled: bool = False
    capture_parent: str | None = None
    runs: tuple = ()
    reply_for: str = "present"


@dataclass(eq=False)
class _ManualCursor:
    socket: object
    profile: object
    highest: int = 0
    expired: int = 0
    revoked: bool = False
    entries: OrderedDict = field(default_factory=OrderedDict)


class NativeManualCommands:
    """Original Registry work owned by App; bounded transport replay, not a proof store.

    Canonical cursors occupy the core's bounded workspace slots. Only the current
    server incarnation is retained there; a trusted adapter must issue a fresh
    epoch when reconnecting. Equal-epoch reacceptance keeps its revoked cursor.
    Legacy random correlations have a separate, explicit App-lifetime limit.
    """
    WINDOW, TOTAL, PENDING, PENDING_TOTAL, BODY_BYTES = 32, 256, 4, 32, 65536
    # The accepted observation has its own compact 64 KiB contract. A request
    # pin adds bounded IDs/timing around it; JSON whitespace is not payload.
    PIN_BYTES = 65536 + 2048

    def __init__(self, app):
        self.app = app
        self.cursors = {}
        self.legacy = {}  # at most TOTAL admitted keys, including retired keys
        self.pending = {}  # at most PENDING_TOTAL; actual Ticket -> original work

    def _discard_retired(self, cursor):
        if not cursor.revoked:
            return
        # H/F survive retirement; only genuine unfinished originals need bodies.
        pending = {id(entry) for entry in self.pending.values() if entry.cursor is cursor}
        for sequence, entry in tuple(cursor.entries.items()):
            if id(entry) not in pending:
                cursor.entries.pop(sequence)

    def _canonical_count(self):
        # Temporary identity sets read the existing owners, never another ledger.
        cursors = {id(cursor): cursor for cursor in self.cursors.values()}
        for entry in self.pending.values():
            cursors[id(entry.cursor)] = entry.cursor
        entries = {id(entry) for cursor in cursors.values() for entry in cursor.entries.values()}
        entries.update(id(entry) for entry in self.pending.values() if entry.sequence is not None)
        return len(entries)

    def accepted(self, socket, profile):
        ws = profile.ref.workspace_id
        previous = self.cursors.get(ws)
        if previous is not None and previous.profile.ref == profile.ref:
            previous.revoked = True
            self._discard_retired(previous)
            return  # never reset H/F for an identically reaccepted wire epoch
        if previous is not None:
            self.gone(previous.socket)
        self.cursors[ws] = _ManualCursor(socket, profile)

    def gone(self, socket):
        for cursor in self.cursors.values():
            if cursor.socket is socket:
                cursor.revoked = True
                self._discard_retired(cursor)
        for entry in tuple(self.pending.values()):
            if entry.cursor.socket is socket:
                entry.cursor.revoked = True
                self._discard_retired(entry.cursor)  # includes an older pending incarnation
                entry.revoked = "the original workspace connection was retired"
                entry.ticket.cancel(entry.revoked)
        for key, entry in tuple(self.legacy.items()):
            if entry is not None and entry.settled and entry.cursor.socket is socket:
                self.legacy[key] = None  # bounded retirement marker, no result/socket/pin

    def retire(self, why):
        # Day/reset revokes originals, not the accepted stream's H/F namespace.
        for entry in tuple(self.pending.values()):
            entry.revoked = why
            entry.ticket.cancel(why)
        for key, entry in tuple(self.legacy.items()):
            if entry is not None and entry.settled:
                self.legacy[key] = None

    def _origin(self, socket, supplied, run):
        from .capture import RUN
        from .workspace_observation import ObservationError
        try:
            profile = self.app.hub.screen._profile(socket)
        except TypeError:
            raise ObservationError("CLIENT_MISMATCH", "a client dictionary is not an accepted socket") from None
        if supplied is not None and supplied != profile.ref.wire():
            raise ObservationError("CLIENT_MISMATCH", "manual proposal belongs to another connection")
        if profile.app_run_id != RUN or (run is not None and run != RUN):
            raise ObservationError("RUN_MISMATCH", "manual proposal belongs to another app run")
        cursor = self.cursors.get(profile.ref.workspace_id)
        if cursor is None or cursor.socket is not socket or cursor.profile is not profile or cursor.revoked:
            raise ObservationError("CLIENT_MISMATCH", "manual connection was retired; accept a fresh server epoch")
        return cursor

    def _reason(self, entry):
        from .ticket import generations
        from .presentations import PresentationError
        cursor = entry.cursor
        if entry.revoked:
            return entry.revoked
        if (cursor.revoked or self.app.hub.screen._accepted.get(cursor.socket) is not cursor.profile
                or self.cursors.get(cursor.profile.ref.workspace_id) is not cursor):
            return "the original accepted workspace incarnation was retired"
        if (entry.issued_session != generations(self.app.hub).value("session")
                or not self.app.day.current(entry.day_generation)):
            return "the original manual request belongs to another session"
        try:
            generation = self.app.services.presentations.workspace_generation(self.app._binding_context(cursor.profile.ref))
        except PresentationError as exc:
            return exc.reason
        if generation != entry.workspace_generation:
            return "the workspace reset since the original manual request"
        return None

    def guard(self, ticket, ctx, *, allow_cancelled=False):
        entry = self.pending.get(ticket)
        if entry is None:
            return None  # other callers keep their existing App policy
        why = self._reason(entry)
        if not why and (ticket.closed or ticket.admission is None):
            why = "the original manual request is no longer active"
        if not why and not allow_cancelled:
            why = ticket.superseded("session")
        original = entry.context
        run = next((run for run in original.steps.runs.values()
                    if run.occurrence is not None and run.occurrence.action_id == ctx.owner), None) if original is not None else entry.run
        number = run.step[0] if run is not None and run.step else 1
        if not why and (original is None or original.admission is not ticket.admission
                or ctx.request_id != ticket.admission.request_id or run is None
                or run.occurrence is None or ctx.owner != run.occurrence.action_id
                or original.steps.occurrences.get(number) != run.occurrence):
            why = "the original manual request/action no longer owns this work"
        if why and not allow_cancelled:
            # A core observation reader can swallow PresentationError. This
            # original manual guard must fail the real Registry operation before
            # its completion taps/logs can report success from that receipt.
            if run is not None and run.occurrence is not None:
                run.outcome = {**(run.outcome or {}), "requestId": run.occurrence.request_id,
                    "actionId": run.occurrence.action_id, "status": "failed",
                    "failure": {"code": "REQUEST_CANCELED", "reason": why}}
            raise ToolFailed(why, next="send a new request from the current studio")
        return why

    @staticmethod
    def _envelope(cursor, sequence, **values):
        if sequence is not None:
            values.update(manualSeq=sequence, highestManualSeq=cursor.highest,
                          expiredThroughManualSeq=cursor.expired)
        return values

    def _refused(self, cursor, sequence, code, why, *, not_admitted=False):
        return self._envelope(cursor, sequence, ok=False, code=code, error=why,
                              manualStatus="not-admitted" if not_admitted else "expired",
                              **({"notAdmitted": True} if not_admitted else {}))

    def _project(self, entry):
        run = entry.run
        if run is None and entry.context is not None:
            run = entry.context.steps.runs.get(1)
        if run is None:
            return self._envelope(entry.cursor, entry.sequence, ok=False, error=entry.error or "manual operation could not start")
        runs = entry.runs or (run,)
        error = entry.error
        if entry.reply_for == 'research-open-capture' and not error:
            error = next((r.summary for r in runs if r.ok is False), None)
        return self._envelope(entry.cursor, entry.sequence, ok=entry.error is None and all(r.ok is not False for r in runs),
                              pending=not entry.settled, manualStatus="retained",
                              run=run.wire(), summary=run.summary,
                              **({"steps": [r.wire() for r in runs]} if entry.capture_parent else {}),
                              **({"error": error} if error else {}))

    def _finish(self, entry):
        if entry.settled:
            return
        if entry.context is not None:
            entry.run = entry.context.steps.runs.get(1)
        self.pending.pop(entry.ticket, None)
        entry.settled = True
        # Registry owns outcome/output history. No second success/result copy,
        # observation pin, capture or prepared source is retained for replay.
        entry.context = entry.ticket = entry.driver = None
        self._discard_retired(entry.cursor)
        if entry.sequence is None and self._reason(entry) is not None:
            self.legacy[entry.correlation] = None

    def _check_base(self, cursor, base, observed):
        from .workspace_observation import ObservationError
        if base is None:
            return
        if observed is None:
            raise ObservationError("OBSERVATION_INCOMPLETE", "manual control needs its current observed target")
        rows = {row["id"]: row for row in observed.wire()["items"]}
        for iid, proposed in base.items():
            actual = self.app.services.presentations.get(cursor.profile.ref.workspace_id, iid)
            row = rows.get(iid)
            expected = {"sourceGeneration": actual.source_generation,
                        "lifetimeGeneration": actual.lifetime_generation,
                        "propertyVersions": dict(actual.property_versions)}
            if (proposed != expected or row is None
                    or any(row.get(key) != value for key, value in expected.items())):
                raise ObservationError("TARGET_REVISED", "that exact window changed before the manual request")

    async def replay(self, cursor, key, body, sequence=None):
        """Look up an admitted original before any mutable source resolution."""
        entries = cursor.entries if sequence is not None else self.legacy
        if key not in entries:
            return None
        entry = entries[key]
        if entry is None:
            return self._refused(cursor, sequence, "MANUAL_REVOKED", "that legacy correlation has been retired")
        if entry.cursor is not cursor or entry.body != body:
            return self._refused(cursor, sequence, "MANUAL_REPLAY_CHANGED", "a replay must retain its original connection and complete command body")
        why = self._reason(entry)
        if why:
            return self._refused(cursor, sequence, "MANUAL_REVOKED", why)
        if not entry.settled:
            await asyncio.shield(entry.driver)
        return self._project(entry)

    async def submit(self, socket, message, args, body, sequence, base, *, capture_parent=None,
                     prepared_source_check=None, resolve_capture=None, reply_for="present"):
        from . import ticket as tickets
        from .workspace_observation import ObservationError
        from .presentations import PresentationError
        cursor = self._origin(socket, message.get("client"), message.get("appRunId"))
        when_asked = self.app.brain.board.when_asked()
        key = sequence if sequence is not None else message["requestId"]
        entries = cursor.entries if sequence is not None else self.legacy
        replayed = await self.replay(cursor, key, body, sequence)
        if replayed is not None:
            return replayed
        if sequence is not None:
            if sequence <= cursor.highest:
                return self._refused(cursor, sequence, "MANUAL_EXPIRED", "this sequence is outside the retained manual horizon; it cannot be executed again")
            cursor.highest = sequence  # synchronous intake, even definitive capacity refusal
        pending_origin = sum(entry.cursor is cursor for entry in self.pending.values())
        capacity = len(self.pending) >= self.PENDING_TOTAL or pending_origin >= self.PENDING
        if sequence is None:
            capacity = capacity or len(self.legacy) >= self.TOTAL
        else:
            while len(cursor.entries) >= self.WINDOW:
                oldest = next(iter(cursor.entries.values()))
                if not oldest.settled:
                    capacity = True
                    break
                cursor.entries.popitem(last=False)
                cursor.expired = oldest.sequence
            capacity = capacity or self._canonical_count() >= self.TOTAL
        if capacity:
            return self._refused(cursor, sequence, "MANUAL_CAPACITY", "manual work is at capacity; retry admitted work with its original packet",
                                 not_admitted=True)
        try:
            # Existing Screen ingress owns inline validation. No new HTTP grant
            # or browser field can create the accepted socket/profile authority.
            if "screen" in message:
                self.app.hub.screen.report(message["screen"], client=socket)
            self._check_base(cursor, base, self.app.hub.screen.observation(socket))
            request = tickets.issue(self.app.hub, "command", asked=self.app.day.here(), channel="studio")
            try:
                with tickets.using(request):
                    pin = self.app.hub.screen.pin(socket, request_id=request.admission.request_id,
                        timing="client-send" if "screen" in message else "server-receive-legacy")
                    if len(json.dumps(pin.wire(), allow_nan=False, separators=(",", ":")).encode()) > self.PIN_BYTES:
                        raise ObservationError("INVALID_REPORT", "manual request pin exceeds its bounded allowance")
                    self._check_base(cursor, base, pin.observation)
                    if capture_parent is not None:
                        self._capture_parent(cursor, pin, capture_parent)
            except Exception:
                request.close()
                raise
        except (ObservationError, PresentationError) as exc:
            return self._refused(cursor, sequence, exc.code, str(exc), not_admitted=True)
        if capture_parent is not None:
            source_check = prepared_source_check
            async def check_capture_parent():
                if source_check is not None:
                    await source_check()
                # Layout is optional on phones; original report ownership and
                # lifetime are not. This synchronous check is the last work
                # after preparation, immediately before native admission.
                self._capture_parent(cursor, pin, capture_parent, after_preparation=True)
            prepared_source_check = check_capture_parent
        info = self.app.hub.latest_info
        ctx = ToolContext("studio", 0, info.id if info else None, "Manual workspace control", asked=request.asked,
            actor="you", admission=request.admission, request_id=request.admission.request_id, client=socket,
            workspace_id=cursor.profile.ref.workspace_id, request_snapshot=pin, board=self.app.brain.board,
            when_asked=when_asked, prepared_source_check=prepared_source_check)
        entry = _ManualCommand(body, cursor, message["requestId"], sequence,
            getattr(request.asked, "generation", None), request.issued["session"],
            self.app.services.presentations.workspace_generation(self.app._binding_context(cursor.profile.ref)),
            ticket=request, context=ctx, capture_parent=capture_parent['id'] if capture_parent else None,
            reply_for=reply_for)
        # Transport correlation is not execution authority. Publish it before
        # starting the Runner so a delayed native delivery keeps its browser's
        # initiating arrangement even when that browser has navigated away.
        try:
            socket.offer(json.dumps({"type": "presentation-admitted", "requestId": entry.correlation,
                "executionRequestId": request.admission.request_id,
                "client": cursor.profile.ref.wire(), "appRunId": self.app.services.presentations.app_run}))
        except Exception as exc:
            request.close()
            raise ToolFailed("The requesting studio could not receive this operation",
                             next="reconnect and open the retained result again") from exc
        entries[key] = entry
        self.pending[request] = entry
        # One shared caller task registered before the first await. Each socket
        # waiter is shielded; canceling it cannot cancel the original Runner.
        entry.driver = asyncio.create_task(self._drive(entry, args, resolve_capture=resolve_capture))
        request.track(entry.driver)
        await asyncio.shield(entry.driver)
        return self._project(entry)

    async def _drive(self, entry, args, *, resolve_capture=None):
        from .ticket import using
        from .brain.actions import ActionRunner
        request, ctx = entry.ticket, entry.context

        async def late(run, original):
            if entry.reply_for == 'research-open-capture':
                return  # this bounded control awaits the complete fixed plan below
            if run.sequence is not None:
                await asyncio.shield(run.sequence)
            self._finish(entry)
            if self._reason(entry) is None:
                reply = {"type": "result", "for": entry.reply_for, "requestId": entry.correlation,
                         **self._project(entry)}
                entry.cursor.socket.offer(json.dumps(reply))

        with using(request):
            try:
                if resolve_capture is not None:
                    from dataclasses import asdict
                    resource = await resolve_capture()
                    why = self._reason(entry) or request.cancelled
                    if why:
                        raise ToolFailed(why, next="open the retained capture from the current studio")
                    args = {**args, 'source': asdict(resource)}
                runner = ActionRunner(self.app.brain.registry, self.app.hub, lambda: None,
                                      late, board=self.app.brain.board, log=self.app.agent.log)
                plan = [("present", args)]
                if entry.capture_parent:
                    width = ctx.request_snapshot.observation.wire()['viewport']['layoutSize'][0]
                    if width > 960:
                        plan[0] = ('present', {**args, 'relation': {'kind':'beside',
                            'reference': {'instanceId':entry.capture_parent}, 'gapPx':12, 'align':'top'}})
                    # Fixed local control, not a client-supplied tool plan. The
                    # second step names only this open's output and its report.
                    plan.append(("present", {"do": "group", "targets": [
                        {"step": 1, "output": 0}, {"instanceId": entry.capture_parent}]}))
                runs = await runner.run(plan, ctx)
                entry.runs = tuple(runs)
                entry.run = runs[0]
                if entry.reply_for == 'research-open-capture' and runs[0].sequence is not None:
                    await asyncio.shield(runs[0].sequence)
                if entry.run.ok is not None:
                    if entry.run.sequence is not None:
                        await asyncio.shield(entry.run.sequence)
                    self._finish(entry)
            except (ResearchRefused, ToolFailed) as exc:
                entry.error = str(exc)
                self._finish(entry)
            except asyncio.CancelledError:
                entry.error = "the original manual operation was canceled"
                self._finish(entry)
                raise
            except Exception as exc:
                _log_quietly(self.app, {"kind": "command-error", "command": "present",
                                       "error": f"{type(exc).__name__}: {exc}"[:300]})
                entry.error = "the manual operation could not start; try again"
                self._finish(entry)
            finally:
                request.seal()

    def _capture_parent(self, cursor, pin, selected, *, after_preparation=False):
        """The original Send must show this exact report in this browser."""
        from .presentations import PresentationError
        state = self.app.services.presentations.get(cursor.profile.ref.workspace_id, selected['id'])
        row = next((r for r in pin.observation.wire()['items'] if r['id'] == selected['id']), None)
        if not state.open or state.source.kind != 'written' or row is None or not row.get('visible'):
            raise PresentationError('source_identity_mismatch', 'the requesting report window is no longer visible')
        if (row.get('lifetimeGeneration') != state.lifetime_generation
                or row.get('sourceGeneration') != state.source_generation
                or row.get('propertyVersions') != dict(state.property_versions)):
            raise PresentationError('target_revised', 'the requesting report changed after this request was sent')
        if after_preparation:
            latest = self.app.hub.screen.observation(cursor.socket)
            current = latest.wire() if latest is not None else None
            visible = next((r for r in current['items'] if r['id'] == selected['id']), None) if current else None
            if (visible is None or not visible.get('visible')
                    or any(visible.get(k) != row.get(k) for k in
                           ('lifetimeGeneration', 'sourceGeneration', 'propertyVersions'))
                    or current['viewport'] != pin.observation.wire()['viewport']):
                raise PresentationError('target_revised', 'the original report or visible area changed while preparing its captured view')
        with_context = self.app._binding_context(cursor.profile.ref)
        lease = self.app.services.prepared_sources.borrow(row.get('sourceToken'), with_context)
        try:
            ref = state.source.to_wire()['identity'].get('resource', {})
            report = lease.to_wire().get('report') or {}
            basis = report.get('findingRevision') or {}
            if (not state.source.same_identity(lease.source) or ref.get('day') != selected['day']
                    or ref.get('id') != selected['rid'] or basis.get('version') != selected['findingVersion']
                    or basis.get('digest') != selected['findingDigest']):
                raise PresentationError('source_identity_mismatch', 'the capture belongs to another findings revision')
        finally:
            lease.release()


def _manual_proposal(message):
    """Closed transport translation; server Registry alone allocates occurrences."""
    from .workspace_observation import ObservationError

    def bad(why):
        raise ObservationError("INVALID_MANUAL", why)

    def finite(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False

    try:
        body = json.dumps(message, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        bad("manual proposal needs bounded JSON primitives")
    # The control and its observation have separate existing budgets. A rich, valid
    # screen report is transport context, not part of the 64 KiB control payload.
    control = {key: value for key, value in message.items() if key != "screen"}
    if len(json.dumps(control, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()) > NativeManualCommands.BODY_BYTES:
        bad("manual proposal exceeds 64 KiB")
    if "screen" in message:
        from .workspace_observation import Limits
        if len(json.dumps(message["screen"], allow_nan=False, separators=(",", ":")).encode()) > Limits().wire_bytes:
            bad("inline screen report exceeds its observation budget")
    message = json.loads(body)  # detach all admitted arguments before queueing
    if message.get("type") != "present":
        bad("manual proposal type must be present")
    if "client" in message:
        value = message["client"]
        if (not isinstance(value, dict) or set(value) != {"workspaceId", "clientId", "pageEpoch", "connectionEpoch"}
                or any(not isinstance(v, str) or not 1 <= len(v) <= 256 for v in value.values())):
            bad("manual client metadata needs the complete bounded origin")
    if "appRunId" in message and (not isinstance(message["appRunId"], str) or not 1 <= len(message["appRunId"]) <= 256):
        bad("manual app run metadata needs a bounded identifier")
    canonical = "schema" in message
    correlation = ("requestId", "commandId", "actionId") if canonical else ("requestId", "actionId")
    for key in correlation:
        if not isinstance(message.get(key), str) or not 1 <= len(message[key]) <= 256:
            bad("manual correlation needs bounded opaque identifiers")
    transport = {"type", "requestId", "actionId", "client", "appRunId", "screen"}
    if canonical:
        allowed = transport | {"schema", "manualSeq", "commandId", "op", "targets", "values", "base"}
        if (set(message) - allowed or message.get("schema") != "presentation-manual-v2"
                or not {"client", "appRunId", "targets", "values", "base"} <= set(message)):
            bad("canonical manual proposal has undeclared or missing fields")
        sequence = message.get("manualSeq")
        if type(sequence) is not int or not 1 <= sequence <= 2 ** 53 - 1:
            bad("manualSeq needs a positive safe integer")
        targets, values, op = message["targets"], message["values"], message.get("op")
        fields = {"zoom": {"level", "factor", "fit", "size"}, "pan": {"center"}, "move": {"position"},
                  "resize": {"size"}, "front": set(), "close": set(), "opacity": {"value"}, "hide": set(), "restore": set(), "group": set(), "ungroup": set()}
        if (not isinstance(targets, list) or not (2 <= len(targets) <= 8 if op == "group" else len(targets) == 1)
                or any(not isinstance(iid, str) or not 1 <= len(iid) <= 256 for iid in targets)
                or len(set(targets)) != len(targets) or not isinstance(op, str) or op not in fields
                or not isinstance(values, dict) or set(values) - fields[op]):
            bad("manual control needs exact distinct targets and a supported operation; grouping needs two to eight")
        if op == "zoom":
            if len(set(values) - {"size"}) != 1 or not (values.get("fit") is True or
                    any(finite(values.get(key)) and values[key] > 0 for key in ("level", "factor"))):
                bad("manual zoom needs one positive level/factor or fit:true")
            if "size" in values:
                size = values["size"]
                if (values.get("fit") is not True or not isinstance(size, list) or len(size) != 2
                        or any(not finite(v) or v <= 0 for v in size)):
                    bad("manual fit size needs two positive finite values")
        if op == "opacity" and (set(values) != {"value"} or not finite(values["value"]) or not 0 <= values["value"] <= 1):
            bad("manual opacity needs a fraction between zero and one")
        if op in ("pan", "move", "resize"):
            key = {"pan": "center", "move": "position", "resize": "size"}[op]
            point = values.get(key)
            if (set(values) != {key} or not isinstance(point, list) or len(point) != 2
                    or any(not finite(v) for v in point)
                    or op == "pan" and any(v < 0 or v > 1 for v in point)
                    or op == "resize" and any(v <= 0 for v in point)):
                bad("manual geometry needs its declared two finite values")
        base = message["base"]
        if (not isinstance(base, dict) or set(base) != set(targets)
                or any(not isinstance(base[iid], dict)
                       or set(base[iid]) != {"sourceGeneration", "lifetimeGeneration", "propertyVersions"} for iid in targets)):
            bad("manual base needs complete target generation/version fields")
        for proposed in base.values():
            versions = proposed["propertyVersions"]
            if (type(proposed["sourceGeneration"]) is not int or proposed["sourceGeneration"] < 1
                    or not isinstance(proposed["lifetimeGeneration"], str)
                    or not 1 <= len(proposed["lifetimeGeneration"]) <= 256
                    or not isinstance(versions, dict) or not versions or len(versions) > 16
                    or any(not isinstance(k, str) or not 1 <= len(k) <= 256 or type(v) is not int or v < 0
                           for k, v in versions.items())):
                bad("manual base generations and property versions are invalid")
        args = ({"do": op, "targets": [{"instanceId": iid} for iid in targets]} if op == "group"
                else {"do": op, "what": {"instanceId": targets[0]}})
        if op == "zoom": args["zoom"] = values
        elif op == "opacity": args["value"] = str(values["value"])
        elif op == "pan": args["center"] = values["center"]
        elif op in ("move", "resize"):
            key, coordinates = ("position", ("x", "y")) if op == "move" else ("size", ("width", "height"))
            args[key] = dict(zip(coordinates, values[key]))
    else:
        # Qualified legacy tool shape: finite, explicitly limited correlation
        # retirement. It neither requires an HTTP grant nor gains canonical H/F.
        fields = {"do", "what", "instance", "source", "title", "zoom", "center", "position", "size", "targets", "relation"}
        if set(message) - (transport | fields):
            bad("qualified manual proposal has undeclared fields")
        args = {key: value for key, value in message.items() if key in fields}
        operations = {"open": {"source", "title"}, "duplicate": set(), "split": set(),
            "close": set(), "front": set(), "zoom": {"zoom"}, "pan": {"center"},
            "move": {"position"}, "resize": {"size"}, "arrange": {"targets", "relation"},
            "beside": {"targets", "relation"}}
        op = args.get("do")
        if (not isinstance(op, str) or op not in operations
                or set(args) - (operations[op] | {"do", "what", "instance"})
                or "what" in args and "instance" in args):
            bad("qualified manual operation has conflicting or unused arguments")
        sequence, base = None, None
    return args, body, sequence, base


# Studio commands that are the user changing the board or the screen, written to the interaction log as they happen
# (clicks and questions have their own records; reports, timing and the blind test's own are left out).
USER_ACTIONS = frozenset({"clear", "remove", "restore", "select", "cells", "magnifier", "present", "settings", "slidemap",
                          "presenter", "showing", "thread", "session", "scale", "focus", "flag", "pin-note", "dataset",
                          "view", "source", "source-remove", "source-restore", "asset-meta", "asset-trash", "asset-restore", "action", "action-cancel",
                          "population-correct"})


def command(*kinds: str):
    def register(handler: Handler) -> Handler:
        for kind in kinds:
            COMMANDS[kind] = handler
        return handler

    return register


async def handle(app, client: Client, message: dict) -> None:
    log = getattr(app.hub, "execution", None)
    if log is not None and (log.enabled or log.archive is not None) and message.get("type") in {"click", "ask", "tool", "cells", "present", "research-open-capture"}:
        with execution.request(app.hub, message["type"]):
            try:
                owner = app.hub.screen._profile(client).ref.wire()
            except Exception:
                owner = None
            execution.bind(words=message.get('text'), owner=owner)
            await _handle(app, client, message)
    else:
        await _handle(app, client, message)


async def _handle(app, client: Client, message: dict) -> None:
    kind = message.get("type")
    reply: dict = {"type": "result", "for": kind, "requestId": message.get("requestId")}
    handler = COMMANDS.get(kind)
    before = activity.before_command(app, kind, message) if kind in USER_ACTIONS else None
    try:
        if handler is None:
            reply.update(ok=False, error=f"unknown command {kind}")
        else:
            from research_worker import api_usage
            with api_usage.local_only(getattr(app.day, "local_only", False)):
                reply.update(await handler(app, client, message))
    except ToolFailed as exc:  # a tools.ToolError too: the user's error message, with what to do next
        reply.update(ok=False, error=exc.said(), **({"next": exc.next} if exc.next else {}))
    except Exception as exc:  # unexpected: plain words for the user (the partner §1c), the real error in the log
        logged = _log_quietly(app, {"kind": "command-error", "command": kind, "error": f"{type(exc).__name__}: {exc}"[:300]})
        reply.update(ok=False, error="something went wrong in the app; try again"
                     + ("; if it keeps happening, it is in the log" if logged else ""))
    refused = refusal(reply)
    if kind in USER_ACTIONS:
        # What the user did in the studio, beside what the app did (live 2026-10-11: a clear of every outline arrived
        # while a request was outlining, and nothing said where it came from), and why it did not work when it did not
        # (the demo of 2026-10-11: 10 window changes refused, no reason kept anywhere).
        _log_quietly(app, {"kind": "command", "command": kind, "ok": reply.get("ok") is not False, **refused,
                           "args": {k: v for k, v in message.items() if k not in ("type", "requestId")
                                    and isinstance(v, (str, int, float, bool)) and len(str(v)) <= 120}})
        try:  # and in Activity, in plain words (activity.py)
            activity.command(app, kind, message, reply, before)
        except Exception:
            pass
    if reply.get("ok") is False:
        # Admission can fail before any tool span exists. Retain the bounded
        # protocol reason, never raw exception text or transport/source grants.
        with execution.span("command-rejected", phase="command", rejection=refused.get("code") or "COMMAND_REFUSED") as rejected:
            rejected.finish("failed")
        execution.finish("failed")
    client.offer(json.dumps(reply))


def refusal(reply: dict) -> dict:
    """Why a command did not work, as its reply says it: the error, else its failed step's own words (a manual window
    control or the cells' layer from Made today reply with the run's summary, not an error), with the refusal's code
    (an admission's, or the run's failure code) and what to do next. Empty when it worked. For the command record and
    Activity's details (activity.command); nothing is added to the reply."""
    if reply.get("ok") is not False:
        return {}
    run = reply.get("run") if isinstance(reply.get("run"), dict) else {}
    outcome = run.get("outcome") if isinstance(run.get("outcome"), dict) else {}
    failure = outcome.get("failure") if isinstance(outcome.get("failure"), dict) else {}
    why = reply.get("error") or reply.get("summary") or run.get("summary") or failure.get("reason")
    code = reply.get("code") or failure.get("code")
    upnext = reply.get("next") or run.get("next")
    return {**({"error": str(why)[:300]} if why else {}), **({"code": str(code)[:80]} if code else {}),
            **({"next": str(upnext)[:200]} if upnext else {})}


def _log_quietly(app, record: dict) -> bool:
    """Write a failure to the interaction log; a log that fails too (a full disk) never costs the reply."""
    try:
        app.agent.log(record)
        return True
    except Exception:
        return False


def admit_workspace_command(app, request, msg):
    if msg.get('workspaceOrigin') is None:
        return
    from . import workspace_views
    try:
        if not isinstance(msg['workspaceOrigin'], dict):
            raise ValueError('the workspace origin must be an object')
        origin = dict(msg['workspaceOrigin'])
        expected = ({'resource': msg['resource']} if 'resource' in msg else
                    {'volume': msg['volume']} if 'volume' in msg else {'source': app.day.on_stage})
        if (origin.get('run') != msg.get('run') or
                workspace_views.checked_input(origin.get('input'), app.day.sid) !=
                workspace_views.checked_input(expected, app.day.sid)):
            raise ValueError('The analysis input changed before admission.')
        workspace_views.admit(app.day, origin)
    except ValueError as exc:
        raise ToolFailed(str(exc), next='review the input and run again') from exc
    request.workspace_origin = origin
    request.workspace_store = lambda session=origin['session']: app.day.elsewhere(session)


async def run_tool(app, name: str, args: dict, *, question: str = '', client=None) -> dict:
    """A brain tool, run for the studio or a test: the same thing the brain would do. What it makes is the user's
    (`actor` "you"; ticket 66, S2): the studio's Run, its Actions and its controls run through here."""
    from .ticket import current, issue, using

    request = current()
    owns_request = request is None
    if owns_request:
        request = issue(app.hub, "command", asked=app.conversation.here(), channel="system")
    with using(request):
        try:
            info = app.hub.latest_info
            ctx = ToolContext("system", 0, info.id if info else None, question=question, asked=app.conversation.here(),
                              admission=request.admission, client=client, actor="you")
            stop = user_stop.STOP.get()
            if stop is not None and stop.requested:
                raise asyncio.CancelledError  # the user pressed Stop between the steps of their action: none starts after it
            run = app.brain.registry.start(name, args, ctx)
            if run.task is not None:
                if stop is not None:
                    stop.task = run.task  # what the user's Stop cancels now (action-cancel)
                request.track(run.task)
                await run.task
            return {"ok": bool(run.ok), "summary": run.summary, "error": None if run.ok else run.summary,
                    "executions": list(run.executions),
                    **({"comparison": _comparison_summary(run.values["comparison"])} if name == "compare_models" and run.values.get("comparison") else {}),
                    **({"backgroundJob": run.values["backgroundJob"]} if run.values.get("backgroundJob") else {})}
        finally:
            if owns_request:
                request.seal()


def _comparison_summary(comparison: dict) -> dict:
    """The UI receives identities and measures; scientific geometry stays in its retained resource."""
    fields = {"impl", "label", "failed", "population", "revision", "count", "areaPx", "ms", "reused", "executed"}
    return {**{key: comparison[key] for key in ("id", "operation", "capture", "pairs") if key in comparison},
            "arms": [{key: value for key, value in arm.items() if key in fields} for arm in comparison.get("arms", [])]}


@command("workspace-layout")
async def workspace_layout(app, client, msg) -> dict:
    from . import workspace_views
    if msg.get('session') != app.day.sid:
        return {"ok": False, "error": "The project changed; no layout was written."}
    try:
        submitted = msg.get('layout')
        if submitted is not None and not isinstance(submitted, dict):
            raise ValueError('A workspace layout must be an arrangement object')
        supported = msg.get('schema', submitted.get('schema', 1) if submitted else 1)
        workspace_views.read(app.day, schema=supported)
        if submitted is not None:
            workspace_views.checked_layout(submitted, app.day.sid)
            if submitted['schema'] > supported:
                raise workspace_views.WorkspaceError('unsupported-schema', 'Reload this app before saving the newer layout format.')
        registrations = getattr(app, 'registrations', None)
        if registrations is not None:
            from .day import SessionStore
            session, root, clock = msg['session'], app.day.root, app.day.clock
            def reconcile_owned_jobs():
                owner = SessionStore(root, clock, session=session)
                workspace_views.reconcile_jobs(owner, registrations.records('j', session))
            await asyncio.to_thread(reconcile_owned_jobs)
            if msg['session'] != app.day.sid:
                return {"ok": False, "error": "The Project changed; no layout was written."}
        workspace_views.read(app.day, schema=supported)
        workspace_views.reconcile(app.day)
        workspace_views.reconcile_research(app.day, getattr(getattr(app, 'research', None), 'sessions', {}).values())
        if msg.get('action') == 'dismiss':
            return {'ok':True,**workspace_views.dismiss(app.day,msg.get('run'))}
        result = (workspace_views.arrangement(app.day, msg['action'], msg.get('tab'), msg.get('base')) if msg.get('action') in ('remove','restore') else workspace_views.save(app.day, msg['layout'], msg.get('base')) if 'layout' in msg else workspace_views.read(app.day, schema=msg.get('schema', 1)))
        return {"ok": True, **result}
    except (ValueError, OSError) as error:
        return {"ok": False, "error": str(error), "code": getattr(error, "code", "storage" if isinstance(error, OSError) else "validation"), "current": getattr(error, "current", None)}


@command("session-note")
async def session_note(app, client, msg) -> dict:
    from .workspace_views import save_note
    if msg.get('session') != app.day.sid:
        return {"ok": False, "error": "The project changed; your note draft was kept locally."}
    try:
        if msg.get('action') == 'save':
            return {"ok": True, "note": save_note(app.day, msg)}
        return {"ok": True, "notes": [rows[-1] for rows in app.day.annotations.values()
                                      if rows and rows[-1]['source'].get('scope') == 'session']}
    except (ValueError, OSError) as error:
        return {"ok": False, "error": str(error), "code": getattr(error, "code", "storage" if isinstance(error, OSError) else "validation"), "current": getattr(error, "current", None)}


@command("workspace-profile")
async def workspace_profile(app, client, msg) -> dict:
    import os
    from .workspace_profile import profile_id

    return {"ok": True, "profile": profile_id(app.paths),
            "review": os.environ.get('MICROMIND_WORKSPACE_STUDY') == '1'}


@command("workspace-study-profile")
async def workspace_study_profile(app, client, msg) -> dict:
    from .workspace_study import profile_id

    return {"ok": True, "profile": profile_id(app.paths)}


@command("action")
async def action(app, client, msg, *, capability_input=None) -> dict:
    """_action, with what is on here read once for the whole command (kinds.read_once): its row and the run it starts
    (run_capability) come from one reading of the catalog, made in a thread."""
    from . import kinds

    with kinds.read_once():
        return await _action(app, client, msg, capability_input=capability_input)


async def _action(app, client, msg, *, capability_input=None) -> dict:
    """The Actions panel (actions-panel.ts; Will's ask of 2026-10-12: "the manual alternative to the live/chat agent"):
    one of the agent's capabilities run by the user's own hand, through the same tool the agent would call, so it is
    kept, checked and said in Activity as the agent's are. `capability`: its id in contract/capabilities.json (an
    agent's, with a tool); `args`: the fields the user filled in, over its preset arguments; `use` ("Run with…"):
    {role: model} for this run only, chosen through the agent's own `use` tool first, in the same request, so a model
    that can't run here is refused as the agent's choice would be and nothing runs in its place."""
    from .capabilities import load
    from .ticket import issue, using

    from .workspace_study import action_delay, delaying

    delay = action_delay(msg, client, app.day.sid)
    cid, args, use = str(msg.get("capability") or ""), msg.get("args") or {}, msg.get("use") or {}
    if not isinstance(args, dict) or not isinstance(use, dict):
        raise tools.ToolError("args and use are the action's fields", next=None)  # no next: the studio's own command format
    row = next((c for c in load().get("capabilities", []) if c.get("id") == cid and c.get("actor") == "agent"), None)
    if row is None and cid.startswith("catalog:"):  # a capability the catalog added (the one door, kinds.py): its form
        # is its parameters, run as the agent would run it
        from .capabilities import contributed

        listed = await asyncio.to_thread(contributed, set(app.brain.registry.tools))  # it reads files: off the loop
        row = next((c for c in listed if c["id"] == cid), None)
        if row is not None:
            args = {"params": args, "by": "user"}  # run by the user's own hand: their choice, never the brain's
    name = ((row or {}).get("how") or {}).get("tool")
    if not name or name not in app.brain.registry.tools:
        raise tools.ToolError(f"there is no action {cid!r} here", next="reload the Actions panel")
    if 'resource' in msg:
        if name != 'run_capability':
            raise tools.ToolError('This action cannot use a retained capability input', next=None)  # no next: this action has no resource input contract
        args['resource'] = msg['resource']
    if 'volume' in msg:
        if name != 'run_capability':
            raise tools.ToolError('This action cannot use a volume input', next=None)  # no next: this action has no volume contract
        args['volume'] = msg['volume']
    if 'inputs' in msg:  # several or native inputs, one retained resource per input (admission.py: checked at Run)
        if name != 'run_capability' or not isinstance(msg['inputs'], dict):
            raise tools.ToolError('This action takes no chosen inputs', next=None)  # no next: this action has no typed inputs
        args['inputs'] = msg['inputs']
    if capability_input is not None:
        if name != 'run_capability':
            raise ValueError('only capabilities accept an explicit scientific input')
        args.update(capability_input)
    from .tables import made_words_async

    preset = (row.get("how") or {}).get("args") or {}
    run_id = msg.get("run")
    if run_id is not None and (not isinstance(run_id, str) or not 1 <= len(run_id) <= RUN_ID_MAX):
        raise tools.ToolError("an action's run is the studio's own id", next=None)  # no next: the studio's own command format
    runs = action_runs(app)
    if run_id is not None and run_id in runs:
        raise tools.ToolError("that action is already running", next=None)  # no next: the studio's own command format
    if msg.get('session') is not None and msg['session'] != app.day.sid:
        raise tools.ToolError('The Project changed before admission.', next='choose the input again in its Project')
    title = row.get("title") or name
    asked = app.conversation.here()  # where it was asked: a late Stop's lines are filed there, whatever is open by then
    request = issue(app.hub, "command", asked=asked, channel="system")
    holding = contextlib.ExitStack()
    try:
        admit_workspace_command(app, request, msg)
        # One run at a time of the same action, settings and input in this Project (user_stop.once, L7b): another
        # window, or this one reloaded mid-run (its Run button forgot the run), never starts the same work twice (F094).
        holding.enter_context(user_stop.once(user_stop.identity(app.day.sid, cid, args, _run_models(app, row, use),
                                                                _run_input(app, msg)), title, run_id))
    except user_stop.AlreadyRunning as exc:
        request.seal()
        raise tools.ToolError(str(exc), next=None) from exc  # no next: its words say to wait for the running one
    except BaseException:
        request.seal()
        raise
    by_hand = activity.BY_HAND.set(True)  # what it changes (a setting, the objective) is the user's, said as theirs
    stopped = cut_off = False
    try:
        # One run the user can stop (action-cancel with its `run`): told apart from a waiter that went away (user_stop.py).
        with user_stop.stoppable() as stop, using(request), delaying(delay):
            if run_id is not None:
                runs[run_id] = ActionRun(client, app.day.sid, stop)
            try:
                for role, model in use.items():
                    from .capabilities import operation_of
                    operation = operation_of(str(role))
                    chosen = await run_tool(app, "use", {"operation": operation.id if operation else str(role),
                                                         "implementation": str(model), "by": "user"})
                    if not chosen.get("ok"):
                        raise tools.ToolError(chosen.get("summary") or "that model can't be used here", next=None)  # no next: use's refusal says what to do
                done = await run_tool(app, name, {**preset, **args}, client=client if name == "run_capability" else None)
            except asyncio.CancelledError:
                me = asyncio.current_task()
                if stop.requested:
                    stopped = True
                elif me is not None and me.cancelling():
                    raise  # this command itself is being cancelled (the app stopping): never said as the user's Stop
                else:
                    cut_off = True  # its work was cancelled from inside, not by the user: said, never a missing reply
    finally:
        if run_id is not None and runs.get(run_id) is not None and runs[run_id].stop is stop:
            runs.pop(run_id, None)
        activity.BY_HAND.reset(by_hand)
        request.seal()
        holding.close()
    if cut_off:
        raise tools.ToolError("it was stopped before it finished (not by you)", next="run it again")
    if stopped:
        # Stopped by the user, no failure: nothing after the Stop was applied (registry: its finished listeners were not
        # told; operations: begun work of its own ended). Activity: "Stopped: <action> (you pressed Stop)".
        if not stop.outcomes and not stop.carries_on and stop.task is not None and stop.task.cancelled():
            stop.work(f"“{title}”", "stopped")  # the tool's own run, ended by this Stop (no job of the runner's)
        report = stop.report(title)  # what stopped, what was kept, what was discarded, what carries on (user_stop)
        if stop.late:
            # Work it stopped had not ended within operations.STOP_WAIT_S: said as stopping, never as stopped; its
            # Activity line becomes how it really ended once it does (Runner._stopped tells the Stop), and the studio
            # that started it is told that end for its exact run (`action-settled`), so its row leaves "Stopping".
            from uuid import uuid4

            key = f"action-stop:{run_id or uuid4().hex[:12]}"
            # Its "Stopping" line and the line of how it ended are filed where the action was asked, by `asked` itself,
            # never by Activity's recent-line cache (evicted after 256 lines: then a session opened since would get it).
            activity.activity(app.hub, "command", activity.stopping_words(title), id=key,
                              state="running", details={"ids": {"command": "action", "run": run_id}}, asked=asked)

            def settled(outcomes: list[str]) -> None:
                said = stop.report(title)  # now that its late work ended: what stopped, was kept, was discarded
                outcome, text = activity.action_stop_ended(app.hub, key, title, outcomes, asked=asked,
                                                           said=said["words"])
                if run_id is not None:
                    with contextlib.suppress(Exception):  # a window gone since hears nothing; Activity keeps it
                        client.offer(json.dumps({"type": "action-settled", "run": run_id, "outcome": outcome,
                                                 "text": text, "said": said["words"]}))

            stop.listeners.append(settled)
            return {"ok": True, "stopped": False, "stopping": True, "outcome": "stopping", "title": title,
                    "activityId": key, "summary": report["words"], "stop": report}
        outcome = report["outcome"]  # how its work ended within the wait, or that none of it was ended (user_stop)
        if outcome != "stopped":
            out = {"ok": outcome != "failed", "stopped": False, "outcome": outcome, "title": title,
                   "summary": report["words"], "stop": report,
                   **({"error": "its work did not stop cleanly"} if outcome == "failed" else {})}
        else:
            out = {"ok": True, "stopped": True, "outcome": "stopped", "title": title, "summary": report["words"],
                   "stop": report}
        # Its line is filed where the action was asked, by `asked` itself, never where the user is by now (the Standards
        # re-review of 1cc3db12: a session switched during the Stop's cleanup got it); _handle's own line skips it.
        with contextlib.suppress(Exception):
            activity.command(app, "action", msg, out, asked=asked)
        return out
    if not done.get("ok"):
        raise tools.ToolError(done.get("summary") or "it did not work", next=None)  # no next: the summary says what to do
    # What it measured, in plain words, and its table (tables.py): the panel says this over the agent's summary.
    plain = await made_words_async(app.hub, done["executions"])
    return {**done, "title": row.get("title") or name, **({"said": plain[0], "table": plain[1], "tableIdentity": plain[2]} if plain else {}),
            **_placed(app, request)}


def _placed(app, request) -> dict:
    """Where a This Tab run's result is, when its window waits on its outlines' check on the Stage's still (S5a,
    population.placed_on_stage): "specimen" while it is checked or once drawn there (no window: kept in Data), "tab" once
    its masks window was placed instead. Said only for such a run; every other run's result is placed as before. Later
    decisions reach the studio as `action-placed`."""
    run = getattr(request, "on_specimen", None)
    if not run:
        return {}
    from .population import layer_of

    return {"placed": "tab" if layer_of(app.hub).specimen_runs.get(run) == "placed" else "specimen"}


RUN_ID_MAX = 128  # the studio's own id of one action run (a uuid)


def _run_models(app, row: dict, use: dict) -> dict:
    """Each of its roles' model for this run: the run's own choice ("Run with"), else the current setting, so another
    model is other work, never refused as the same run."""
    current = app.hub.settings.to_wire() if getattr(app.hub, "settings", None) is not None else {}
    return {r: str(use.get(r) or current.get(r) or "") for r in (row.get("roles") or [])} | {k: str(v) for k, v in use.items()}


def _run_input(app, msg) -> object:
    """What an action runs on, for telling two runs apart (user_stop.once): the admitted retained input (an asset on
    stage, a resource, a volume) when there is one, else the live frame it takes now, so a new frame is new work."""
    origin = msg.get("workspaceOrigin") if isinstance(msg.get("workspaceOrigin"), dict) else {}
    chosen = origin.get("input")
    if chosen and chosen != {"source": "live"}:
        return chosen
    info = getattr(app.hub, "latest_info", None)
    return {"frame": getattr(info, "id", None)}


@dataclass(eq=False)
class ActionRun:
    """One Actions run in progress, by the studio's own run id: who started it, in which session, and its Stop."""
    client: object
    session: str
    stop: "user_stop.Stop"


def action_runs(app) -> dict[str, ActionRun]:
    """The Actions runs in progress (run id -> ActionRun), each removed when its run ends."""
    runs = getattr(app, "_action_runs", None)
    if runs is None:
        runs = {}
        app._action_runs = runs
    return runs


@command("action-cancel")
async def action_cancel(app, client, msg) -> dict:
    """The Actions panel's Stop on a running row (issue 40, capability safety and recovery): stops exactly the run the
    studio named, only for the window and session that started it. Its work is cancelled (begun work too when nothing
    else needs it: operations.Runner.run), nothing after the Stop is applied, and the action's own reply and Activity
    say it was stopped ("Stopped: … (you pressed Stop)"), never failed. A waiter that went away is not a Stop."""
    run_id = msg.get("run")
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= RUN_ID_MAX:
        raise tools.ToolError("Stop needs the run it stops", next=None)  # no next: the studio's own command format
    found = action_runs(app).get(run_id)
    if found is None:
        raise tools.ToolError("that action is not running: it has finished, or it was not started here", next=None)  # no next: the row says how it ended
    if found.client is not client:
        raise tools.ToolError("that action was started in another window: stop it there", next=None)  # no next: the message says where
    if found.session != app.day.sid:
        raise tools.ToolError("that action belongs to another session", next="open that session, then press Stop")
    found.stop.stop()
    return {"ok": True, "run": run_id}


@command("operation-retry")
async def operation_retry(app, client, msg) -> dict:
    """One failed original step, never a submitted ask or client-supplied args."""
    store = app.brain.registry.operation_retries
    if msg.get("actionId") is not None:
        action = msg["actionId"]
        if not isinstance(action, str) or len(action) > 256:
            raise ToolFailed("Retry requires an exact retained action identity.", next="open the original activity item and choose its retry control")
        return await store.retry(action, client)
    root = msg.get("receiptId")
    if not isinstance(root, str) or not root or len(root) > 256:
        raise ToolFailed("Choose the original execution receipt to inspect retry availability.", next="open the original activity item")
    return {"ok": True, **store.inspect(root, client)}


@command("execution-recording")
async def execution_recording(app, client, msg) -> dict:
    """UI-only diagnostics: never changes model settings or conversation state."""
    enabled = msg.get("enabled")
    if type(enabled) is not bool:
        raise tools.ToolError("execution recording needs enabled: true or false",
                              next=None)  # no next: the studio's own command format, never the user's words
    app.hub.execution.set_enabled(enabled)
    app.hub.broadcast(app.execution_wire())
    return {"ok": True, "enabled": enabled}


# --- asking and pointing ----------------------------------------------------------------------
@command("workspace-hello")
async def workspace_hello(app, client, msg) -> dict:
    reply = app.workspace_transport.hello(client, msg)
    if reply.get("ok"):
        app.workspace_transport.begin_restore(client)
    return reply


@command("ask")
async def ask(app, client, msg, *, research_selection=None, said: str | None = None) -> dict:
    """`said`: the words the user typed, shown in the chat and kept in the conversation and Activity, when the
    model's `text` also carries context the user did not type (a researcher's composer)."""
    text = str(msg.get("text", "")).strip()
    if msg.get('scientificInput') is not None and (app.arena.enabled or any(msg.get(k) is not None for k in ('frameId', 'objectId', 'capabilityTrial')) or research_selection is not None):
        raise tools.ToolError('A saved attachment cannot be combined with a camera, trial or research selection', next='use the saved attachment alone')
    if not text:
        raise tools.ToolError("ask needs a question", next="type a question")
    # Accepted workspace requests own one Ticket and immutable Send observation
    # before any effect or provider await. Legacy callers keep Brain's issuing path.
    from .ticket import issue
    from .workspace_observation import ObservationError

    screen = getattr(app.hub, "screen", None)
    inline_screen = msg.get("screen")
    request = snapshot = None
    if not app.arena.enabled and (inline_screen is not None or client in getattr(screen, "_accepted", {})):
        request = issue(app.hub, "turn", asked=app.conversation.here(), channel="chat")
        try:
            if screen is None:
                raise ObservationError("OBSERVATION_INCOMPLETE", "workspace observation is unavailable")
            snapshot = screen.pin(client, request_id=request.admission.request_id,
                                  inline=inline_screen,
                                  timing="client-send" if inline_screen is not None else "server-receive-legacy")
        except ObservationError as exc:
            request.seal()
            return {"ok": False, "code": exc.code, "error": str(exc)}
    if request is not None and snapshot is not None:
        from . import workspace_views
        context = next(((i.get('view') or {}).get('legacy', {}).get('workspace') for i in snapshot.observation.wire()['items'] if i['id']=='live'), None)
        if context:
            origin = {'session':context['project'],'tab':context['tab'],'placementId':request.admission.request_id.replace('/','-')[:110],
                      'run':request.admission.request_id[:128],'input':({'resource':msg['scientificInput']['reference']} if isinstance(msg.get('scientificInput'),dict) and isinstance(msg['scientificInput'].get('reference'),dict) else {'source':app.day.on_stage})}
            try: workspace_views.admit(app.day,origin)
            except (ValueError,OSError) as error:
                request.seal()
                return {'ok':False,'error':str(error),'code':getattr(error,'code','storage')}
            request.workspace_origin=origin
            request.workspace_store=lambda session=origin['session']: app.day.elsewhere(session)
    if msg.get('capabilityTrial'):
        from . import capability_trials
        from .ticket import using
        request = request or issue(app.hub, 'turn', asked=app.conversation.here(), channel='chat')
        try:
            with using(request):
                await capability_trials.apply(app, capability_trials.consume(app, client, msg['capabilityTrial'], 'ask'))
        except BaseException:
            request.seal()
            raise
    about = msg.get("objectId")
    if about:
        # Asked from an outline's card (Look deeper): that outline is what "this one" means, so it is
        # made the selection first (Board resolves "this" against the selection).
        if about not in app.hub.scene.objects:
            if request is not None:
                request.seal()
            raise tools.ToolError("that outline is no longer on screen", next="click the thing again, then ask")
        app.hub.scene.select(about)
    said = (said or "").strip() or text
    asked = app.conversation.here()  # the answer goes where it was asked, whatever is open by then
    app.dialogue.show({"type": "chat", "role": "user", "text": said}, asked=asked)
    if app.arena.enabled:
        trial = await app.arena.ask_trial(text)
        return {"ok": True, "trialId": trial.id}
    app.hub.broadcast({"type": "thinking", "on": True})
    frame_id = msg.get("frameId")
    try:
        origin = ({"client": client if snapshot is not None else None, "request_snapshot": snapshot, "request_ticket": request}
                  if request is not None else {})
        if research_selection is not None:
            origin['research_selection'] = research_selection
        if said != text:
            origin['said'] = said
        from . import scientific_ask
        async with scientific_ask.admitted(app.hub, msg.get('scientificInput'), request) as selected:
            answer = await app.brain.turn(text, "chat", frame_id=int(frame_id) if frame_id is not None else None,
                                          asked=asked, **origin, **({'input_capture': selected} if selected is not None else {}))
    finally:
        if request is not None:
            request.seal()
        app.hub.broadcast({"type": "thinking", "on": False})
    compare = answer.pop("compare", None)
    moved, asked = answer.get("asked") != asked, answer.pop("asked", asked)
    if moved:  # it went back to another conversation: the question is shown there too
        app.dialogue.show({"type": "chat", "role": "user", "text": said}, asked=asked)
    if answer.get("answer"):
        app.dialogue.show({"type": "chat", "role": "assistant", "text": answer["answer"], "objectIds": answer["objectIds"],
                           "turnId": answer["turnId"], "actions": answer["actions"], "thinkMs": answer["thinkMs"],
                           "totalMs": answer["totalMs"], "model": answer["model"]}, asked=asked)
    else:
        # Actual operation facts remain replayable without manufacturing an empty
        # assistant anchor or an application-authored interpretation of them.
        for run in answer["actions"]:
            app.dialogue.show({"type": "chat-action", "turnId": answer["turnId"],
                               **run, "standalone": True}, asked=asked)
        if not answer["actions"]:
            app.dialogue.show({"type": "chat-action", "turnId": answer["turnId"],
                               "standalone": True, "answerStatus": answer.get("answerStatus", "not-published"),
                               "calls": answer["calls"], "model": answer["model"]}, asked=asked)
    if compare is not None:
        asyncio.create_task(_post_compare(app, compare, asked))
    return {"ok": True, **answer}


async def _post_compare(app, task, asked) -> None:
    result = await app.agent.compare_result(task)
    if result and not result.get("error"):
        app.dialogue.show({"type": "chat", "role": "compare", "text": result["answer"], "model": result["model"],
                           "thinkMs": result["thinkMs"], "labels": result["labels"]}, asked=asked)
    elif result:
        app.dialogue.show({"type": "chat", "role": "system", "text": f"Comparison model failed: {result['error']}"},
                          asked=asked)


@command("click")
async def click(app, client, msg) -> dict:
    """A click outlines the thing under it; a box dragged on the view outlines what is inside it
    (`box`, frame pixels of the frame the user saw, anchored to it as the brain's boxes are).
    Every click is logged, a failed one too, so an outline that did not come leaves a trace."""
    from .capture import captures
    from .ticket import issue, using

    started = time.perf_counter()
    info = app.hub.latest_info
    record: dict = {"kind": "click", "frameId": msg.get("frameId"), "latestFrame": info.id if info else None}
    # The click is one request (ticket.py): the view the user clicked on is held until it is
    # outlined and named, and a clear made meanwhile means its results change nothing on screen.
    request = issue(app.hub, "click", channel="studio")
    try:
        with using(request):
            if msg.get('capabilityTrial'):
                from . import capability_trials
                await capability_trials.apply(app, capability_trials.consume(app, client, msg['capabilityTrial'], 'click'))
            quiz = getattr(app, "quiz", None)
            quiz_context = quiz.capture_click(client) if quiz is not None else None
            return await _click(app, msg, request, started, record, captures, quiz_context)
    finally:
        request.seal()
        with contextlib.suppress(Exception):
            activity.clicked(app, msg, record)  # Activity: what outlined it, from the click's own record


async def _click(app, msg, request, started, record, captures, quiz_context=None) -> dict:
    obj = None
    try:
        frame_id, box = int(msg["frameId"]), _dragged_box(msg.get("box"))
        # A box is outlined around its centre (the point logged); a click at its point.
        x, y = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else (float(msg["x"]), float(msg["y"]))
        record.update(x=round(x, 1), y=round(y, 1), box=[round(v, 1) for v in box] if box else None)
        # The view clicked on, held by the click from its start (ADR 0010).
        clicked = request.hold(captures(app.hub).get(frame_id), "interactive")
        from .population import layer_of as _layer_of

        if not (box is None and clicked is not None and _layer_of(app.hub).member_hit(clicked, x, y) is not None) \
                and _layer_of(app.hub).selected is not None:
            _layer_of(app.hub).select(None)  # a click that outlines something: the cell clicked before is no longer "that" (N1)
        if app.arena.enabled:
            trial = await app.arena.outline_trial(frame_id, x, y, box=box)
            record.update(ok=True, trialId=trial.id)  # each arm's model stays unnamed until the vote
            return {"ok": True, "trialId": trial.id}
        from .population import layer_of

        layer = layer_of(app.hub)
        member = layer.member_hit(clicked, x, y) if box is None and clicked is not None else None
        if member is not None:
            # A click inside a shown, verified member selects it (the design's decision): no new outline. The
            # outline selected before is no longer what "that" means (M1.5c re-check N7).
            app.hub.scene.select(None)
            layer.select(member)
            record.update(ok=True, member=member.id, population=layer.shown)
            return {"ok": True, "member": member.id, "population": layer.shown}
        record["outline"] = app.hub.settings.outline
        obj = await tools.outline_at(app.hub, clicked if clicked is not None else frame_id, x, y, box=box)
        # Named next, on the view it was made on (held by this click since outline_at): the hold
        # reserves room for naming's colour decode too.
        request.hold(request.held(obj.origin), "interactive", "outline")
        record.update(ok=True, objectId=obj.id, areaPx=round(area_px(obj.polygons)), bbox=[round(v, 1) for v in obj.bbox])
        made = (getattr(obj, "made_by", None) or {}).get("execution")
        if made:
            record["executionId"] = made  # what ran, linkable to its execution line (Amendment 1, item 4)
        machine = getattr(app.hub, "machine", None)
        if machine is not None:
            machine.clicked((time.perf_counter() - started) * 1000)  # the meter's click time
    except Exception as exc:
        record.update(ok=False, error=str(exc) or type(exc).__name__)
        raise
    finally:
        record["ms"] = round((time.perf_counter() - started) * 1000)
        app.agent.log(record)
    if msg.get("identify", True) and app.agent.available():
        request.track(asyncio.create_task(_identify(app, obj.id, quiz_context)))
    return {"ok": True, "objectId": obj.id}


def _dragged_box(raw) -> tuple[float, float, float, float] | None:
    """The box the user dragged (x0, y0, x1, y1 in frame pixels, corners in any order), or None
    for a plain click."""
    if raw is None:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        raise tools.ToolError("a dragged box needs four numbers",
                              next=None) from None  # no next: the studio's own command format, never the user's words
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        raise tools.ToolError("a dragged box needs four numbers",
                              next=None)  # no next: the studio's own command format, never the user's words
    box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if box[2] - box[0] < MIN_BOX_PX or box[3] - box[1] < MIN_BOX_PX:
        raise tools.ToolError("that box is too small to outline in; drag a larger one, or click",
                              next=None)  # no next: the message says what to do
    return box


NAMING_AT_ONCE = 3  # outlines the app drew, named at the same time (a highlight of many cells waits its turn)


async def name_drawn(app, object_id: str) -> None:
    """An outline the app drew (a highlight, a find, the look's pointing, a promoted cell), named by the same step as a
    tap (agent.identify; option NAMING "one", tools.name_like_a_tap). It runs on its own, outside the request that drew
    it, so a later request never leaves it half named; the outline's revision still keeps a name the user gave since.
    Never a lesson's answer and never a hint about a tap; if naming fails the outline keeps the name it had."""
    from . import ticket as tickets

    obj = app.hub.scene.objects.get(object_id)
    if obj is None:
        return
    before = {"label": obj.label, "status": obj.status, "confidence": getattr(obj, "confidence", None)}
    slots = getattr(app, "_naming_slots", None)
    if slots is None:
        slots = app._naming_slots = asyncio.Semaphore(NAMING_AT_ONCE)
    with tickets.using(None):
        async with slots:
            try:
                await app.agent.identify(object_id, quiet=True)
            except Exception as exc:  # said in the log; the outline keeps the name it had
                _log_quietly(app, {"kind": "identify-error", "objectId": object_id, "drawnBy": obj.source,
                                   "error": f"{type(exc).__name__}: {exc}"[:300]})
                now = app.hub.scene.objects.get(object_id)
                if now is not None and now.status == "looking":
                    app.hub.scene.update(object_id, **before)


async def _identify(app, object_id: str, quiz_context=None) -> None:
    """Name what was clicked. The name and what was seen go on the outline and its card, not
    into the chat (the chat is for the conversation; clicks are not a post each); the brain
    still has it in its conversation window. During a lesson's find step the click is then
    judged as the user's answer (quiz.py)."""
    quiz = getattr(app, "quiz", None)
    if quiz_context is None and quiz is not None:
        quiz_context = quiz.capture_click()
    try:
        await app.agent.identify(object_id)
    except ToolFailed as exc:  # said in plain words, with what to do next
        app.hub.broadcast({"type": "hint", "text": f"Couldn't name that: {exc.said()}", "tone": "error"})
    except Exception as exc:  # unexpected: plain words for the user, the real error in the log
        _log_quietly(app, {"kind": "identify-error", "objectId": object_id, "error": f"{type(exc).__name__}: {exc}"[:300]})
        app.hub.broadcast({"type": "hint", "text": "Couldn't name that: something went wrong in the app; click it again",
                           "tone": "error"})
    with contextlib.suppress(Exception):
        activity.named(app, object_id)  # the click's Activity line says the name and the model that gave it
    quiz = getattr(app, "quiz", None)
    if quiz is not None and quiz_context is not None and quiz_context[0] is not None:
        # A click while a lesson's find step is open is the user's answer (quiz.py): the model
        # judges it from what naming and the running look said, and says so.
        try:
            await quiz.clicked(object_id, captured=quiz_context)
        except Exception as exc:
            app.agent.log({"kind": "quiz-click", "objectId": object_id, "ok": False, "error": str(exc) or type(exc).__name__})


# --- the user's verdicts on capabilities (ADR 0007) --------------------------------------------
@command("validate")
async def validate(app, client, msg) -> dict:
    """Works, broken (with a note) or not yet: kept in the app's own log until `make verdicts`
    puts it in git; every studio is sent the catalogue again."""
    try:
        # Capture only declared role choices and edition; never transport grants or API keys.
        current = app.capabilities_wire()
        event = app.verdicts.record(str(msg.get("id") or ""), str(msg.get("state") or ""), str(msg.get("note") or ""),
                                    setup=current.get("setup"))
    except ValueError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the message says what was wrong
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "verdict": event}


# --- the app's own check of each model (model_checks.py; the user's Q20, Slice 8) ---------------------------------
@command("capability-check")
async def capability_check(app, client, msg) -> dict:
    """Check now (the Capabilities page): every model on this Mac runs once on the leaf frame, inside the live app,
    one at a time in the workers' lowest class, in the background; the page shows each as it is checked. One check at a
    time: a press while one runs says so. The cloud models are `make capability-check-cloud`'s (they cost money)."""
    from . import model_checks

    store = getattr(app.hub, "checks", None)
    if store is None:
        raise ToolFailed("the model check is not available here", next=None)  # no next: nothing the user can do here
    if store.running is not None:
        return {"ok": True, "running": dict(store.running), "said": "a check is already running"}
    chosen = model_checks.chosen("local")
    store.running = {"by": "Check now", "done": 0, "of": len(chosen), "now": ""}  # at once: a second press waits
    try:
        jpeg = await asyncio.to_thread(model_checks.frame)
    except OSError as exc:
        store.running = None
        raise ToolFailed(f"the check's frame is not in the assets store ({exc})", next="make assets") from exc
    checker = model_checks.Checker(app.hub, app.agent, store, by="Check now", jpeg=jpeg, live=True, log=app.agent.log,
                                   progress=lambda state: app.hub.broadcast(app.capabilities_wire()))

    async def go() -> None:
        try:
            lines = await checker.run(chosen)
        except Exception as exc:  # noqa: BLE001 - said, never a stuck "checking"
            store.running = None
            app.agent.log({"kind": "model-check-run", "by": "Check now", "error": f"{type(exc).__name__}: {exc}"[:300]})
            app.hub.broadcast(app.capabilities_wire())
            return
        found = model_checks.summary(lines)
        app.agent.log({"kind": "model-check-run", "by": "Check now", **found})
        counts = ", ".join(f"{n} {model_checks.STATE_WORDS[s]}" for s, n in found["counts"].items())
        try:
            app.dialogue.notice(producer={"kind": "model-check"}, event="checked",
                                summary=f"Checked the models on this Mac: {counts}."
                                        + (f" Broken: {'; '.join(found['broken'])}." if found["broken"] else ""),
                                facts=found, tone="error" if found["broken"] else "info")
        except Exception:  # noqa: BLE001 - the page shows the result either way
            pass
        app.hub.broadcast(app.capabilities_wire())

    task = asyncio.create_task(go())
    app._tasks.append(task)
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "started": True, "of": len(chosen)}


# --- the models folder: Download on a model's step (downloads.py; P2 of the open catalog) --------------------------
@command("model-download")
async def model_download(app, client, msg) -> dict:
    """Download a model's pinned files into the models folder, in the background (or adopt them when this Mac already
    has them at that revision): an Activity line and the wire's `setup.downloads` follow it. A key or a licence it
    needs first is said as that step."""
    from .downloads import Failed

    try:
        row = app.downloads.start(str(msg.get("model") or ""), by="you")
    except Failed as exc:
        raise ToolFailed(exc.says, next={"key": "add your Hugging Face key", "licence": f"accept its licence: {exc.url}",
                                         "disk": "make room on the disk"}.get(exc.step)) from exc
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "download": row}


# --- your own models (mine.py; P5 of the open catalog, ADR 0021) ---------------------------------------------------------
@command("model-add")
async def model_add(app, client, msg) -> dict:
    """Add a model of your own from a link or a path (the Models page's Add your own): its entry is written and shown at
    once; nothing of it runs until it is turned on."""
    from . import mine

    try:
        added = await asyncio.to_thread(mine.add, str(msg.get("source") or ""), str(msg.get("name") or ""))
    except mine.CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what to give instead
    from . import own_trials
    if msg.get("trial"):
        try:
            await asyncio.to_thread(own_trials.prepare, added["model"], str(msg["trial"]))
        except ValueError as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the input error says what must be selected again
    elif added.get("kind") != "skeleton":
        # Add needs no picture, but a model from a link is still compared on one before it is a choice (C3).
        await asyncio.to_thread(own_trials.await_comparison, added["model"])
    activity.activity(app.hub, "model", added["says"], ref={"model": added["model"]})
    await app.sync_mine()
    return {"ok": True, **added}


@command("model-activate")
async def model_activate(app, client, msg) -> dict:
    """Turn on a model of yours: its check runs once in a process of its own (mine.activate), and only a pass makes it a
    choice under Cells. Said in Activity, running then done or failed with each step (App.activate_mine)."""
    from gpu_worker import manifests

    key = str(msg.get("model") or "")
    if manifests.mine().get(key) is None:
        raise ToolFailed("there is no model of yours by that name", next="reload the Models page")
    return await model_turn_on(app, client, msg)


@command("model-deactivate")
async def model_deactivate(app, client, msg) -> dict:
    """Turn a model off: it is no longer a choice (a setting that chose it goes back to the default, said). One with its
    own adapter (yours, the reviewed catalog's or a community channel's) keeps every installed version and its worker is
    retired (env_runner.turn_off); nothing is deleted, and Turn on makes it a choice again."""
    from gpu_worker import manifests as catalog

    from . import env_runner, mine

    m = catalog.get(str(msg.get("model") or ""))
    if m is not None and m.runner == "fiji" and m.namespace:
        from . import fiji_contributions

        def withdraw():
            for job, intent in app.turn_on.wire().items():
                if intent.get("model") == m.key:
                    app.turn_on.cancel(job, attempt=intent.get("attempt"))
            app.installs.cancel(m.key, why="you turned this capability off")
            fiji_contributions.turn_off(m)
        await asyncio.to_thread(withdraw)
        app.catalog_changed()
        activity.activity(app.hub, "model", f"{m.name} is off; its proven version is kept", ref={"model": m.key})
        return {"ok": True}
    if m is not None and m.runner == "python-env":
        if env_runner.in_use(m) is None:
            raise ToolFailed(f"{m.name} isn't on", next="reload the Models page")
        try:
            await asyncio.to_thread(env_runner.turn_off, m)
        except env_runner.InUse as exc:  # a run uses it: never ended unsaid by a Turn off (L7b, row 12)
            raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what to do
        app.catalog_changed()  # its choice taken away and its worker retired (env_runner.register)
        activity.activity(app.hub, "model", f"{m.name} is off: no longer a choice; its installed versions are kept, and "
                          "Turn on makes it a choice again", ref={"model": m.key})
        return {"ok": True}
    try:
        await asyncio.to_thread(mine.deactivate, str(msg.get("model") or ""))
    except mine.CantDo as exc:
        raise ToolFailed(str(exc), next="reload the Models page") from exc
    await app.sync_mine()
    return {"ok": True}


@command("observe-again")
async def observe_again(app, client, msg) -> dict:
    """Check again (a card's facts), or the Models page opened: what the app asked the outside world (a licence's access,
    whether a key works) is asked again: one model's, or everything older than a couple of minutes (observed.py)."""
    from .observed import PAGE_STALE_S

    model = str(msg.get("model") or "") or None
    await app.observe(model, stale=None if model else PAGE_STALE_S)
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True}


@command("observe-confirm")
async def observe_confirm(app, client, msg) -> dict:
    """"I've accepted it": the user's word for a licence no probe can ask about (observed.SOURCES "user"), kept as theirs
    with when."""
    from . import observed

    key = str(msg.get("model") or "")
    if msg.get("update"):  # the licence only its waiting update needs (maintenance's Needs review step)
        from .maintenance import discover
        update = discover.candidate(key)
        if update is None:
            raise ToolFailed(f"{key} has no update waiting", next="press Check for updates on its card")
        key = discover.update_target(key, update.version)
    observed.confirm("access", key)
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True}


@command("page-opened")
async def page_opened(app, client, msg) -> dict:
    """The studio's answer to open-page (open_models): it shows the page now, and whether it is in view."""
    app.page_opened(str(msg.get("id") or ""), {"page": str(msg.get("page") or ""), "shown": bool(msg.get("shown")),
                                                "visible": bool(msg.get("visible"))})
    return {"ok": True}


@command('workspace-tabs-result')
async def workspace_tabs_result(app, client, msg):
    try:
        accepted = app.hub.workspace_tabs.reply(client, msg)
    except ValueError:
        accepted = False
    return {'ok': accepted}


@command("model-reveal")
async def model_reveal(app, client, msg) -> dict:
    """The Models page's Show in Finder: a model's folder in the models folder (its revisions inside), or the models
    folder itself when it has none yet (this Mac's own app). With no model (the small-models offer's folder line), the
    models folder itself."""
    import subprocess
    import sys

    from gpu_worker import manifests, model_files

    key = str(msg.get("model") or "")
    m = (manifests.get(key) or manifests.mine().get(key)) if key else None
    if key and m is None:
        raise ToolFailed("there is no model of that name in the catalog", next="reload the Models page")
    folder = model_files.folder(m) if m is not None else None
    shown = folder if folder is not None and folder.exists() else manifests.catalog_root() / "models"
    shown.mkdir(parents=True, exist_ok=True)  # the models folder is the user's to look in, empty or not
    if sys.platform != "darwin":
        return {"ok": False, "error": f"the folder is {shown}", "path": str(shown)}
    subprocess.Popen(["open", str(shown)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(shown)}


@command("model-install")
async def model_install(app, client, msg) -> dict:
    """Make a model's own Python from its catalog lockfile (envs.py), in the background: an Activity line and the wire's
    `setup.installs` follow it, and its card turns Ready once its proof passed."""
    from .envs import Failed

    try:
        row = app.installs.start(str(msg.get("model") or ""), by="you")
    except Failed as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the model names no environment of its own
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "install": row}


@command("model-prepare")
async def model_prepare(app, client, msg) -> dict:
    """Prepare a scientific candidate for explicit private review, without activation."""
    from .envs import Failed
    try:
        row = app.installs.start(str(msg.get('model') or ''), by='you', prepare_only=True)
    except Failed as exc:
        raise ToolFailed(str(exc), next="open the candidate's setup and review its preparation requirements") from exc
    app.hub.broadcast(app.capabilities_wire())
    return {'ok': True, 'install': row}


@command("fiji-add-site")
async def fiji_add_site(app, client, msg) -> dict:
    """Turn on a Fiji extension by its update site (the inventory's reviewed route), in Fiji's next generation: proven
    before it is used, the generation in use left as it is (fiji_generations.py)."""
    from . import inventories
    from .envs import Failed

    page = str(msg.get("page") or "")
    row = next((e for e in inventories.fiji().get("extensions", []) if e["page"] == page), None)
    route = (row or {}).get("route") or {}
    if route.get("kind") != "update-site":
        raise ToolFailed("that extension has no update site to turn on" if row else "there is no such extension",
                         next=None)  # no next: it ships with Fiji, or no install route is known yet
    try:
        job = app.installs.start("fiji", by="you", site={"site": route["site"], "url": route["url"]})
    except Failed as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: Fiji isn't set up
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "install": job}


@command("fiji-rollback")
async def fiji_rollback(app, client, msg) -> dict:
    """Go back to the previous Fiji generation (the newer one is kept)."""
    from . import fiji_generations

    try:
        n = await asyncio.to_thread(fiji_generations.rollback)
    except fiji_generations.Failed as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: there is no earlier generation
    app.catalog_changed()
    return {"ok": True, "generation": n}


@command("catalog-update")
async def catalog_update(app, client, msg) -> dict:
    """Check for catalog updates: the reviewed catalog at its newest commit (channels.py), so an entry merged since this
    app was made is listed at once; its card's Download and Install turn it on."""
    from . import channels

    try:
        r = await app.update_catalog(by="you")
    except channels.CantDo as exc:
        raise ToolFailed(app.channel_update["says"], next="check this Mac is online, then check again") from exc
    return {"ok": True, **r}


@command("capability-try-voice")
async def capability_try_voice(app, client, msg) -> dict:
    from .capability_trials import consume
    consume(app, client, msg.get('token', ''), 'voice')
    return {'ok': True}


@command("capability-try")
async def capability_try(app, client, msg) -> dict:
    from . import capability_trials

    return await capability_trials.prepare(app, client, str(msg.get("id") or ""), str(msg.get("option") or ""),
                                           empty=msg.get("empty") is True)


@command("capability-remove")
async def capability_remove(app, client, msg) -> dict:
    """Remove exactly the managed runtime copies reviewed in Space; never source data or saved results."""
    from . import capability_space, scientific_retention

    try:
        return await capability_space.remove(app, str(msg.get("id") or ""), str(msg.get("revision") or ""))
    except scientific_retention.Retained as exc:
        raise ToolFailed(str(exc), next=exc.next) from exc
    except ValueError as exc:
        raise ToolFailed(str(exc), next="open Space and refresh its list") from exc


@command("capability-turn-on")
async def capability_turn_on(app, client, msg) -> dict:
    """Turn on, on a job's card (capability_shelf.py, C2): one press carries every step its option needs, the app's own
    (a download, an install and its proof) in their queues, the user's own (a key, a licence, an Allow) asked on the
    card; once it runs, it is said."""
    job = str(msg.get("id") or "")
    asked = app.day.here()
    try:
        started = await asyncio.to_thread(app.turn_on.start, job, str(msg.get("option") or "") or None, asked=asked,
                                          expected=msg.get("plan"), recheck=msg.get("recheck") is True)
    except ValueError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say why nothing of it can be turned on
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, **started}


@command("capability-select")
async def capability_select(app, client, msg) -> dict:
    from . import capability_shelf as shelf

    session, job, option = msg.get('session'), msg.get('job'), msg.get('option')
    if not session or session != app.day.sid:
        raise ToolFailed('The session changed; reopen setup before choosing a model', next=None)  # no next: the refusal names the action to take in the guided sheet
    wire = app.capabilities_wire(counts=False)
    role = next((r for r in wire.get('roles', []) if r['id'] == job), None)
    if role is None or role.get('fixed') or role.get('entry'):
        raise ToolFailed('This capability is run directly; it is not a model choice', next=None)  # no next: the refusal names the action to take in the guided sheet
    candidate = next((o for o in shelf._options(role) if o['id'] == option), None)
    if candidate is None or not shelf._proven(candidate):
        raise ToolFailed('This candidate is not ready; check its setup again', next=None)  # no next: the refusal names the action to take in the guided sheet
    recipe = await asyncio.to_thread(shelf.recipe, candidate)
    if recipe != msg.get('recipe'):
        raise ToolFailed('The candidate changed; review its setup again', next=None)  # no next: the refusal names the action to take in the guided sheet
    fresh = app.capabilities_wire(counts=False)
    role = next((r for r in fresh.get('roles', []) if r['id'] == job), None)
    candidate = next((o for o in shelf._options(role) if o['id'] == option), None) if role else None
    setup = next((s for m in fresh.get('models', []) for s in m.get('setups', [])
                  if s['role'] == job and s['option'] == option), None)
    if (role is None or role.get('fixed') or role.get('entry') or candidate is None
            or not shelf._proven(candidate) or setup is None or setup.get('recipe') != recipe):
        raise ToolFailed('The candidate changed or is no longer ready; review its setup again', next=None)  # no next: the refusal names the action to take in the guided sheet
    current = app.hub.settings.to_wire().get(job)
    if session != app.day.sid or (current != msg.get('current') and current != option):
        raise ToolFailed('The session or model choice changed; review the current choice before replacing it', next=None)  # no next: the refusal names the action to take in the guided sheet
    problems = [] if current == option else change_settings(app, {job: option}, by='menu')
    return {'ok': not problems, 'error': '; '.join(problems) or None, 'settings': app.hub.settings.to_wire()}


@command("model-turn-on")
async def model_turn_on(app, client, msg) -> dict:
    """Turn it on, for one catalog entry (one just added from a link, or one New for you offers): every step it needs,
    carried through as a job's Turn on is (capability_shelf.TurnOn), said once it is on. With `trial` (Set up for
    comparison, a picture chosen only now: Add no longer asks for one), that picture is the one it is compared on before
    Use it adds the choice (own_trials.prepare)."""
    key = str(msg.get("model") or "")
    asked = app.day.here()
    if msg.get("trial"):
        from . import own_trials
        try:
            await asyncio.to_thread(own_trials.prepare, key, str(msg["trial"]))
        except ValueError as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the input error says what must be selected again
    try:
        started = await asyncio.to_thread(app.turn_on.start, f"model:{key}", asked=asked)
    except ValueError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say why it can't be turned on here
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, **started}


@command("model-build")
async def model_build(app, client, msg) -> dict:
    """Build it (C4): for a link no template runs, a researcher drafts an adapter (reading its pages), and the app tries
    each draft on the view now inside macOS's sandbox until it runs (drafts.py). Started only by this press: it uses the
    research model under the user's key, at the builder's own budget, said on the card."""
    from . import drafts
    from .research.policy import ResearchRefused

    link = str(msg.get("link") or "").strip()
    description = msg.get("description", "")
    if not isinstance(description, str) or len(description) > 4000 or "\0" in description:
        raise ToolFailed("Describe the tool in at most 4,000 characters", next="edit the request in this sheet")
    description = description.strip()
    guided = "description" in msg
    if guided and (not description or not msg.get("trial") or msg.get("session") != app.day.sid):
        raise ToolFailed("Choose a test picture and describe the job in the current session", next="reopen Create or adapt with AI")
    if (link and not link.startswith("https://")) or (not link and not description):
        raise ToolFailed("give the model's https link", next="paste its GitHub or Hugging Face address")
    from research_worker import prompts
    try:
        prompts.deliverable_text("adapter")
    except (OSError, ValueError) as exc:
        raise ToolFailed("The canonical capability skill is missing or invalid; the build did not start", next="restore the capability skill and retry") from exc
    picture = None
    try:
        info = app.hub.best_frame()
        frame = app.hub.source.get(info.id) if info is not None else None
        picture = getattr(frame, "jpeg", None) if frame is not None else None
    except Exception:  # noqa: BLE001 - no picture now: the app's representative one is tried instead
        picture = None
    from . import own_trials
    trial = str(msg.get("trial") or "")
    job = "find"
    if trial:
        try:
            selected = await asyncio.to_thread(own_trials.state, trial)
            picture = await asyncio.to_thread(own_trials.picture, trial)
            job = selected["job"]
        except ValueError as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the input error says what must be selected again
    did = await asyncio.to_thread(drafts.create, link, picture, job=job, trial=trial)
    if guided and msg.get("session") != app.day.sid:
        await asyncio.to_thread(drafts.finish, did, "refused")
        raise ToolFailed("The session changed; the build did not start", next="reopen Create or adapt with AI")
    brief = (f"Create a capability in HookeUI for {own_trials.JOBS[job][0]}. "
             f"Input image.2d.rgb8; output {own_trials.JOBS[job][1]}. Read its pages, pin its weights, draft "
             "its adapter and try it on the retained input, or say plainly why it cannot do this job.")
    brief += "\nUser request and optional source (task context): " + json.dumps({"request": description, "source": link})
    try:
        session = await app.research.start(brief, effort="build", deliverable="adapter", confirmed=True,
                                           label=(description or link.rstrip("/").split("/")[-1])[:40], origin={"channel": "studio"})
    except ResearchRefused as exc:
        await asyncio.to_thread(drafts.finish, did, "refused")
        raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on from ResearchRefused
    app.research.drafts = {**getattr(app.research, "drafts", {}), session.rid: did}
    await asyncio.to_thread(drafts.assign, did, session.rid)
    follow = asyncio.get_running_loop().create_task(_follow_draft(app, did))
    app.draft_followers = {*getattr(app, "draft_followers", set()), follow}  # kept until it ends (a task held weakly is lost)
    follow.add_done_callback(lambda task: app.draft_followers.discard(task))
    return {"ok": True, "draft": did, "researcher": session.rid,
            "says": f"Researcher {session.n} is drafting an adapter for it (up to 15 minutes, at most $2 of your research "
                    "model). Each try runs inside the sandbox; you choose whether to use what it makes."}


async def _follow_draft(app, did: str) -> None:
    """The studio's sheet follows its draft while the builder works (live 2026-10-20: it said "Reading the retained AI
    draft" until a reload, since nothing told the studios a try had ended): each change of its row is broadcast, until
    it is used or its builder ended, at most the builder's budget and a margin."""
    from . import drafts

    def row():
        return next((r for r in drafts.wire() if r["id"] == did), None)

    last, until = None, time.monotonic() + drafts.BUDGET_S + 300
    while time.monotonic() < until:
        now = await asyncio.to_thread(row)
        seen = json.dumps(now, sort_keys=True, default=str)
        if seen != last:
            last = seen
            app.hub.broadcast(app.capabilities_wire())
        ended = now is None or now["state"] == "used" or now.get("terminal")
        if ended and not ((now or {}).get("review") or {}).get("busy"):  # a busy review is sent again once it reads
            return
        await asyncio.sleep(2)


@command("model-draft-use")
async def model_draft_use(app, client, msg) -> dict:
    """Use it: a draft that ran, exactly as its review showed it (`reviewed`, the review's digest), becomes one of your
    models (drafts.promote), then installed and proven as any."""
    from . import drafts

    try:
        used = await asyncio.to_thread(drafts.promote, str(msg.get("draft") or ""), reviewed=str(msg.get("reviewed") or ""))
    except drafts.DraftError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what it still needs
    await app.sync_mine()
    await asyncio.to_thread(app.turn_on.start, f"model:{used['model']}", asked=app.day.here())
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, **used, "says": "Adding your reviewed draft as a choice; its installation and functional check are running."}


@command("model-own-compare")
async def model_own_compare(app, client, msg) -> dict:
    from . import own_trials
    try:
        result = await own_trials.compare(app, str(msg.get("trial") or ""), model=str(msg.get("model") or ""),
                                         draft=str(msg.get("draft") or ""))
    except (ValueError, OSError) as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the comparison refusal explains its missing or changed input
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, **result}


@command("model-own-use")
async def model_own_use(app, client, msg) -> dict:
    from . import own_trials
    try:
        result = await asyncio.to_thread(own_trials.use, str(msg.get("model") or ""), str(msg.get("trial") or ""))
    except ValueError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the adoption refusal explains what must be compared again
    await app.sync_mine()
    return {"ok": True, **result}


@command("model-draft-community")
async def model_draft_community(app, client, msg) -> dict:
    """Prepare a local, reviewable community question; this command never sends it."""
    from . import drafts
    try:
        return {"ok": True, **await asyncio.to_thread(drafts.community, str(msg.get("draft") or ""))}
    except drafts.DraftError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the draft error names what is unavailable


@command("model-publish-prepare")
async def model_publish_prepare(app, client, msg) -> dict:
    """Share with everyone, step one (publish.py, unchanged: its review package and certification): the entry's files,
    its proof, the pull request's body and its diff, prepared on this Mac for the user to read. Nothing leaves this Mac."""
    import re as _re

    from . import github_signin, publish

    key = str(msg.get("model") or "")
    destination = str(msg.get("destination") or "review")
    if github_signin.token() or destination == "community":
        from . import publish_api

        try:
            made = await asyncio.to_thread(publish_api.prepare, key, destination=destination)
        except publish.CantPublish as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what stops it
        return {"ok": True, "says": made["review"], "package": made["package"], "route": "api",
                "identity": made["identity"], "files": made["files"]}
    try:
        says = await asyncio.to_thread(publish.propose, key.removeprefix("mine/"))
    except publish.CantPublish as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what stops it
    found = _re.search(r"Prepared (\S+) for local review", says)
    return {"ok": True, "says": says, "package": found.group(1) if found else "", "route": "git"}


@command("model-publish-submit")
async def model_publish_submit(app, client, msg) -> dict:
    """Share with everyone, step two: the reviewed package sent for review (publish.py: pushed and a pull request opened,
    only with the user's own DCO sign-off on this press)."""
    from pathlib import Path as _Path

    from . import publish, publish_api

    key, package = str(msg.get("model") or ""), str(msg.get("package") or "")
    if not package:
        raise ToolFailed("prepare it first: the package to review is what is sent", next="press Prepare, read it, then send")
    if msg.get("route") == "api" or publish_api.prepared(package):
        from . import publish_api

        try:  # exactly the reviewed package, through GitHub's API with the sign-in, only on the user's sign-off
            sent = await asyncio.to_thread(publish_api.submit, package, msg.get("signoff") is True, model=key)
        except publish.CantPublish as exc:
            raise ToolFailed(str(exc), next=None) from exc  # no next: the publication refusal names its missing review or sign-off prerequisite
        return {"ok": True, "says": sent["says"], "url": sent.get("url")}
    try:
        says = await asyncio.to_thread(lambda: publish.propose(key.removeprefix("mine/"), push=True,
                                                               package=_Path(package), signoff=msg.get("signoff") is True))
    except publish.CantPublish as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what it needs (the sign-off, gh)
    return {"ok": True, "says": says}


@command("usage-cap")
async def usage_cap(app, client, msg) -> dict:
    """The optional monthly cap on the app's own cloud calls (api_usage, C7): a sum of dollars, or null for none. Kept in
    the usage ledger across restarts. When reached, a cloud call is refused before it is sent, and said."""
    if not hasattr(app, "_usage_cap_lock"):
        app._usage_cap_lock = asyncio.Lock()
    async with app._usage_cap_lock:
        return await _save_usage_cap(app, msg)


async def _save_usage_cap(app, msg) -> dict:
    from research_worker import api_usage

    ledger = api_usage._active
    if ledger is None:
        raise ToolFailed("usage isn't recorded in this copy of the app", next=None)  # no next: this build has no usage ledger
    usd = msg.get("usd")
    before = ledger.cap()
    try:
        await asyncio.to_thread(ledger.set_cap, None if usd is None else float(usd))
    except (TypeError, ValueError) as exc:
        raise ToolFailed("a monthly cap is a sum of dollars, zero or more", next="type a number, or leave it empty") from exc
    from . import activity

    said = "No monthly cap on cloud models" if usd is None else f"Monthly cap on cloud models: ${float(usd):.2f}"
    if usd is not None and (before is None or float(usd) < before):
        stopped = await app.research.reconcile_limit()
        if stopped:
            said += f". {stopped} active researcher(s) stopped; findings kept. Already-sent calls may still be billed"
    activity.activity(app.hub, "setting", said + " (you set it)", state="done")
    return {"ok": True, "says": said, "month": await asyncio.to_thread(ledger.month)}


@command("model-report")
async def model_report(app, client, msg) -> dict:
    """Report it (report.py, C7): the facts of an entry that didn't work, prepared here to read and copy, and the page it
    is reported on. Nothing is sent: no picture, session, key or path is in it."""
    from . import report

    try:
        return {"ok": True, **await asyncio.to_thread(report.prepare, str(msg.get("model") or ""))}
    except KeyError as exc:
        raise ToolFailed("there is no such entry to report", next="reload the Models page") from exc


@command("model-version")
async def model_version(app, client, msg) -> dict:
    """Go back to an earlier proven version of an entry with its own adapter, in one press (C7), or to the newest again
    (`generation` null). Kept across restarts; the worker of the version left is retired (env_runner.choose)."""
    from gpu_worker import manifests as catalog

    from . import env_runner

    m = catalog.get(str(msg.get("model") or ""))
    if m is None or m.runner != "python-env":
        raise ToolFailed("only an entry with its own adapter has versions to go back to", next=None)  # no next: this entry has no separately installed generations
    name = msg.get("generation")
    try:
        found = await asyncio.to_thread(env_runner.choose, m, str(name) if name else None)
    except env_runner.Revoked as exc:  # nothing to install again: the version in use stays, or choose another
        raise ToolFailed(str(exc), next="keep the version in use, or choose another on its card") from exc
    except env_runner.Failed as exc:
        raise ToolFailed(str(exc), next="install it again from its card") from exc
    app.catalog_changed()  # the choice it is now, its earlier worker retired, every studio told
    says = (f"{m.name} runs {found.m.version} now" + (" (your choice)" if name else " (the newest)")) if found else \
        f"{m.name} has no proven version here"
    from . import activity

    activity.activity(app.hub, "model", says, state="done")
    return {"ok": True, "says": says}


@command("github-signin")
async def github_signin(app, client, msg) -> dict:
    """GitHub's device flow for Share with everyone (github_signin.py): `do` status, start (the code to type on GitHub's
    page), poll, or signout. The token stays in the Keychain, never on this reply."""
    from . import github_signin as gh

    do = str(msg.get("do") or "status")
    try:
        if do == "start":
            return {"ok": True, **await asyncio.to_thread(gh.start)}
        if do == "poll":
            return {"ok": True, **await asyncio.to_thread(gh.poll, str(msg.get("id") or ""))}
        if do == "signout":
            return {"ok": True, "signedOut": gh.sign_out(), **gh.status()}
    except (gh.NotSetUp, gh.SignInFailed) as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what works today
    return {"ok": True, **gh.status()}


@command("model-publish-status")
async def model_publish_status(app, client, msg) -> dict:
    """Where a sent capability's pull request stands (publish_api.status): waiting, merged or closed."""
    from . import publish, publish_api

    try:
        return {"ok": True, **await asyncio.to_thread(publish_api.status, str(msg.get("package") or ""))}
    except publish.CantPublish as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the publication-state error names what cannot be read


@command("capability-turn-on-cancel")
async def capability_turn_on_cancel(app, client, msg) -> dict:
    """Stop carrying a Turn on through (a download or an install it started has its own Cancel)."""
    attempt = msg.get('attempt')
    if not isinstance(attempt, str) or not attempt:
        raise ToolFailed('Reopen setup to stop its current attempt', next=None)  # no next: the refusal names the action to take in the guided sheet
    cancelled = await asyncio.to_thread(app.turn_on.cancel, str(msg.get("id") or ""), attempt=attempt)
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "cancelled": cancelled}


@command("catalog-news-seen")
async def catalog_news_seen(app, client, msg) -> dict:
    """"Got it" on New for you (catalog_news.py): the new capabilities (or those named) are no longer new; each stays in
    the catalog with its one step."""
    from . import catalog_news

    keys = msg.get("keys")
    marked = await asyncio.to_thread(catalog_news.seen, [str(k) for k in keys] if isinstance(keys, list) else None)
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "seen": marked}


@command("catalog-news-get")
async def catalog_news_get(app, client, msg) -> dict:
    """Get it, on a new capability only peeked at (catalog_news.py): the reviewed catalog at that same commit, fetched in
    full on this press (its adapters and lockfiles; still nothing installed or run), then its card's one step."""
    from . import catalog_news, channels

    key = str(msg.get("key") or "")
    _, commit, found = await asyncio.to_thread(catalog_news.peeked)
    if key not in found or not commit:
        raise ToolFailed("that capability isn't waiting to be got", next="reload the Models page")
    if msg.get('commit') != commit or msg.get('offer') != catalog_news.fingerprint(found[key]):
        raise ToolFailed('The catalog offer changed; review it again before Get it', next='reload the Models page')
    try:
        r = await app.update_catalog(by="you", commit=commit)
    except channels.CantDo as exc:
        raise ToolFailed(app.channel_update["says"], next="check this Mac is online, then try again") from exc
    return {"ok": True, "key": key, **r}


@command("catalog-news-dismiss")
async def catalog_news_dismiss(app, client, msg) -> dict:
    """"Not for me" on a new capability: never offered again (it stays on the Models page)."""
    from gpu_worker import manifests as catalog

    from . import catalog_news

    key = str(msg.get("key") or "")
    if catalog.get(key) is None and key not in (await asyncio.to_thread(catalog_news.peeked))[2]:
        raise ToolFailed("there is no capability of that name in the catalog", next="reload the Models page")
    await asyncio.to_thread(catalog_news.dismiss, key)
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "key": key}


@command("made-with-acquire")
async def made_with_acquire(app, client, msg) -> dict:
    from . import made_with
    asset = await asyncio.to_thread(app.hub.library.get, str(msg.get("asset") or ""))
    if asset is None:
        raise ToolFailed("that shared result is no longer in your assets", next="open the shared result again")
    made = await asyncio.to_thread(made_with.of_asset, asset.path)
    entry = next((c for c in made if c["key"] == msg.get("key") and (c.get("acquire") or c.get("portable"))), None)
    if entry is None:
        raise ToolFailed("that result has no pinned capability reference", next=None)  # no next: the result lacks the valid immutable entry needed for acquisition
    try:
        if entry.get("acquire"):
            got = await asyncio.to_thread(made_with.acquire_reference, entry["acquire"])
        else:
            from . import result_entries
            from .capture import captures
            got = await asyncio.to_thread(result_entries.acquire, asset.path, entry["portable"], captures(app.hub).memory)
    except ValueError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the result lacks the valid immutable entry needed for acquisition
    await app.sync_mine()
    return {"ok": True, **got}


@command("channel-add")
async def channel_add(app, client, msg) -> dict:
    """Add a community channel, pinned at a commit (the newest when none is given): its entries are listed as not
    reviewed here, and none runs until the user allows its code (channel-trust)."""
    from . import activity, channels

    try:
        r = await asyncio.to_thread(channels.add, str(msg.get("url") or ""), str(msg.get("name") or ""),
                                    str(msg.get("commit") or ""))
    except channels.CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what to give
    says = (f"Added the community channel {r['name']} at {r['commit'][:7]}: {r['entries']} entries, not reviewed here. "
            "None runs until you allow its code.")
    activity.activity(app.hub, "command", says)
    app.catalog_changed()
    return {"ok": True, **r, "says": says}


@command("channel-trust")
async def channel_trust(app, client, msg) -> dict:
    """Allow (or no longer allow) code from a community channel (ADR 0021's consent): each of its entries still runs
    only after its own Install and proof; no longer allowed, its choices go and a setting that chose one goes back."""
    from . import activity, channels

    name, allowed = str(msg.get("name") or ""), msg.get("allowed", True) is not False
    try:
        channels.trust(name, allowed)
    except channels.CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say which channel there is
    activity.activity(app.hub, "command", f"You {'allowed' if allowed else 'no longer allow'} code from the community "
                                          f"channel {name}")
    app.catalog_changed()
    return {"ok": True, "name": name, "allowed": allowed}


@command("channel-update")
async def channel_update(app, client, msg) -> dict:
    """Check a community channel for updates (its row's Check for updates): pinned at its default branch's newest
    commit and fetched (channels.update), said with the commit it moved to. Whether its code is allowed is kept; an entry
    installed from it keeps running its version until its update's own Install and proof (C7 Window B, finding 2)."""
    from . import activity, channels

    name = str(msg.get("name") or "")
    if not any(c["name"] == name and not c["reviewed"] for c in channels.wire()):
        raise ToolFailed(f"there is no community channel called {name}", next="reload the Models page")
    try:
        r = await asyncio.to_thread(channels.update, name)
    except channels.CantDo as exc:
        raise ToolFailed(str(exc), next="check this Mac is online, then check again") from exc
    label = channels.label(name)
    if r.get('says'):
        says = r['says']
    elif r["commit"] == r["was"]:
        says = f"Checked {label}: already at its newest commit, {r['commit'][:7]} ({r['entries']} entries)"
    else:
        says = (f"Checked {label}: now at {r['commit'][:7]} (was {r['was'][:7] or 'none'}), {r['entries']} entries"
                + (f"; new: {', '.join(r['added'])}" if r["added"] else "")
                + ". An update of one you installed waits for its own Install")
    activity.activity(app.hub, "command", says)
    app.catalog_changed()
    return {"ok": True, **r, "says": says}


@command("channel-remove")
async def channel_remove(app, client, msg) -> dict:
    """Stop reading a channel; what was installed from it stays until its own Remove."""
    from . import activity, channels

    name = str(msg.get("name") or "")
    try:
        await asyncio.to_thread(channels.remove, name)
    except channels.CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say which channel there is
    activity.activity(app.hub, "command", f"Removed the channel {name}")
    app.catalog_changed()
    return {"ok": True, "name": name}


@command("capability-run")
async def capability_run(app, client, msg) -> dict:
    """Run the exact catalog tool the studio displayed, bound to the session and explicit volume selected there.
    The tool captures Asked before awaiting; a volume never falls back to the newest one on the studio path."""
    from gpu_worker import manifests as catalog
    try:
        from . import kinds
    except ImportError:
        raise ToolFailed("catalog-added capabilities are not available in this build", next=None) from None  # no next: the message states the missing feature or the input to select again
    session = msg.get('session')
    if not isinstance(session, str) or not session or session != getattr(app.day, 'sid', None):
        raise ToolFailed("the session changed; choose the tool's input again", next=None)  # no next: the message states the missing feature or the input to select again
    model = str(msg.get('model') or '')
    from . import env_runner

    manifest = catalog.get(model.partition('#')[0])
    backend = kinds.backends().get(manifest.runner) if manifest is not None else None  # whatever runs it, proven here
    import os

    installed = backend.in_use(manifest, os.environ) if backend is not None else None
    active = installed.m if installed is not None else (env_runner.operational(manifest) if manifest is not None else None)
    if '#' in model:
        active = next((member.m for member in backend.expand(installed) if member.m.key == model), None) if installed else None
    kind = kinds.door_entry(active)
    if kind is None:
        raise ToolFailed("this catalog tool is no longer available; reopen its details", next=None)  # no next: the message states the missing feature or the input to select again
    if msg.get('input') != kind.input:
        raise ToolFailed('this tool’s input changed; reopen its details and select the input again', next=None)  # no next: the message states the missing feature or the input to select again
    args = {'capability': model, 'by': 'user'}
    if 'resource' in msg:
        args['resource'] = msg['resource']
    params = msg.get('params')
    if params is not None:  # its form's values (its parameters), checked as every actor's are (kinds.values_for)
        if not isinstance(params, dict):
            raise ToolFailed("a capability's parameters are its form's fields", next=None)  # no next: the studio's own command format
        args['params'] = params
    if kind.input == 'volume.3d':
        volume = msg.get('volume')
        if not isinstance(volume, str) or not volume.strip():
            raise ToolFailed("select a volume in this session first", next=None)  # no next: the message states the missing feature or the input to select again
        args['volume'] = volume.strip()
    forwarded = {'type': 'action', 'capability': f'catalog:{model}', 'args': args.get('params', {}), 'session': session}
    for key in ('resource', 'volume', 'run', 'workspaceOrigin'):
        if key in msg:
            forwarded[key] = msg[key]
    return await action(app, client, forwarded,
                        capability_input={'volume': args['volume']} if 'volume' in args else None)



def _registrations(app):
    found = getattr(app, "registrations", None)
    if found is None:
        raise ToolFailed("volumes can't be kept in this run", next=None)  # no next: a run without a home for them
    return found


@command("volume-add")
async def volume_add(app, client, msg) -> dict:
    """Add a whole-brain volume (a TIFF or OME-TIFF on this Mac, by its path): kept as it was, what its file states read
    by brainreg's own Python, and what is still to be said of it (registrations.py)."""
    from .registrations import CantDo

    regs = _registrations(app)
    session = regs.session()
    thread = app.day.active_id if app.day is not None else None
    try:
        record = await asyncio.to_thread(regs.add_volume, str(msg.get("path") or ""), name=str(msg.get("name") or ""),
                                         thread=thread, session=session)
    except CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what to give or install
    return {"ok": True, "volume": record, "missing": regs.missing(record), "says": regs.words(record)}


@command("volume-describe")
async def volume_describe(app, client, msg) -> dict:
    """What the user says of a volume: its voxel sizes (µm, z y x), its orientation (BrainGlobe's three letters), the
    series, channel and time point to register; each kept as theirs with when."""
    from .registrations import CantDo

    regs = _registrations(app)
    session = regs.session()
    if msg.get('session') is not None and msg['session'] != session:
        raise ToolFailed('the session changed; reopen the volume before changing its facts', next=None)  # no next: the message says to reopen the volume in the current session
    try:
        record = regs.describe(str(msg.get("id") or ""), voxel_um=msg.get("voxelUm"), orientation=msg.get("orientation"),
                               series=msg.get("series"), channel=msg.get("channel"), time_point=msg.get("time"), session=session)
    except (CantDo, ValueError, TypeError) as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what a value takes
    return {"ok": True, "volume": record, "missing": regs.missing(record, orientation=msg.get("registration") is not False), "says": regs.words(record)}


@command("job-start")
async def job_start(app, client, msg) -> dict:
    """Register a volume to the Allen mouse atlas with brainreg (a job of minutes, in its own Python; registrations.py)."""
    from .registrations import CantDo

    regs = _registrations(app)
    session = regs.session()
    thread = app.day.active_id if app.day is not None else None
    try:
        record = await asyncio.to_thread(regs.start, str(msg.get("volume") or ""), by="you",
                                         thread=thread, session=session)
    except CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say what is missing
    return {"ok": True, "job": record}


@command("job-cancel")
async def job_cancel(app, client, msg) -> dict:
    """Stop a registration: the whole job ends; what it had written is kept, marked, never a result."""
    from .registrations import CantDo

    regs = _registrations(app)
    session = msg.get('session') or regs.session()
    try:
        record = await asyncio.to_thread(regs.cancel, str(msg.get("id") or ""), session=session)
    except CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: it isn't running
    return {"ok": True, "job": record}


@command("job-again")
async def job_again(app, client, msg) -> dict:
    """Run an interrupted, failed or cancelled registration again: a new job with the same volume and facts."""
    from .registrations import CantDo

    regs = _registrations(app)
    session = regs.session()
    try:
        record = await asyncio.to_thread(regs.again, str(msg.get("id") or ""), session=session)
    except CantDo as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: its words say why
    return {"ok": True, "job": record}


@command("model-install-cancel")
async def model_install_cancel(app, client, msg) -> dict:
    """Stop an install: the half-made environment is never used, and Install makes it again from the start."""
    row = app.installs.cancel(str(msg.get("model") or ""))
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "install": row}


@command("model-uninstall")
async def model_uninstall(app, client, msg) -> dict:
    """Take a model's environments away (envs.remove): its card says Install again."""
    from gpu_worker import manifests as catalog

    from . import envs, scientific_retention

    m = catalog.get(str(msg.get("model") or ""))
    if m is None or not m.env:
        raise ToolFailed("there is no model with an environment of its own by that name", next=None)  # no next: nothing to remove
    going = next((r for r in app.installs.wire() if r.get("model") == m.key and r.get("state") in ("queued", "installing")), None)
    if going is not None:
        raise ToolFailed(f"{m.name} is being installed", next="press Stop on it first, then Remove")
    try:
        projects = getattr(getattr(app.hub, 'resources', None), 'projects', None)
        if projects is not None:
            await asyncio.to_thread(projects.register_runtime)
        done = await asyncio.to_thread(envs.removal, m)
    except scientific_retention.Retained as exc:
        raise ToolFailed(str(exc), next=exc.next) from exc
    except envs.RemovalIncomplete as exc:
        app.catalog_changed()
        raise ToolFailed(str(exc), next="open Models → Storage and finish the pending removal") from exc
    except envs.Failed as exc:
        raise ToolFailed(str(exc), next="resolve the reported removal requirement, refresh storage and retry Remove") from exc
    app.catalog_changed()  # no longer a choice, and its worker retired (env_runner.register)
    says = None
    if done["removed"]:
        from .capability_space import gain_words, size_words
        says = (f"Removed {m.name}'s installed versions. Their files took up to {size_words(done['filesBytes'])}; "
                f"{gain_words(done['freeBefore'], done['freeAfter'])}. Its entry stays, and Install makes it again. "
                "Your Projects and saved results are kept.")
        activity.activity(app.hub, "model", says, ref={"model": m.key})
    return {"ok": True, "removed": done["removed"], "filesBytes": done["filesBytes"],
            "freedBytes": max(0, done["freeAfter"] - done["freeBefore"]), "says": says}


@command("model-download-cancel")
async def model_download_cancel(app, client, msg) -> dict:
    """Stop a download: what it fetched stays in its staging folder, and the next Download carries on from there."""
    from .downloads import Failed

    try:
        row = app.downloads.cancel(str(msg.get("model") or ""))
    except Failed as exc:
        raise ToolFailed(exc.says, next=None) from exc  # no next: nothing is downloading for it, so nothing to stop
    app.hub.broadcast(app.capabilities_wire())
    return {"ok": True, "download": row}


# --- settings and the blind A/B/C arena -------------------------------------------------------
@command("settings")
async def settings(app, client, msg) -> dict:
    problems = change_settings(app, msg.get("settings") or {}, by="menu")
    return {"ok": not problems, "error": "; ".join(problems) or None, "settings": app.hub.settings.to_wire()}


def change_settings(app, changes: dict, by: str) -> list[str]:
    """The one way settings change from now on: the studio's menus (`settings`) and `set_default` (the brain, by
    voice or chat; Services.change_settings) alike. The model choices are kept (defaults.py), the user's other
    preferences too (day/prefs.py), and what a change needs is started. Returns the problems of what was refused."""
    from . import defaults

    return defaults.change(app.hub, app.day, changes, by, warm=lambda c, p: after_change(app, c, p),
                           publish=app.publish_settings)


def after_change(app, changes: dict, problems: list[str]) -> None:
    """What a settings change needs started (a picture model loads, today's examples are embedded with it, MedSigLIP
    warms) and the preferences it keeps: for a change the user made, and for a kept choice applied again
    (App.resume_defaults)."""
    if "userLeads" in changes:
        app.day.set_pref("userLeads", app.hub.settings.user_leads)  # kept until changed
    wire = app.hub.settings.to_wire()
    for key in learner.PREFS:  # how to explain (learner.py), kept until changed
        if key in changes and wire[key] == changes[key]:
            app.day.set_pref(key, wire[key])
    if "researchCap" in changes and not any("research cap" in p for p in problems):
        app.day.set_pref("researchCap", app.hub.settings.research_cap)  # kept until changed
    if app.hub.settings.taught == "off":
        # Matching the user's labels is off: what it showed goes too (taught.py).
        for obj in [o for o in app.hub.scene.objects.values() if o.taught]:
            app.hub.scene.update(obj.id, taught=None)
    if "embed" in changes and getattr(app, "taught", None) is not None:
        # Another picture model (option EMBED): it loads, and today's taught examples and kept outlines
        # are embedded with it from their crops, in the background (taught.py, seen.py).
        asyncio.ensure_future(app.taught.warm())
        if getattr(app, "seen", None) is not None and app.seen.on():
            app.seen.refresh()
    if "zeroshot" in changes and app.agent.zeroshot is not None:
        asyncio.ensure_future(app.agent.zeroshot.warm())  # MedSigLIP, before a click needs it (zeroshot.py)


@command("scores")
async def scores(app, client, msg) -> dict:
    return {"ok": True, "scores": scoreboard(path=app.arena.votes_log)}


@command("arena")
async def arena(app, client, msg) -> dict:
    problems = app.arena.configure(msg.get("arena") or {})
    app.hub.broadcast({"type": "arena", **app.arena.state()})
    return {"ok": not problems, "error": "; ".join(problems) or None}


@command("vote")
async def vote(app, client, msg) -> dict:
    result = app.arena.vote(str(msg.get("trialId", "")), str(msg.get("winner", "")), str(msg.get("note", "")),
                            by=str(msg.get("by") or "studio"))
    app.hub.broadcast({"type": "verdict", **result})
    app.hub.broadcast({"type": "arena", **app.arena.state()})
    return {"ok": True, **result}


# --- the view ---------------------------------------------------------------------------------
@command("population-correct")
async def population_correct(app, client, msg) -> dict:
    """Correct the exact retained revision named by the Studio, using the shared tool."""
    from .evidence import evidence_of

    result = evidence_of(app.hub).get(str(msg.get("population") or ""))
    revision, change = msg.get("revision"), msg.get("change")
    if msg.get("session") != app.day.sid or result is None or result.session != app.day.sid:
        raise tools.ToolError("that population is not in the current session", next="open the saved result again")
    if type(revision) is not int or revision != result.revision:
        raise tools.ToolError("the population revision changed", next="review its current members, then try again")
    if change not in ("remove", "undo"):
        raise tools.ToolError("choose Remove member or Undo correction", next=None)  # no next: the refusal names the available controls
    member = str(msg.get("member") or "")
    question = (f"Remove member {member} from {result.id} revision {revision}" if change == "remove" else
                f"Undo correction in {result.id} revision {revision}")
    return await run_tool(app, "correct_cells", {"of": result.id, "revision": revision, "session": app.day.sid,
        "change": change, "members": [member] if change == "remove" else []}, question=question)


@command("cells")
async def cells(app, client, msg) -> dict:
    from .ticket import issue, using

    request = issue(app.hub, "command", channel="studio")
    try:
        with using(request):
            population, shown = await tools.outline_cells(app.hub)
            return {"ok": True, "count": len(population.live()), "population": population.id, "shown": shown}
    finally:
        request.seal()


@command("clear")
async def clear(app, client, msg) -> dict:
    from .population import layer_of

    kind = msg.get("kind")
    removed = tools.clear(app.hub, kind)
    if kind in (None, "cell"):
        layer = layer_of(app.hub)
        if layer.population() is not None:
            removed += len(layer.population().live())
        layer.hide()  # the population stays as evidence; only the layer goes
    return {"ok": True, "removed": removed}


@command("remove")
async def remove(app, client, msg) -> dict:
    return {"ok": app.hub.scene.remove(msg["id"])}


@command("restore")
async def restore(app, client, msg) -> dict:
    """The user took back their removal of an outline (the studio's Undo): it is back as it was, while still held."""
    obj = app.hub.scene.restore(str(msg.get("id") or ""))
    if obj is None:
        return {"ok": False, "error": "that outline can no longer be brought back"}
    return {"ok": True, "id": obj.id}


@command("select")
async def select(app, client, msg) -> dict:
    ok = app.hub.scene.select(msg.get("id"))
    if msg.get("id"):
        app.magnifier.follow(msg["id"])
    return {"ok": ok}


@command("focus")
async def focus(app, client, msg) -> dict:
    """The user lingered on an object: its details are wanted next (orchestrator/enrich.py)."""
    app.hub.focused = msg.get("id")
    return {"ok": True}


@command("flag")
async def flag(app, client, msg) -> dict:
    """A moment the user flagged (F in the studio, during a demo): logged with the time, the frame
    and what was on screen, to line up with the recordings later (scripts/demo.sh)."""
    info = app.hub.latest_info
    app.agent.log({"kind": "flag", "note": str(msg.get("note") or "")[:300], "frameId": msg.get("frameId"),
                   "latestFrame": info.id if info else None,
                   "outlines": [o.label for o in app.hub.scene.objects.values() if o.kind == "highlight"][:20],
                   "magnifier": app.magnifier.open, "presenter": app.presenter.open})
    return {"ok": True}


@command("card-open")
async def card_open(app, client, msg) -> dict:
    """The user opened a thing's details: logged, so description use can be measured."""
    obj = app.hub.scene.objects.get(str(msg.get("id") or ""))
    entry = app.enrich.entries.get(label_key(obj.label)) if obj is not None and obj.label else None
    app.agent.log({"kind": "card", "id": msg.get("id"), "label": obj.label if obj else None,
                   "described": bool(entry and entry.get("status") == "ready")})
    return {"ok": True}


@command("reveal")
async def reveal(app, client, msg) -> dict:
    """A description is being shown: fetch its reference pictures (once)."""
    asyncio.create_task(app.enrich.reveal(str(msg.get("key") or "")))
    return {"ok": True}


@command("pin-note")
async def pin_note(app, client, msg) -> dict:
    """Pin a researcher's note under an object's label (or unpin with text null)."""
    obj = app.hub.scene.update(str(msg.get("id")), pinned=(str(msg["text"])[:160] if msg.get("text") else None))
    return {"ok": obj is not None}


@command("scale")
async def scale(app, client, msg) -> dict:
    """The studio's objective picker (orchestrator/scale.py): sizes and a scale bar follow."""
    raw = msg.get("objective")
    try:
        app.hub.scale.set_objective(int(raw) if raw not in (None, "", 0) else None, msg.get("fieldNumber"), via="studio")
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@command("timing")
async def timing(app, client, msg) -> dict:
    """The studio's camera-to-screen times per stage for the last stretch of frames
    (apps/studio/src/frametiming.ts); logged for make day-report."""
    stages = msg.get("stages") if isinstance(msg.get("stages"), dict) else {}
    clean = {str(k)[:20]: {q: v.get(q) for q in ("p50", "p90") if isinstance(v, dict) and isinstance(v.get(q), (int, float))}
             for k, v in list(stages.items())[:6]}
    app.agent.log({"kind": "frame-timing", "frames": int(msg.get("frames") or 0), "stages": clean})
    return {"ok": True}


@command("debug")
async def debug(app, client, msg) -> dict:
    client.show_candidates = bool(msg.get("candidates"))
    return {"ok": True}


@command("magnifier")
async def magnifier(app, client, msg) -> dict:
    """The studio flipped the magnifier's source (dots), asked for a zoom, changed the level
    (wheel, pinch, + and -) or closed it."""
    m, action, reply = app.magnifier, msg.get("action"), {"ok": True}
    if action == "close":
        m.close()
    elif action == "lost" and m.open and msg.get("id") == m.object_id and m.object_id in app.hub.scene.objects:
        # Its object lost its place on the live view (or left the eyepiece). A cell of a population is shown on the
        # view it was made on (its real pixels, labelled so), not left to fold away (M1.5c review S1; flows 4 and 6).
        # Only for the object the magnifier shows now, and only while it is not placed (re-check N5).
        obj = app.hub.scene.objects[m.object_id]
        made = obj.made_by or {}
        from .brain.toolbox.common import in_view

        # Decided here, from the scene and the frame now, never from what the studio says (re-check SF1): out of
        # the eyepiece when the hub says so, or tracked in this epoch and outside the disc; otherwise its place is
        # lost (another epoch until re-anchored counts as lost, not out of view; re-check 1d76f9f).
        info = app.hub.latest_info
        same_epoch = info is not None and obj.anchor_epoch == getattr(info, "epoch", None)
        out_of_view = obj.track == "out_of_view" or (obj.track == "tracking" and same_epoch and not in_view(obj, info))
        placed = obj.track == "tracking" and same_epoch and in_view(obj, info)
        if not placed and made.get("how") == "measured" and made.get("evidence") and made.get("member"):
            import time as _time

            from .evidence import evidence_of
            from .population import captured_picture

            population = evidence_of(app.hub).get(made["evidence"])
            jpeg = captured_picture(app.hub, population, [made["member"]]) if population is not None else None
            if jpeg is not None:
                when = _time.strftime("%H:%M:%S", _time.localtime(population.view.get("at") or population.made_at))
                where = "it is out of the eyepiece now" if out_of_view else "its place there is lost"
                m.show_picture(jpeg, f"{made['member']} of {population.id} on the view at {when}, real pixels (not the live view: {where})", object_id=obj.id,
                               resume_live=True)
                reply["captured"] = True
    elif action == "zoom" and msg.get("id") in app.hub.scene.objects:
        m.zoom(msg["id"], hold=bool(msg.get("hold")), level=msg.get("level"))
    elif action == "level" and m.open:
        try:
            reply["level"] = m.set_level(msg.get("level"))
        except ValueError as exc:
            raise tools.ToolError(str(exc), next=None) from exc  # no next: the message says what was wrong
    elif action == "index":
        m.set_index(int(msg.get("index", 0)))
    elif action == "image" and str(msg.get("url", "")).startswith("/research/"):
        # A researcher's picture, from the Research tab (only our own files).
        oid = msg.get("id") if msg.get("id") in app.hub.scene.objects else None
        from .media import of_research_file

        kind_of, credit = of_research_file(app.research, str(msg["url"]))  # what the researcher recorded, not the label
        m.show_image(str(msg["url"]), str(msg.get("label") or "")[:120], credit=str(credit or msg.get("credit") or "")[:120],
                     object_id=oid, kind_of=kind_of)
    return reply


@command("presenter")
async def presenter(app, client, msg) -> dict:
    """The user opened, flipped or closed a researcher's work beside the view."""
    action = msg.get("action")
    view = app.presenter.for_client(client) if client is not None else app.presenter
    if action == "close":
        view.close()
    elif action == 'show' and 'savedItem' in msg:
        try:
            view.show_retained_item(msg.get('day'), msg.get('rid'), msg['savedItem'])
        except ValueError as exc:
            return {'ok':False, 'error':str(exc)}
    elif action == 'show' and msg.get('day') and msg.get('outputId'):
        from .research.files import DAY, NAME
        day, rid, output = str(msg['day']), str(msg.get('rid') or ''), str(msg['outputId'])
        version = msg.get('version')
        if not DAY.fullmatch(day) or not NAME.fullmatch(rid) or not NAME.fullmatch(output) or type(version) is not int:
            return {'ok':False, 'error':'invalid saved research identity'}
        try:
            view.show_saved(day, rid, output, version, msg.get('index', 0))
        except KeyError as exc:
            return {'ok':False, 'error':str(exc)}
    elif action == "show" and app.research.get(str(msg.get("rid"))):
        view.show(str(msg["rid"]), msg.get("index"))
    elif action == "remove" and msg.get("rid"):
        app.presenter.remove_from_shelf(str(msg["rid"]))  # off the shelf for the day; still in the Research tab
    elif action == "restore" and msg.get("rid"):
        app.presenter.back_on_shelf(str(msg["rid"]))
    return {"ok": True}


@command("showing")
async def showing(app, client, msg) -> dict:
    """The user stopped a showing (its chip's Stop, or Escape): the brain's own tool, as by voice."""
    return await run_tool(app, "show_me", {"stop": True, "why": "the user's Stop"})


@command("slidemap")
async def slidemap(app, client, msg) -> dict:
    """The user opened or closed the slide map: the brain's own tool, as by voice."""
    return await run_tool(app, "show_map", {"open": msg.get("open") is not False})


# --- conversations ------------------------------------------------------------------------------
@command("thread")
async def thread(app, client, msg) -> dict:
    """The conversation menu: start a new one or go back to one (the brain's own tools, as by
    voice), or rename this one."""
    action = msg.get("action")
    if action == "new":
        done = await run_tool(app, "new_thread", {"title": str(msg.get("title") or "")})
    elif action == "switch":
        done = await run_tool(app, "switch_thread", {"thread": str(msg.get("id") or "")})
    elif action == "rename":
        if "session" in msg and msg["session"] != app.day.sid:
            raise tools.ToolError("This conversation belongs to another Project; its name was not changed.",
                                  next="open that Project and rename its conversation there")
        app.day.rename(str(msg.get("id") or app.day.active_id), str(msg.get("title") or ""))
        done = {"ok": True}
    else:
        raise tools.ToolError(f"no thread action {action!r}",
                              next=None)  # no next: the studio's own command format, never the user's words
    return {**done, "thread": app.day.active_id}


# --- sessions -----------------------------------------------------------------------------------
@command("session")
async def session(app, client, msg) -> dict:
    """The header chip's menu and the sessions rail (ADR 0017): start a session or go back to one (the brain's own
    tools, as by voice), name one (this one, or another in its own journal), or keep working here when the app
    suggests a new one."""
    action = msg.get("action")
    if action in ("new", "switch"):
        await note_position(app)  # the session left keeps where its recording was, to land there when it comes back
    if action == "new":
        done = await run_tool(app, "new_session", {"name": str(msg.get("name") or "")})
    elif action == "switch":
        sid = str(msg.get("id") or "")
        if not sids.is_sid(sid):
            raise tools.ToolError("no such session", next="choose one from the sessions list")
        done = await run_tool(app, "switch_session", {"session": sid})
    elif action == "rename":
        try:
            changed = app.rename_session(str(msg.get("id") or app.day.sid), str(msg.get("name") or ""))
        except KeyError:
            raise tools.ToolError("no such session", next="choose one from the sessions list") from None
        done = {"ok": True, "changed": changed}
    elif action == "keep":
        app.keep_session()
        done = {"ok": True}
    else:
        raise tools.ToolError(f"no session action {action!r}",
                              next=None)  # no next: the studio's own command format, never the user's words
    return {**done, "session": app.day.sid}


@command("view")
async def view(app, client, msg) -> dict:
    """The studio covers the live view with a new session's home (`held` true: session-home.ts), or shows it again
    (`held` false) once the user chose what the session looks at. While held, the running look makes no looks and the
    subject tracker takes none (hub.view_held), so nothing is written into a session before the user chose its source,
    and the brain and the voice are told in one line (brain/context.py). Another session's opening and the last studio
    leaving clear it too. Which feed is shown is the studio's and the bridge's: this says only whether the view is
    covered."""
    held = msg.get("held")
    if not isinstance(held, bool):
        raise tools.ToolError("held is true or false", next=None)  # no next: the studio's own command format, not the user's
    changed = bool(app.hub.view_held) != held
    app.hold_until_landed(held, expires=False)  # the studio's own: it lets go itself
    if changed and not held and getattr(app, "sight", None) is not None:
        app.sight.recheck = True  # the view is seen again: the running look looks soon, not at its next refresh
    return {"ok": True, "held": held, "changed": changed}


# --- datasets -----------------------------------------------------------------------------------
# --- a session's sources (library.py, sources.py; ADR 0022) ---------------------------------------------------
FEED_TIMEOUT_S = 20.0  # a feed switch restarts the bridge's capture (it waits up to 6 s for frames)


async def _bridge(app, method: str, path: str) -> dict:
    """The camera bridge's /feed (the server plays a session's source there; the studio's test footage it switches
    itself). ToolError in words when the bridge can't be reached or refuses."""
    import httpx

    url = f"{app.hub.source.bridge_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=FEED_TIMEOUT_S) as client:
            response = await (client.post(url) if method == "POST" else client.get(url))
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise tools.ToolError(f"the camera bridge could not be reached ({type(exc).__name__})",
                              next="check the camera bridge is running (make status), then try again") from None
    if response.status_code >= 400 or (isinstance(body, dict) and body.get("ok") is False):
        raise tools.ToolError(f"the camera bridge could not play it: {str(body.get('error') or response.status_code)[:200]}",
                              next="pick another source, or check the file is still there")
    return body if isinstance(body, dict) else {}


def staged(app) -> str | None:
    """The asset the server put on stage (`source`), or None: the live camera, or nothing landed yet in this session."""
    meta = getattr(app.hub, "stage_meta", None)
    return meta.get("id") if isinstance(meta, dict) else None


async def note_position(app) -> None:
    """Where the recording on stage is, kept with the session before the stage changes (another source, or another
    session), so coming back lands there. Nothing when the live camera is on stage, or the bridge can't say."""
    from .library import is_asset_id

    aid = staged(app)
    if not is_asset_id(aid):
        return
    try:
        playback = (await _bridge(app, "GET", "/feed")).get("playback") or {}
    except tools.ToolError:
        return
    seconds = playback.get("seconds")
    if isinstance(seconds, (int, float)) and not playback.get("still"):
        app.day.source_left_at(aid, float(seconds))


def _source_lock(app) -> asyncio.Lock:
    """The app's one lock for putting a source on stage (made in the loop that runs it)."""
    loop = asyncio.get_running_loop()
    held = getattr(app, "_source_lock", None)
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Lock())
        app._source_lock = held
    return held[1]


@command("source")
async def source(app, client, msg) -> dict:
    """Put a source on stage (the Sources section, the new-session home, a returning session's landing): one of the
    user's assets, or the live camera ("live"). Opening associates: it is the session's from then on (ADR 0022). The
    bridge plays it, held at `at` when `paused` (a picture is always held), unless `switch` is false: the studio
    switched the feed itself to its test footage, which stands for the live camera, so the stage is "live". `session`
    (a landing's): refused once another session is open, so switching quickly never puts one session's source in
    another."""
    from .library import is_asset_id

    asked = msg.get("session")
    sid = app.day.sid
    if isinstance(asked, str) and asked and asked != sid:
        raise tools.ToolError("that session is no longer open", next=None)  # no next: the studio lands the one open now
    target = str(msg.get("open") or "")
    library = getattr(app.hub, "library", None)
    asset = None
    if target != "live":
        if not is_asset_id(target):
            raise tools.ToolError("open is an asset's id or \"live\"", next=None)  # no next: the studio's own command format
        if library is None:
            raise tools.ToolError("the library is not available here", next=None)  # no next: this app keeps no library (a test stack)
        asset = await asyncio.to_thread(library.get, target)
        if asset is None:
            raise tools.ToolError(f"there is no asset {target}", next="pick one from this session's Assets or from All assets")
    # One at a time, in the order asked, so the bridge ends on the last one (switching sessions quickly sends one landing
    # each: run together, a slow seek of the first finished last). One asked in a session left while it waited, or while
    # the bridge switched, is not this session's (its own landing follows).
    async with _source_lock(app):
        if app.day.sid != sid:
            raise tools.ToolError("that session is no longer open", next=None)  # no next: the studio lands the one open now
        if msg.get("switch") is not False:
            await note_position(app)
            if asset is None:
                await _bridge(app, "POST", "/feed?source=camera")
            else:
                from urllib.parse import urlencode

                at = msg.get("at")
                params = {"source": "asset", "path": str(asset.path), "title": asset.title_now(),
                          **({"paused": "1"} if msg.get("paused") or asset.kind == "still" else {}),
                          **({"at": f"{float(at):.3f}"} if isinstance(at, (int, float)) and at >= 0 else {})}
                await _bridge(app, "POST", "/feed?" + urlencode(params))
        if app.day.sid != sid:
            raise tools.ToolError("that session is no longer open", next=None)  # no next: the studio lands the one open now
        previous = app.day.on_stage
        app.day.open_source(target)
        await app.stage_changed(asset)
        app.hold_until_landed(False)  # its source is on stage: it is looked at again (a session's opening held it)
    return {"ok": True, "onStage": target, "previous": previous, **({"title": asset.title_now()} if asset else {})}


@command("source-remove")
async def source_remove(app, client, msg) -> dict:
    """The user's × on a source: out of the session (the library keeps it; what was made from it stays the session's
    history; its specimens leave the session's list unless another source shows them). Undo is `source-restore`. When
    it was on stage, the studio puts `next` there (the newest other source, else the live camera)."""
    aid = str(msg.get("asset") or "")
    library = getattr(app.hub, "library", None)
    asset = await asyncio.to_thread(library.get, aid) if library is not None else None
    if not app.day.remove_source(aid):
        raise tools.ToolError("that is not one of this session's sources" if aid != "live" else "the live camera is always a source",
                              next=None)  # no next: the studio offers × only on the session's own assets
    on_stage = staged(app) == aid
    if on_stage:
        await app.stage_changed(None)  # its scale and specimen no longer apply; the studio puts `next` on stage
    listed = await app.session_sources(app.day.sid)
    following = next((row["id"] for row in listed["sources"] if row["id"] != aid), "live")
    app.broadcast_sources(listed)
    return {"ok": True, "removed": aid, "wasOnStage": on_stage, **({"next": following} if on_stage else {}),
            **({"title": asset.title_now()} if asset else {})}


@command("source-restore")
async def source_restore(app, client, msg) -> dict:
    """Undo of a source's removal: it is the session's again, with its specimens."""
    aid = str(msg.get("asset") or "")
    if not app.day.restore_source(aid):
        raise tools.ToolError("that source was not removed from this session", next=None)  # no next: Undo is offered only right after a removal
    app.broadcast_sources(await app.session_sources(app.day.sid))
    library = getattr(app.hub, "library", None)
    asset = await asyncio.to_thread(library.get, aid) if library is not None else None
    return {"ok": True, "restored": aid, **({"title": asset.title_now()} if asset else {})}


@command("asset-meta")
async def asset_meta(app, client, msg) -> dict:
    """The user's own fields of an asset (its specimen, magnification, title, notes): kept on it in the library, so they
    follow it into every session. On stage, its scale and its specimen change at once."""
    library = getattr(app.hub, "library", None)
    if library is None:
        raise tools.ToolError("the library is not available here", next=None)  # no next: this app keeps no library (a test stack)
    aid, fields = str(msg.get("asset") or ""), msg.get("set")
    if not isinstance(fields, dict):
        raise tools.ToolError("set is the fields to change", next=None)  # no next: the studio's own command format
    try:
        now = await asyncio.to_thread(library.set_meta, aid, fields, "user")
    except ValueError as exc:
        raise tools.ToolError(str(exc), next="change the value and try again") from None
    if staged(app) == aid:
        await app.stage_changed(await asyncio.to_thread(library.get, aid), fields=set(fields))
    app.broadcast_sources(await app.session_sources(app.day.sid))
    asset = await asyncio.to_thread(library.get, aid)
    return {"ok": True, "asset": aid, "fields": now, **({"title": asset.title_now()} if asset else {})}


@command("asset-trash")
async def asset_trash(app, client, msg) -> dict:
    """All assets' Move to trash (the user's, after a confirm naming the sessions that use it): out of the library and
    every session's list; the work made from it stays; a file the library holds goes to a trash folder, never deleted.
    When it was on stage here, it leaves the stage (`wasOnStage`: the studio goes live). Undo is `asset-restore`."""
    library = getattr(app.hub, "library", None)
    if library is None:
        raise tools.ToolError("the library is not available here", next=None)  # no next: this app keeps no library (a test stack)
    aid = str(msg.get("asset") or "")
    try:
        done = await asyncio.to_thread(library.trash, aid)
    except (ValueError, OSError) as exc:
        raise tools.ToolError(str(exc), next="reload All assets") from None
    on_stage = staged(app) == aid
    if on_stage:
        await app.stage_changed(None)
    app.broadcast_sources(await app.session_sources(app.day.sid))
    return {"ok": True, "trashed": aid, "wasOnStage": on_stage, "title": done["title"]}


@command("asset-restore")
async def asset_restore(app, client, msg) -> dict:
    """Undo of Move to trash: back in the library and in every session that had it."""
    library = getattr(app.hub, "library", None)
    if library is None:
        raise tools.ToolError("the library is not available here", next=None)  # no next: this app keeps no library (a test stack)
    try:
        done = await asyncio.to_thread(library.restore, str(msg.get("asset") or ""))
    except (ValueError, OSError) as exc:
        raise tools.ToolError(str(exc), next=None) from None  # no next: Undo is offered only right after the trash
    app.broadcast_sources(await app.session_sources(app.day.sid))
    return {"ok": True, "restored": done["id"], "title": done["title"]}


@command("dataset")
async def dataset(app, client, msg) -> dict:
    """Record: the user's Record starts a dataset and its Stop ends it (the studio sends this; ADR 0009).
    No tool does this: a misheard phrase must never start or end one (datasets/collector.py)."""
    if app.datasets is None:
        raise tools.ToolError("datasets are not available here", next=None)  # no next: this app keeps no datasets (a test stack)
    before = app.datasets.wire().get("id")
    wire = app.datasets.start() if msg.get("on") else app.datasets.stop()
    if msg.get("on") and wire.get("id") and wire["id"] != before:
        app.day.made("dataset", str(wire["id"]))  # the session's (its journal claims it): its camera video is a source
    return {"ok": True, "dataset": wire}


@command("dataset-rename")
async def dataset_rename(app, client, msg) -> dict:
    """The Datasets tab: the user names a dataset (appended to its events; nothing recorded changes)."""
    if app.datasets is None or not app.datasets.rename(str(msg.get("id") or ""), str(msg.get("name") or "")):
        raise tools.ToolError("no such dataset", next="reload the Datasets tab")
    return {"ok": True}


@command("rig")
async def rig(app, client, msg) -> dict:
    """The user enters a fact about their microscope (the Datasets tab). A field number given is the
    one the scale uses from now on; a dataset collecting gets it as entered after its start."""
    if app.rig is None:
        raise tools.ToolError("microscope facts are not available here",
                              next=None)  # no next: this app keeps no datasets (a test stack)
    field, value = str(msg.get("field") or ""), msg.get("value")
    try:
        fact = app.rig.set(field, value)
        if field == "fieldNumber" and fact.get("value") is not None:
            app.hub.scale.set_field_number(fact["value"], via="microscope facts")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if app.datasets is not None:
        app.datasets.rig_fact(field, fact)
    app.hub.broadcast(app.rig.wire())
    return {"ok": True, "fact": fact}


@command("dataset-trash")
async def dataset_trash(app, client, msg) -> dict:
    """The Datasets tab's Move to trash (after the user confirmed it): the dataset goes to datasets/.trash/
    whole; never one being collected."""
    from .datasets import store

    try:
        target = store.trash(app.paths.datasets, str(msg.get("id") or ""))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": str(target)}


@command("dataset-reveal")
async def dataset_reveal(app, client, msg) -> dict:
    """The Datasets tab: show a dataset's folder in the Finder (this Mac's own app)."""
    import subprocess
    import sys

    from .datasets import store

    folder = store.find(app.paths.datasets, str(msg.get("id") or "")) if app.datasets is not None else None
    if folder is None:
        raise tools.ToolError("no such dataset", next="reload the Datasets tab")
    if sys.platform != "darwin":
        return {"ok": False, "error": f"the folder is {folder}", "path": str(folder)}
    subprocess.Popen(["open", "-R", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(folder)}


@command("sessions-reveal")
async def sessions_reveal(app, client, msg) -> dict:
    """The Projects page's Show in Finder: the folder every Project is saved in (`app.paths.sessions`, the sessions
    wire's `root`), shown in the Finder as dataset-reveal shows a dataset's (thesis question 12)."""
    import subprocess
    import sys

    folder = app.paths.sessions
    if sys.platform != "darwin":
        return {"ok": False, "error": f"the folder is {folder}", "path": str(folder)}
    subprocess.Popen(["open", "-R", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(folder)}


# --- researchers --------------------------------------------------------------------------------
@command('research-open-capture')
async def research_open_capture(app, client, msg) -> dict:
    """Open one exact retained frame; optionally link it to its requesting report."""
    from .research.captures import resolve
    from .workspace_observation import ObservationError
    from .presentations import PresentationError

    selected_keys = {'day', 'rid', 'findingVersion', 'findingDigest', 'sequence', 'picture'}
    allowed = selected_keys | {'type', 'requestId', 'actionId', 'parentInstance', 'client', 'appRunId', 'screen'}
    try:
        if set(msg) - allowed or not selected_keys <= msg.keys():
            raise ObservationError('INVALID_MANUAL', 'capture selection needs its exact retained findings address')
        # Reject a different/unaccepted browser before resolving private evidence.
        cursor = app.manual_commands._origin(client, msg.get('client'), msg.get('appRunId'))
        if any(not isinstance(msg.get(key), str) or not 1 <= len(msg[key]) <= 256 for key in ('requestId', 'actionId')):
            raise ObservationError('INVALID_MANUAL', 'capture control needs its original bounded request and action identity')
        body = json.dumps(msg, sort_keys=True, allow_nan=False, separators=(',', ':'))
        if len(body.encode()) > NativeManualCommands.BODY_BYTES:
            raise ObservationError('INVALID_MANUAL', 'capture selection exceeds its bounded allowance')
        replayed = await app.manual_commands.replay(cursor, msg['requestId'], body)
        if replayed is not None:
            return replayed
        parent = msg.get('parentInstance')
        if parent is not None and (not isinstance(parent, str) or not 1 <= len(parent) <= 256):
            raise ObservationError('INVALID_MANUAL', 'the requesting report needs its exact window identity')
        target = json.loads(json.dumps({key: msg[key] for key in selected_keys}, allow_nan=False))
        resource = None
        async def resolve_capture():
            nonlocal resource
            resource = await asyncio.to_thread(resolve, app.research, app.hub.resources, target)
            return resource
        async def recheck():
            try:
                current = await asyncio.to_thread(resolve, app.research, app.hub.resources, target)
                if current != resource:
                    raise ResearchRefused('the retained capture address changed while opening it', next='reopen the exact saved findings')
            except ResearchRefused as exc:
                raise ToolFailed(str(exc), next=exc.next) from exc
        packet = {key: msg[key] for key in ('requestId', 'actionId', 'client', 'appRunId', 'screen') if key in msg}
        packet.update(type='present', do='open', title='Retained research capture')
        args, _, sequence, base = _manual_proposal(packet)
        return await app.manual_commands.submit(client, packet, args, body, sequence, base,
            capture_parent={'id': parent, **target} if parent else None, prepared_source_check=recheck,
            resolve_capture=resolve_capture, reply_for='research-open-capture')
    except (ResearchRefused, ObservationError, PresentationError) as exc:
        return {'ok': False, 'error': str(exc), **({'code': exc.code} if hasattr(exc, 'code') else {})}


@command("accept-correction", "dismiss-correction")
async def correction(app, client, msg) -> dict:
    return app.announcer.decided(str(msg.get("id") or ""), msg.get("type") == "accept-correction")


@command("research-followup")
async def research_followup(app, client, msg) -> dict:
    import uuid
    from .research.selection import validate

    try:
        rid, text = str(msg.get('rid') or ''), str(msg.get('text') or '').strip()
        session = app.research.get(rid)
        if session is None or not text:
            raise ResearchRefused('no researcher or follow-up text was supplied', next='open a researcher and enter your follow-up')
        target = validate(session, msg.get('selection')) or {'day': session.day, 'rid': session.rid}
        previous = next((job for job in session.interaction_jobs if job['id'] == msg.get('requestId')), None)
        if 'presentationBase' not in target:
            target['presentationBase'] = (previous['request'].get('selection', {}).get('presentationBase') if previous
                                          else (app.research.presentations.current(session) or {}).get('id'))
        if not target.get('findingVersion'):
            if previous:
                target = {**target, **{key:value for key,value in previous['request'].get('selection', {}).items()
                                      if key in ('findingVersion', 'findingDigest')}}
            else:
                basis = (session.result or {}).get('findingRevision')
                if basis:
                    target = {**target, 'findingVersion':basis['version'], 'findingDigest':basis['digest']}
        payload = {'text': text, 'selection': target, 'kind': 'followup'}
        if 'expectedBase' in msg:
            payload['expectedBase'] = msg['expectedBase']
        async def dispatch():
            # Which researcher and which of its versions is context for the model only: the chat, the conversation
            # and Activity keep the user's own words (`said`).
            context = f'In the conversation with {session.name} (researcher {rid}): '
            if target:
                context += f"[Selected retained artifact: {json.dumps(target, sort_keys=True)}] "
            return await ask(app, client, {**msg, 'type': 'ask', 'text': context + text}, research_selection=target, said=text)
        return await app.research.interact(rid, str(msg.get('requestId') or uuid.uuid4()), payload, dispatch)
    except ResearchRefused as exc:
        return {"ok": False, "error": str(exc)}


@command("research-note")
async def research_note(app, client, msg) -> dict:
    import uuid
    from .research.selection import validate
    try:
        rid, text = str(msg.get('rid') or ''), str(msg.get('text') or '').strip()
        session = app.research.get(rid)
        if session is None or not text:
            raise ResearchRefused('no researcher or note text was supplied', next='open a researcher and enter your note')
        target = validate(session, msg.get('selection')) or {'day':session.day, 'rid':session.rid}
        async def dispatch():
            session = app.research.note(rid, text)
            return {'ok': True, 'rid': session.rid}
        return await app.research.interact(rid, str(msg.get('requestId') or uuid.uuid4()),
                                           {'text': text, 'kind': 'note', 'selection':target}, dispatch)
    except ResearchRefused as exc:
        return {"ok": False, "error": str(exc)}


@command("research-cancel")
async def research_cancel(app, client, msg) -> dict:
    return {"ok": await app.research.cancel(str(msg.get("rid") or ""))}


@command('research-evidence')
async def research_evidence(app, client, msg) -> dict:
    """Explicit evidence buttons use the existing tool, with durable delivery identity."""
    try:
        session = app.research.get(str(msg.get('rid') or ''))
        if session is None or session.day != msg.get('day') or session.day != app.research.today():
            raise ResearchRefused('this evidence question is not writable today', next='open today’s researcher')
        choice = msg.get('choice')
        if choice not in ('hold', 'decline'):
            raise ResearchRefused('invalid evidence response', next='choose Hold or Not now')
        payload = {'kind':'evidence', 'question':msg.get('questionId'), 'run':msg.get('run'), 'choice':choice,
                   'selection':{'day':session.day, 'rid':session.rid}, 'text':'Hold requested footage' if choice == 'hold' else 'Not now'}
        async def dispatch():
            request = next((q for q in session.requests if q.get('id') == msg.get('questionId')
                            and q.get('run', 0) == msg.get('run', 0) and q.get('status') == 'asked'), None)
            if request is None:
                return {'ok':False, 'error':'That evidence question is no longer waiting for an answer.'}
            seconds = request.get('seconds') if choice == 'hold' else 0
            if choice == 'hold' and not seconds:
                return {'ok':False, 'error':'This question did not request footage; answer it in the conversation.'}
            text = f'Hold {seconds:g} seconds for this question' if choice == 'hold' else 'Not now; continue without this evidence'
            return await run_tool(app, 'researcher_note', {'researcher':session.rid, 'answers':request['id'],
                'text':text, 'hold_seconds':seconds, 'asked_with':text}, question=text)
        return await app.research.interact(session.rid, str(msg.get('requestId') or ''), payload, dispatch)
    except ResearchRefused as exc:
        return {'ok':False, 'error':str(exc)}


@command('research-presentation')
async def research_presentation(app, client, msg) -> dict:
    """An explicit presentation action consumes retained findings, never research rounds."""
    try:
        rid = str(msg.get('rid') or '')
        if msg.get('action') == 'cancel':
            return {'ok': await app.research.presentations.cancel(rid, str(msg.get('jobId') or ''))}
        if msg.get('action') == 'save':
            saved = app.research.presentations.recover(rid, str(msg.get('jobId') or ''))
            return {'ok': saved, **({} if saved else {'error':'The presentation outcome could not be saved; restore storage and try saving again'})}
        if msg.get('action') != 'produce':
            return {'ok': False, 'error': 'unknown presentation action'}
        job = app.research.presentations.begin(rid, msg.get('requestId'), version=msg.get('findingVersion'),
            digest=msg.get('findingDigest'), model=msg.get('model'), change=str(msg.get('change') or ''),
            expected_base=msg.get('expectedBase'), kind=msg.get('contentKind', 'report'))
        return {'ok': True, 'job': job}
    except (ResearchRefused, OSError) as exc:
        return {'ok': False, 'error': str(exc)}


# --- what the studio shows (screen.py; contract 10) -------------------------------------------------
@command("screen")
async def screen(app, client, msg) -> dict:
    """The studio's report of what it displays, the changes it has applied, and the user's own changes
    there (a drag, a resize, a toggle): an open, close, move or style change is done only once a report
    shows it."""
    from .screen import screen_of
    from .workspace_observation import ObservationError

    try:
        screen_of(app.hub).report(msg, client=client)
        transport = getattr(app, "workspace_transport", None)
        if transport is not None:
            transport.save(client)
    except ObservationError as exc:
        correlation = getattr(exc, "screen_report", None)
        return {"ok": False, "code": exc.code, "error": str(exc),
                **({"screenReport": correlation} if correlation is not None else {})}
    return {"ok": True}


@command("present")
async def present(app, client, msg) -> dict:
    """The studio's own controls (the cells' layer: show, hide, fade, colour; opening something made
    today): the same verbs the brain uses, done as the user's change and only once the screen shows it."""
    from .brain.toolbox.present import act
    from .workspace_observation import ObservationError

    # Made today's original wire addresses a retained resource, not the old
    # singleton magnifier. Resolve once, then use the same accepted-socket
    # admission/Runner/render-confirmation path as native manual controls.
    # Population and current tracks remain layers, handled below.
    if (msg.get("do") == "open" and isinstance(msg.get("what"), str)
            and not any(key in msg for key in ("schema", "source", "instance", "targets"))):
        from .resources import ENTRY_ID
        from uuid import uuid4
        resource_id = msg["what"].strip()
        if ENTRY_ID.fullmatch(resource_id):
            day = app.hub.resources.session()  # the session open now (ADR 0017)
            entry = await asyncio.to_thread(app.hub.resources.get, resource_id, day)
            if entry is not None and (entry.kind == "research" or entry.pictures and entry.kind not in ("population", "motion")):
                msg = {"type": "present", "do": "open", "title": entry.title,
                       "source": {"day": entry.day, "id": entry.id, "version": entry.version,
                                  "picture": None if entry.kind == "research" else 0},
                       "requestId": msg.get("requestId") or str(uuid4()), "actionId": str(uuid4()),
                       **({"screen": msg["screen"]} if "screen" in msg else {})}

    if ("schema" in msg or "source" in msg or "instance" in msg or "targets" in msg
            or isinstance(msg.get("what"), dict)):
        try:
            args, body, sequence, base = _manual_proposal(msg)
            return await app.manual_commands.submit(client, msg, args, body, sequence, base)
        except ObservationError as exc:
            return {"ok": False, "code": exc.code, "error": str(exc)}

    args = {k: msg[k] for k in ("what", "do", "value", "by", "top", "order", "rest", "rect", "revision") if k in msg}
    if client in getattr(getattr(getattr(app, 'hub', None), 'screen', None), '_accepted', {}):
        from .ticket import issue, using
        request = issue(app.hub, 'command', asked=app.conversation.here(), channel='studio')
        try:
            pin = app.hub.screen.pin(client, request_id=request.admission.request_id,
                                     inline=msg.get('screen'), timing='server-receive-legacy')
            info = app.hub.latest_info
            ctx = ToolContext('studio', 0, info.id if info else None, 'Manual retained-work control',
                              asked=request.asked, admission=request.admission, actor='you',
                              request_id=request.admission.request_id, client=client,
                              request_snapshot=pin, board=app.brain.board)
            with using(request):
                run = app.brain.registry.start('present', args, ctx)
                if run.task is not None:
                    request.track(run.task)
                    await run.task
                return {'ok': bool(run.ok), 'summary': run.summary}
        finally:
            request.seal()
    return {"ok": True, "summary": await act(app.brain.services, args, by="user")}


@command("screen-image")
async def screen_image(app, client, msg) -> dict:
    """The studio's picture of its composed workspace, asked for by look_at_screen (never specimen data)."""
    import base64

    from .screen import screen_of
    from .workspace_observation import ObservationError

    owner = screen_of(app.hub)
    if "client" in msg or "lookId" in msg or "originSnapshotId" in msg:
        try:
            await owner.looked_workspace(client, msg)
        except ObservationError as exc:
            return {"ok": False, "code": exc.code, "error": str(exc)}
        return {"ok": True}

    try:
        jpeg = base64.b64decode(str(msg.get("jpeg") or ""), validate=True)
    except ValueError:
        return {"ok": False, "error": "that picture could not be read"}
    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 6 * 1024 * 1024:
        return {"ok": False, "error": "a screen picture is a JPEG of at most 6 MB"}
    owner.looked(str(msg.get("id") or ""), jpeg)  # legacy approximate-compositor reply only
    return {"ok": True}


# --- tests and the scenario harness ---------------------------------------------------------------
@command("gpu-test")
async def gpu_test(app, client, msg) -> dict:
    """For the scenario harness (sim `model-load`): hold a GPU worker as a model load does for `seconds` (after
    `sleep` s of waiting), at that worker's priority and thread cap; or `unload` one model there. Not a user
    feature, as `tool` is not."""
    from .operations import started, worker_client

    gpu = worker_client(app.hub, str(msg.get("worker") or "analysis"), model=str(msg["unload"]) if msg.get("unload") else None)
    if gpu is None:
        return {"ok": False, "error": "no GPU worker"}
    if msg.get("unload") and getattr(gpu, "own", None) and not gpu.ready:
        return {"ok": True, "unloaded": []}  # a model's own worker never started: nothing to let go of
    gpu = await started(gpu)
    if msg.get("unload"):
        loads = getattr(app.hub, "loads", None)
        reply = (await loads.let_go(gpu, str(msg["unload"])) if loads is not None
                 else await gpu.call("unload", timeout=60.0, priority="interactive", model=str(msg["unload"])))
        return {"ok": True, "unloaded": reply.get("unloaded", [])}
    seconds, sleep = float(msg.get("seconds") or 5.0), float(msg.get("sleep") or 0.0)
    reply = await gpu.call("loadtest", timeout=seconds + sleep + 30.0, priority="background", seconds=seconds,
                           sleep=sleep, spin=msg.get("spin"))
    return {"ok": True, "threads": reply.get("threads"), "ms": reply.get("ms")}


@command("tool")
async def tool(app, client, msg) -> dict:
    """Run one brain tool directly (the brain normally chooses tools itself); with `fake`, a
    scripted researcher (no network)."""
    if msg.get("fake") is not None:
        session = await app.research.start(str(msg.get("brief") or "test"), effort=str(msg.get("effort") or "quick"),
                                           deliverable=str(msg.get("deliverable") or "research"),
                                           object_id=msg.get("object"), fake=dict(msg["fake"]), origin={"channel": "chat"})
        return {"ok": True, "rid": session.rid, "name": session.name}
    return await run_tool(app, str(msg.get("name")), msg.get("args") or {})


@command("onboarding")
async def onboarding(app, client, msg) -> dict:
    """An optional real example, with progress kept in this local profile."""
    from .onboarding import of
    return await of(app).command(client, msg)


@command("onboarding-models")
async def onboarding_models(app, client, msg) -> dict:
    """The small-models offer (onboarding.models_offer): `status`, or the answer `not-now` or `download` (with the models
    chosen). It keeps the answer only; the downloads are the model-download and model-turn-on commands, in their queue."""
    from .onboarding import answer_models_offer, models_offer
    action = str(msg.get("action") or "status")
    if action == "status":
        offer = await asyncio.to_thread(models_offer, app.paths.home)
    else:
        offer = await asyncio.to_thread(answer_models_offer, app.paths.home, action, msg.get("models") or [])
    return {"ok": True, "offer": offer}


from .maintenance import commands as _maintenance  # noqa: E402,F401 - registers the maintenance-* commands (ADR 0025)
