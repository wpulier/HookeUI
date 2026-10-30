"""A researcher's hands: running the app's own operations for it (the partner §2.2, "Hands: run"; Amendment 1 A).

A researcher reads the session with its `session` tool; with `run` it asks the app to do one of its own
operations for its own reading, and reads what that made the same way.

- What it can run (research_worker.budget.RUN_OPERATIONS):
  - "segment": every cell in a view segmented by the cell finder and measured, kept as a population entry of the
    session's index (e…), made for it;
  - "enhance": a clearer picture from real frames (enhance.py: the average of still frames, or the sharpest one;
    a focus stack only of the user's own last turn of the focus knob), kept as a result entry with its picture
    (d…), made for it;
  - "classify" (Part 2.5, W5): two to six classifying models (operation `classify`: the vision models, MedSigLIP)
    each name the same cells of a population (`input`, else the one shown) from the same classes, through the runner
    at research priority (model_comparison.compare_classes), and the comparison of their runs is kept as one result
    entry with its table (d…), made for it: what the researcher's report compares is that record.
- On the live view now, or on a sample the session still keeps (`input`, Amendment 1 A "Inputs"): a population's
  view (segment), or a held clip's frames (one frame to segment, its frames to enhance), of the researcher's own
  session, on whichever day it was made. A sample no longer kept is
  said so ("that view is not kept any more"), never captured again in its place.
  Catalog capabilities also take an exact retained RGB result through `resource`: its bytes and
  metadata are admitted by retained_inputs, never replaced by the live view or its calibration.
- Never on screen: nothing is shown, and what it made is never what a later request of the user's means unless the
  user names or shows it (`Population.made_for`, population.resolve; a result's "for").
- Live first (§1g, "background work waits"): GPU work runs in the GPU worker's "research" class, served only when
  nothing of the app's own waits, bounded apart from the user's work and never superseding it, and never sharing a
  job with it (gpu_client, gpu_worker/inbox.py, the runner's job key); its memory is the "research" allowance
  (capture.py). One researcher operation at a time across the app, bounded by the selected effort's model rounds;
  refused, with the reason and a next step, when the camera has no fresh picture (live), memory is full or another
  session is open (ADR 0017: the live view and what it makes are the session open's; midnight changes nothing).
- Attributed from the start: what it makes is made for the researcher and filed with the conversation that started
  it; the researcher's session keeps what it ran (`ran`: the entry, the operation, why, which of its runs, and the
  sample it ran on), saved with it.
"""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass, field

from research_worker.budget import RUN_OPERATIONS as OPERATIONS, operation_limit

PRIORITY = "research"  # gpu_client's class for a researcher's own operations
NEXT = "carry on without it, and say what could not be measured"  # a researcher can't click or wait on the user
ELSEWHERE = "another session is open now: the live view and what it makes are that session's"


def stage_words(still: bool) -> str:
    """What a run with no kept input worked on, as the Stage showed it: a still picture (its frames carry the still's
    original, as population.py says it) or the live view (ticket 67: "segmented the live view" for a picture)."""
    return "the picture on the Stage" if still else "the live view"
GONE = "that view is not kept any more"  # a sample the session no longer keeps (Amendment 1 A), never re-captured
LET_GO_MIN_S = 5.0  # the app lets an operation go at the researcher's wait less 2 s, never sooner than this
MB = 1024 * 1024
FAILED_WORDS = {"segment": "segment the view", "enhance": "make the picture", "motion": "measure what moves",
                "capability": "run that capability", "classify": "classify those cells"}
CAPABILITY = "capability"  # one the catalog added (kinds.py's registry), named by `capability`
CLIP_FPS_TOO_LOW = ("a held clip has a frame every 0.5 s, and things are followed from frame to frame only at the live "
                    "view's rate (about 30 a second; at 2 a second a moving spot is lost or taken for its neighbour)")


@dataclass
class Sample:
    """A view the session still keeps, for a researcher's operation: a population's view or a held clip's frames."""
    entry: str  # its index id: "e3", "k1"
    kind: str  # "population" | "clip"
    capture: object  # the one view segment works on (capture.Capture)
    words: str  # what it is, in the app's words ("e3's view of 18:04:11", "frame 3 of held clip k1, 18:30:05")
    frames: list = field(default_factory=list)  # a clip's frames (held.HeldFrame), for enhance
    n: int | None = None  # which of a clip's frames `capture` is


def _mine(record, session) -> bool:
    """Whether a kept sample (a population, a held clip) is of the researcher's own session: by its `session`, else (a
    stand-in without one) by the session of its date; one that says neither is taken as it was before sessions.
    Another session's e3 is another thing."""
    made_in = getattr(record, "session", "") or getattr(record, "day", None)
    return made_in is None or made_in == (session.sitting or session.day)


class Refusal(Exception):
    """An operation the app won't run for a researcher: its reason and the next step a researcher can take."""

    def __init__(self, error: str, next: str = NEXT, *, interval: dict | None = None) -> None:
        super().__init__(error)
        self.error, self.next = error, next
        self.interval = copy.deepcopy(interval)


class Hands:
    def __init__(self, hub, log=None) -> None:
        self.hub = hub
        self.log = log or (lambda record: None)
        self.busy: str | None = None  # the researcher whose operation is running (one at a time, app-wide)
        self.sweep = None  # sweep.SweepBuffer: the user's last turn of the focus knob, for a focus stack (app.py sets it)

    async def run(self, session, operation: str, why: str = "", wait: float | None = None, input: str | None = None,
                  frame: int | None = None, how: str | None = None, seconds: float | None = None,
                  scheduled: bool = False, fresh: bool = False, implementation: str | None = None,
                  capability: str | None = None, params: dict | None = None, resource: dict | None = None,
                  inputs: dict | None = None, selection: dict | None = None, run_id: str | None = None,
                  models: list | None = None, classes: list | None = None) -> dict:
        """One operation for a researcher: {"ok", "id", "title", "words"} or {"ok": False, "error", "next"}.
        `wait`: how long the researcher waits for it; the app lets it go a little before that (its queued GPU
        call is dropped, its memory and the one-at-a-time slot freed) and says nothing was made. `input`: a kept
        sample to run on instead of the live view (`frame`: which of a clip's frames); `how`: enhance's method;
        `seconds`: how long motion watches the live view; `fresh`: acquire upcoming seconds rather than its rolling window;
        `implementation`: segment's cell finder, by name or id (names.py; the user's Q24), raw: that one or nothing.
        `resource`: an exact retained capability picture, mutually exclusive with input/frame. `inputs`: for a capability
        with several or native inputs, one exact retained resource per input (admission.py), checked as every actor's are.
        `models` and `classes`: classify's models (two to six, by id) and the classes to name the cells from."""
        measuring = operation == 'measure_saved_objects'
        workflow = operation == 'workflow'
        classifying = operation == 'classify'
        if (models is not None or classes is not None) and not classifying:
            return {"ok": False, "error": "models and classes belong only to classify", "next": NEXT}
        if classifying and (not isinstance(models, list) or not all(isinstance(m, str) for m in models)
                            or (classes is not None and (not isinstance(classes, list)
                                                         or not all(isinstance(c, str) for c in classes)))):
            return {"ok": False, "error": "classify takes models (two to six ids) and optionally classes (a list of names)",
                    "next": NEXT}
        if workflow:
            attempt = getattr(session, '_attempt', None)
            if (attempt is None or attempt.id != run_id or attempt.completion.done() or not attempt.record.get('dispatchedAt')):
                return {'ok':False,'error':'The original research run is no longer active','next':NEXT}
            if (type(params) is not dict or any(v is not None for v in (input,frame,how,seconds,implementation,capability,resource,inputs,selection)) or fresh or scheduled):
                return {'ok':False,'error':'Choose one workflow command in params without live inputs','next':NEXT}
            params=copy.deepcopy(params)
        if measuring:
            attempt = getattr(session, '_attempt', None)
            if (attempt is None or attempt.id != run_id or attempt.completion.done()
                    or not attempt.record.get('dispatchedAt')):
                return {'ok': False, 'error': 'The original research run is no longer active', 'next': NEXT}
            if (type(selection) is not dict or set(selection) != {'project', 'session', 'revision', 'input', 'digest'}
                    or any(value is not None for value in (input, frame, how, seconds, implementation, capability, params, resource, inputs))
                    or fresh or scheduled):
                return {'ok': False, 'error': 'Choose one exact saved labels selection, without live or capability inputs', 'next': NEXT}
            selection = copy.deepcopy(selection)  # dispatch owns this exact choice before any await
        elif selection is not None:
            return {'ok': False, 'error': 'selection belongs only to measure_saved_objects', 'next': NEXT}
        if inputs is not None and (operation != CAPABILITY or not isinstance(inputs, dict) or resource is not None
                                   or input or frame is not None):
            return {"ok": False, "error": "inputs name one retained resource per input of a capability; do not combine them "
                                          "with resource, input or frame",
                    "next": "read the session entries for the exact references, then run the capability with inputs"}
        if resource is not None:
            if operation != CAPABILITY or not isinstance(resource, dict) or input or frame is not None:
                return {"ok": False, "error": "resource names one exact retained picture for a capability; do not combine it with input or frame",
                        "next": "read the session entry for its retained capability input reference"}
            resource = copy.deepcopy(resource)
        kind, values, manifest = None, {}, None
        if operation == CAPABILITY:  # one the catalog added: the one door (kinds.py; ADR 0024), the same limits
            # It reads the catalog (every entry's installed generation and proof): once, in a thread.
            kind, values, manifest, why = await asyncio.to_thread(self.capability, capability, params)
            if why:
                return {"ok": False, "error": why[0], "next": why[1]}
            from ..retained_inputs import supported
            if resource is not None and not supported(kind):
                return {"ok": False, "error": "this capability cannot consume a retained picture yet",
                        "next": "choose a single-output 2D image, table, text or points capability"}
            if kind.get("typed") and inputs is None:
                from .. import kinds
                return {"ok": False, "error": f"{kinds.said_name(kind)} takes " + ", ".join(
                            f"{p['id']} ({p['type']})" for p in kind["inputs"]) + "; it never runs on the live view",
                        "next": "run it with inputs: one retained resource for each input"}
            if input:
                from .. import kinds

                return {"ok": False, "error": f"{kinds.said_name(kind)} runs on the live view for researchers, not a kept sample",
                        "next": "run it on the live view (no input)"}
        elif operation not in OPERATIONS:
            return {"ok": False, "error": f"the app does not run {operation!r} for researchers",
                    "next": "ask for one of: " + ", ".join((*OPERATIONS, CAPABILITY))}
        if implementation:
            from .. import names

            meaning = names.resolve("cells", implementation) if operation == "segment" else None
            if meaning is None:
                return {"ok": False, "error": (f"there is no cell finder {implementation!r}; the choices (id = name): "
                                               f"{names.choices_line('cells')}") if operation == "segment" else
                        (f"{operation} is done one way" + (" (spots followed frame to frame)" if operation == "motion" else "")
                         + ": implementation names segment's cell finder"), "next": NEXT}
            implementation = meaning.id
        if not isinstance(fresh, bool) or (fresh and (operation != "motion" or input)):
            return {"ok": False, "error": "fresh is a boolean for motion on the upcoming live camera interval", "next": NEXT}
        this_run = session.run_number()
        done = [r for r in session.ran if r.get("run") == this_run and not r.get("slot")]
        limit = operation_limit(getattr(session, "run_effort", None))
        if not limit:
            return {"ok": False, "error": "this effort does not run the app's operations", "next": NEXT}
        if len(done) >= limit and not scheduled:  # a mission's slots are the app's schedule (missions.py)
            return {"ok": False, "error": f"at most {limit} operations in one run",
                    "next": "work from what the earlier ones made (the session tool reads them)"}
        if self.busy is not None:
            return {"ok": False, "error": "another researcher's operation is running (one at a time, so the live view comes first)",
                    "next": "carry on, and ask again later in this run"}
        if self.elsewhere(session):
            return {"ok": False, "error": ELSEWHERE, "next": NEXT}
        pending_clip = bool(input and str(input).startswith("acq-"))
        try:
            if pending_clip:
                if operation not in ("segment", "motion"):
                    raise Refusal("scheduled clips support reading frames, segmentation and motion measurement",
                                  next="read the retained clip or run segment or motion with its id")
                sample = None  # disk read begins only after this operation owns the research slot
            else:
                sample = self.sample(session, str(input).strip(), frame if operation == "segment" else None,
                                     view=operation == "segment") if input and not classifying else None
            if sample is None and not pending_clip and resource is None and not measuring and not workflow and not classifying:
                source = getattr(self.hub, "source", None)
                if source is None or not source.is_fresh():
                    raise Refusal("no fresh picture from the camera")
        except Refusal as refused:
            self.log({"kind": "research-run", "rid": session.rid, "operation": operation, "ok": False,
                      "error": refused.error, **({"input": input} if input else {})})
            return {"ok": False, "error": refused.error, "next": refused.next}
        self.busy = session.rid
        started = time.monotonic()
        limit = max(LET_GO_MIN_S, float(wait) - 2.0) if wait else None
        input_reservation = None
        input_frames = None
        try:
            if pending_clip:
                from .acquisition import open_capture, open_frames
                try:
                    if operation == "motion":
                        input_frames = await asyncio.wait_for(open_frames(
                            self.hub, session.root / "acquisitions", session.day, str(input)),
                            min(limit, 5.0) if limit else 5.0)
                        sample = Sample(str(input), "acquisition", None,
                                        f"retained camera clip {input}", frames=input_frames)
                    else:
                        capture, n, input_reservation = await asyncio.wait_for(open_capture(
                            self.hub, session.root / "acquisitions", session.day, str(input), frame),
                            min(limit, 5.0) if limit else 5.0)
                        sample = Sample(str(input), "population", capture,
                                        f"frame {n} of retained camera clip {input}", n=n)
                except (ValueError, OSError) as exc:
                    raise Refusal(str(exc)) from None
            work = (self._workflow(session, params, attempt) if workflow
                    else self._classify(session, input, models, classes) if classifying
                    else self._measure(session, selection, attempt) if measuring
                    else self._capability(session, kind, values, manifest, resource=resource, inputs=inputs) if kind is not None
                    else self._segment(session, sample, implementation) if operation == "segment"
                    else self._motion(session, sample, seconds, fresh) if operation == "motion"
                    else self._enhance(session, sample, how, why))
            made = await (asyncio.wait_for(work, max(.001, limit - (time.monotonic() - started))) if limit else work)
        except asyncio.TimeoutError:
            self.log({"kind": "research-run", "rid": session.rid, "operation": operation, "ok": False,
                      "error": f"let go after {limit or 5.0:.0f} s"})
            if workflow:
                return {'ok':False,'error':'Workflow acknowledgement was interrupted; inspect the original request before retrying. Retained work may exist.','next':NEXT}
            if measuring:
                return {'ok': False, 'error': 'Stopped waiting for saved-label measurement; original labels are unchanged. '
                        'A measurement may already have been accepted.',
                        'next': 'inspect the original Project Data for its accepted table before retrying the exact selection'}
            why = {"segment": "(the live view's own work comes first)",
                   "motion": "(watching and measuring took longer than that)",
                   "classify": "(the models took longer than that)"}.get(operation, "(the picture took longer than that)")
            return {"ok": False, "error": f"not done in {limit or 5.0:.0f} s {why}, so the app let it go: nothing was made",
                    "next": NEXT}
        except Refusal as refused:
            self.log({"kind": "research-run", "rid": session.rid, "operation": operation, "ok": False, "error": refused.error,
                      **({"interval": copy.deepcopy(refused.interval)} if refused.interval is not None else {})})
            return {"ok": False, "error": refused.error, "next": refused.next,
                    **({"interval": copy.deepcopy(refused.interval)} if refused.interval is not None else {})}
        except Exception as exc:  # said plainly, never a raw exception (§1c); a ToolFailed's own words kept
            from ..brain.registry import ToolFailed

            # The app's own next step is the user's ("click a cell"), never one a researcher can take: NEXT instead.
            words = str(exc) if isinstance(exc, ToolFailed) else f"the app could not {FAILED_WORDS.get(operation, 'run it')} this time"
            self.log({"kind": "research-run", "rid": session.rid, "operation": operation, "ok": False,
                      "error": f"{type(exc).__name__}: {exc}"[:200]})
            return {"ok": False, "error": words, "next": NEXT}
        finally:
            if input_reservation is not None:
                input_reservation.release()
            if input_frames is not None:
                input_frames.release()
            self.busy = None
        session.ran.append({"id": made["id"], "operation": operation, "why": str(why or "")[:200], "run": this_run,
                            "at": round(time.time(), 2), "title": made["title"], "kind": made["kind"],
                            "words": str(made.get("words") or "")[:600],  # the app's own words: a stopped run's work (results.partial_result)
                            **({"version": made["version"]} if made.get("version") is not None else {}), **made["ran"],
                            **({"input": sample.entry} if sample is not None else {}),
                            **({"frame": sample.n} if sample is not None and sample.n is not None else {}),
                            **({"slot": True} if scheduled else {})})  # a mission's slot, not one of its own runs
        self.log({"kind": "research-run", "rid": session.rid, "operation": operation, "ok": True, "id": made["id"],
                  **made["ran"], **({"input": sample.entry} if sample is not None else {}),
                  "ms": round((time.monotonic() - started) * 1000)})
        return {"ok": True, "id": made["id"], "title": made["title"], "words": made["words"],
                **({"count": made["ran"]["count"]} if "count" in made.get("ran", {}) else {}),
                **({"capture": made["capture"]} if made.get("capture") else {}),
                **({"view": made["view"]} if made.get("view") else {}),
                **({"values": made["values"]} if made.get("values") else {})}

    async def _workflow(self, session, params, attempt):
        from ..workflow_service import service
        from ..workflows import wire, token
        day=getattr(self.hub,'execution_day',None);owner=session.sitting or session.day
        if (day is None or day.sid!=owner or session.status not in ('starting','running')
                or session._attempt is not attempt or attempt.completion.done()):
            raise Refusal('The original researcher or Project changed')
        runner=service(self.hub,day);w=runner.workflows;action=params.get('action')
        try:
            study = attempt.origin.get('study')
            if study and action in ('start', 'capability'):
                raise ValueError('This researcher belongs to a Study; use study-run so its work is retained and controlled there')
            if study and action in ('study-run', 'study-claim') and params.get('study') != study:
                raise ValueError('Admit work and publish findings in this researcher’s original Study')
            if type(action) is str and action.startswith('study-'):
                from .studies import for_workspace
                value=await for_workspace(self.hub,day).command(params,owner,actor='researcher:'+session.rid,label=session.name)
            elif action=='capabilities' and set(params)=={'action'}:
                from ..matrix_capability_runs import descriptors
                value={'capabilities':await asyncio.to_thread(descriptors)}
            elif action=='list' and set(params)<= {'action','query'}:
                value={'workflows':await asyncio.to_thread(w.list,params.get('query',''),False)}
            elif action=='inspect' and set(params)=={'action','workflow','revision'}:
                value={'workflow':wire(await asyncio.to_thread(w.definition,params['workflow'],params['revision']))}
            elif action=='status' and set(params)=={'action','run'}:
                value={'run':await asyncio.to_thread(runner.observed_status,params['run'],owner)}
            elif action in ('create','capture','start','validate','capability'):
                command=copy.deepcopy(params)
                if action in ('capture','start','validate','capability'):
                    if command.get('session',owner)!=owner:raise ValueError('Choose data in the researcher’s original Project')
                    command['session']=owner
                if action in ('start','validate'):
                    definition=await asyncio.to_thread(w.definition,command['workflow'],command['revision'])
                    if any(n['kind']=='researcher' for n in definition.document['state']['workflow']['graph']['nodes']):
                        raise ValueError('A researcher cannot launch another paid researcher through a workflow; ask the user to start that workflow')
                value=await runner.command(command,actor='researcher:'+session.rid)
            else:raise ValueError('Researchers may discover, capture, create or invoke workflows; review, removal and editing accepted definitions stay with the user')
            # Keep the discovery/dispatch receipt with this researcher, without claiming the process finished.
            saved=w.store.create(owner,{'kind':'workflow-invocation','title':'Researcher workflow '+str(action),
                'state':{'workflowInvocation':{'researcher':session.rid,'runId':attempt.id,'result':value}},'resources':[],'runtimes':[]},
                identity='p'+token({'researcher':session.rid,'runId':attempt.id,'params':params,'result':value})[:32])
            return {'id':saved.id,'title':saved.document['title'],'kind':'project','version':saved.revision,
                    'words':'Workflow command recorded. Inspect its run status; this is not a claim that analysis or review completed.',
                    'ran':{'workflowCommand':action},'values':value}
        except (ValueError,OSError,KeyError) as error:raise Refusal(str(error)) from error

    async def _classify(self, session, input: str | None, models: list, classes: list | None) -> dict:
        """Two to six models name the same cells of a population of the researcher's own session (`input`, else the one
        on screen) from the same classes, each through the runner at research priority, made for it; the comparison of
        their runs kept as one result entry with its table (model_comparison.py), never shown."""
        from .. import model_comparison
        from ..evidence import evidence_of
        from ..operations import runner
        from ..population import layer_of
        from ..resources import resources_of

        if input:
            population = evidence_of(self.hub).get(str(input).strip())
            if population is None or not _mine(population, session):
                raise Refusal(f"there are no cells called {input} in this session",
                              next="list the session's populations with the session tool, or segment the view first")
        else:
            population = layer_of(self.hub).population()
            if population is None:
                raise Refusal("no cells are on screen to classify",
                              next="name a population by its id (the session tool lists them), or segment the view first")
        chosen = model_comparison.select(self.hub, "classify", models)
        if len(chosen.impls) < model_comparison.MIN_MODELS:
            said = chosen.said()
            raise Refusal("a comparison needs two classifying models that can run here" + (f" ({said})" if said else ""),
                          next=f"name two to {model_comparison.MAX_MODELS} of: {model_comparison.choices_line('classify')}")
        record = await model_comparison.compare_classes(self.hub, population, chosen.impls, classes=classes,
                                                        origin="background", made_for=session.name, priority=PRIORITY)
        if not any(not a.get("failed") for a in record["arms"]):
            raise Refusal("no model could classify them: " + "; ".join(f"{a['label']}: {a['failed']}" for a in record["arms"]))
        words = model_comparison.words(record)
        executions = [a["execution"] for a in record["arms"] if a.get("execution")]
        made_by = [r.made_by() for r in (runner(self.hub).get(e) for e in executions) if r is not None]
        store = resources_of(self.hub).results
        args = {"operation": "classify", "implementations": [i.id for i in chosen.impls], "of": population.id,
                **({"classes": list(classes)} if classes else {})}
        entry = store.add("compare_models", args, f"{model_comparison.title(record)} on {population.title}: {words}",
                          values=model_comparison.values(record), executions=executions, made_by=made_by,
                          thread=(session.origin or {}).get("thread"), made_for=session.name,
                          session=session.sitting or session.day, retaining=True)
        entry = await store.confirm_save(entry)
        if entry["state"] != "done":
            raise Refusal("the comparison could not be saved", next="check local storage and run it again")
        compact = {k: record[k] for k in ("operation", "on", "totals", "columns", "rows", "says")}
        compact["pairs"] = record["summary"]["pairs"]
        compact["allAgree"], compact["everyNamed"] = record["summary"]["allAgree"], record["summary"]["everyNamed"]
        left = chosen.said()
        return {"id": entry["id"], "title": entry["title"], "kind": "result",
                "ran": {"models": [i.id for i in chosen.impls], "of": population.id, "executions": executions},
                "values": {"comparison": compact},
                "words": (f"classified {record['on']['members']} of {population.title}'s cells ({population.id}) with "
                          f"{len(chosen.impls)} models and kept the comparison ({entry['id']}): "
                          f"{model_comparison.headline(record)}" + (f"; not compared: {left}" if left else "")
                          + ". Its numbers are in the entry")}

    async def _measure(self, session, selection, attempt):
        """The same accepted-label measurement as Studio/Ask, scoped to this research run."""
        from .. import scientific_measurement as measurement
        from ..scientific_project_api import _owned
        from ..capture import Refused as MemoryRefused

        day = getattr(self.hub, 'execution_day', None)
        owner = session.sitting or session.day
        run = session.run_number()
        generation = getattr(day, 'generation', None)
        selected = {key: selection[key] for key in ('project', 'session', 'revision', 'input')}

        def check():
            if (day is None or day.sid != owner or getattr(day, 'generation', None) != generation
                    or (session.sitting or session.day) != owner or session.status not in ('starting', 'running')
                    or session.run_number() != run or getattr(session, '_attempt', None) is not attempt
                    or attempt.completion.done()):
                raise Refusal('The original research run or Project opening changed; nothing was retargeted')

        try:
            check()
            prepared = await _owned(measurement.describe, self.hub.resources.projects,
                                    measurement.OPERATION, selected, owner)
            if prepared['source']['digest'] != selection['digest']:
                raise Refusal('The selected saved revision does not match its captured digest')
            check()
            result = await measurement.run(self.hub, day, measurement.OPERATION, selected,
                caller='agent:researcher', researcher={'id': session.rid, 'run': run, 'runId': attempt.id}, check_caller=check)
        except (ValueError, OSError, MemoryRefused) as exc:
            raise Refusal(str(exc)) from exc
        saved = result['project']
        output = {'id': saved.id, 'session': saved.session, 'revision': saved.revision,
                  'digest': saved.digest, 'table': 'objects'}
        return {'id': saved.id, 'title': saved.document['title'], 'kind': 'project', 'version': saved.revision,
                'ran': {'source': prepared['source'], 'output': output, 'invocation': result['invocation']},
                'values': {'output': output, 'producer': result['producer'], 'reused': result['reused']},
                'words': f"{'Reused' if result['reused'] else 'Saved'} object measurements from accepted revision "
                         f"{selected['revision']} input {selected['input']}; segmentation was not rerun. "
                         'The table is retained in Project Data, not shown automatically.'}

    def capability(self, key: str | None, given: dict | None) -> tuple:
        """(the capability, its checked values, its manifest, None) or (None, {}, None, (why, next)): one the catalog
        added, on here and runnable, its parameters checked as every actor's are (kinds.values_for), from one reading of
        the catalog (kinds.read_once). It reads files: called in a thread."""
        from .. import kinds

        with kinds.read_once():
            kind = kinds.find(str(key or "")) if key else None
            if kind is None:
                on = ", ".join(k["key"] for k in kinds.available() if k.get("runnable")) or "none is on"
                return None, {}, None, (f"there is no capability {key!r} on here", f"name one of: {on}")
            name = kinds.said_name(kind)  # its makers' name, said plainly
            if not kind.get("runnable", True):
                return None, {}, None, (f"{name} can't run here yet: {kinds.said_words(kind.get('why'))}", NEXT)
            if kind["input"] != "image.2d.rgb8" and not kind.get("typed"):
                return None, {}, None, (f"{name} takes {kinds.SUPPLIED.get(kind['input'], kind['input'])}; researchers "
                                        "run capabilities on the live view's picture for now", NEXT)
            if given is not None and not isinstance(given, dict):
                return None, {}, None, ("params are its parameters by name", "call run again with params as an object")
            try:
                m = kinds.manifest_of(kind)
                return kind, kinds.values_for(kind, given, m=m, agent=True), m, None  # a researcher never sets a user-only setting
            except (ValueError, OverflowError) as exc:  # a number past a float's range: a refusal too, never raised
                return None, {}, None, (f"{name}: {kinds.said_words(exc)}", "call run again with values its parameters take")

    async def _capability(self, session, kind: dict, values: dict, m=None, *, resource=None, inputs=None) -> dict:
        from .. import admission, retained_inputs
        if kind.get("typed"):  # several or native inputs: bound at Run by the one admission every actor uses
            try:
                selected = await admission.bind_at_run(self.hub, m, inputs, session=session.sitting or session.day)
            except admission.Refused as exc:
                raise Refusal(admission.refusal_words(exc), next=exc.next) from None
            return await self._capability_on(session, kind, values, m, selected=selected)

        async with retained_inputs.admitted(self.hub, resource, session.sitting or session.day, allowance="research") as selected:
            return await self._capability_on(session, kind, values, m, selected=selected)

    async def _capability_on(self, session, kind: dict, values: dict, m=None, *, selected=None) -> dict:
        """One capability on the frozen live view or admitted retained input: through the operations
        runner at research priority, kept as a session entry made for it (never shown). What it made is its makers'
        output: the researcher reads it with the session tool, as untrusted data."""
        import json

        from .. import kinds
        from ..capture import captures
        from ..operations import Prompt, Unavailable, Unsupported, runner
        from ..resources import resources_of
        from ..source_images import for_analysis

        # The view it runs on, taken as a Capture before anything is awaited (capture.py: never looked up again after).
        info = getattr(self.hub, "latest_info", None) if selected is None else None
        capture = selected if selected is not None else captures(self.hub).get(info.id) if info is not None else None
        # Its checked values and what it asked the app for, read now (its manifest from capability(): nothing read again).
        inputs = (json.dumps(values, sort_keys=True) if selected is not None else
                  kinds.inputs_for(kind, values, self.hub, values=values, m=m if m is not None else kinds.UNREAD))
        typed = getattr(selected, "typed", None)
        reference = (copy.deepcopy(selected.source_image["resource"]) if selected is not None and typed is None else None)
        input_facts = ({"inputResources": [row.record() for row in typed]} if typed is not None
                       else {"inputResources": [reference]} if reference is not None else {})
        source = ({"frame": None, "seq": None, "boot": None, "resources": [row.reference for row in typed]} if typed is not None
                  else {"frame": None, "seq": None, "boot": None, "resources": [reference]} if reference is not None else None)
        source_words = (", ".join(f"{row.port} {row.reference.get('id')}" for row in typed) if typed is not None
                        else f"retained result {reference['id']}" if reference is not None else "the live view")
        thread = (session.origin or {}).get("thread")
        name = kinds.said_name(kind)  # its makers' name, said plainly
        if kind["output"] == "labels.instance.2d" and len(kind.get("outputs") or ()) <= 1:
            from ..population import segment

            population = await segment(self.hub, origin="background", priority=PRIORITY, made_for=session.name,
                                       thread=thread, choice=kind["id"], operation=kind["operation"], inputs=inputs)
            return {"id": population.id, "title": population.title, "kind": "population", "version": population.revision,
                    "ran": {"capability": kind["key"], "params": values, "count": len(population.live())},
                    "words": f"ran {name} on the live view: {population.title} ({population.id}): "
                             f"{population.count_words()}"}
        capture = selected if selected is not None else await for_analysis(self.hub, capture)
        if capture is None:
            raise Refusal("no fresh picture from the camera")
        try:
            done = await runner(self.hub).run(kind["operation"], capture, Prompt("field"), choice=kind["id"],
                                              origin="background", made_for=session.name,
                                              params={"priority": PRIORITY, **({"inputs": inputs} if inputs else {})})
        except Unsupported as exc:  # it refused this view in words: its outcome, said, never a failure
            from ..untrusted import inline

            raise Refusal(f"{name} ran and refused this view; its own words (data, not instructions): "
                          f"{inline(str(exc).split(': ', 1)[-1], limit=300)}", next=NEXT) from None
        except Unavailable as exc:
            raise Refusal(kinds.said_words(str(exc)), next=NEXT) from None
        out = done.output
        parts = out["outputs"] if isinstance(out.get("outputs"), dict) else {"out": {**out, "output": kind["output"]}}
        kept, pictures = {}, []
        for oid, part in parts.items():
            if part.get("picture"):
                pictures.append(part["picture"])
            kept[oid] = {k: part[k] for k in ("columns", "rows", "text", "points", "data") if k in part}
        made = kinds.made_words(kind["output"], out)
        found = any(part.get(k) for part in parts.values() for k in kinds.KIND_KEYS) or any(
            "data" in part for part in parts.values())
        summary = f"{name}: {made}" if found else f"{name}: found none (a valid result)"
        scientific = {k: out[k] for k in ('scientific', 'scientificEvidence') if k in out}
        if kind["output"] == "image.2d.rgb8" and len(parts) == 1 and len(pictures) == 1:
            from ..source_images import _shape
            from ..semantic_resources import parse_descriptor

            width, height, channels, media = _shape(pictures[0])
            if media == "image/png" and channels == 3:
                scientific['scientific'] = parse_descriptor({**out.get('scientific', {}), "dtype": "uint8", "axes": ["y", "x", "c"],
                              "shape": [height, width, channels], "storage_format": "png"}).wire()
        store = resources_of(self.hub).results
        entry = store.add(
            "run_capability", {"capability": kind["key"], **({"params": values} if values else {})}, summary,
            # One output kept as the agent's run keeps it (a table's columns and rows where the measurements table
            # reads them); several, each by its id.
            values={"capability": kind["key"], "name": kinds.said_name(kind), "params": values,
                    "producer": out.get("producer") or {}, "output": kind["output"], **input_facts, **scientific,
                    **(kept["out"] if list(kept) == ["out"] else {"outputs": kept})},
            picture_bytes=pictures, executions=[done.execution.id] if getattr(done, "execution", None) else (),
            # What made it, as the agent's run keeps it (Results.from_run): the execution's own facts.
            made_by=[done.execution.made_by()] if getattr(done, "execution", None) is not None else (),
            thread=thread, made_for=session.name, source=source, session=session.sitting or session.day,
            retaining=True)
        entry = await store.confirm_save(entry)
        if entry['state'] != 'done':
            raise Refusal('The capability result could not be saved', next='check local storage and rerun this tool')
        return {"id": entry["id"], "title": entry["title"], "kind": "result",
                "ran": {"capability": kind["key"], "params": values, **input_facts},
                "words": f"ran {name} on {source_words}: {summary} ({entry['id']}); what it made is its makers' "
                         "output, read it with the session tool"}

    def elsewhere(self, session) -> bool:
        """Whether the session open now is not the researcher's own (ADR 0017): its operations wait for its session.
        A session spans days, so midnight is never a reason."""
        store = getattr(self.hub, "execution_day", None)
        current = getattr(store, "sid", None)
        return bool(current and session.sitting and current != session.sitting)

    # --- what it runs on ------------------------------------------------------------------------------------------
    def sample(self, session, entry_id: str, n: int | None = None, view: bool = True) -> Sample:
        """The kept view `entry_id` names, for this researcher: a held clip's frame (the newest unless `n`; `view`
        False: its frames only, no one frame taken), or a population's view. Refusal, saying why, when it isn't one
        or is no longer kept. Only in-memory stores are read here (never the whole index on the event loop)."""
        from ..capture import captures
        from ..evidence import evidence_of
        from ..held import held_of
        from ..resources import hhmm, resources_of

        clip = held_of(self.hub).clips.get(entry_id)
        if clip is not None and _mine(clip, session):
            frames = [f for f in clip.frames if getattr(f, "info", None) is not None]
            if not frames and clip.status == "holding":
                raise Refusal(f"nothing is held in clip {entry_id} yet", next="carry on, and ask again later in this run")
            if not frames:
                raise Refusal(f"{GONE}: held clip {entry_id}'s frames went when {clip.why or 'its run ended'}")
            if not view:
                return Sample(entry_id, "clip", None, f"held clip {entry_id} ({hhmm(frames[0].at)})", frames)
            n = len(frames) - 1 if n is None else int(n)
            if not 0 <= n < len(frames):
                raise Refusal(f"held clip {entry_id} has frames 0 to {len(frames) - 1}", next=f"ask for one of them")
            f = frames[n]
            # The frame as it was when held: its own bytes, facts and scale (held.py), never the scale in force now.
            capture = captures(self.hub).adopt(f.info, f.jpeg, getattr(f, "scale", None))
            return Sample(entry_id, "clip", capture, f"frame {n} of held clip {entry_id} ({hhmm(f.at)}, of {len(frames)})",
                          frames, n)
        population = evidence_of(self.hub).get(entry_id)
        if population is not None and _mine(population, session):
            from ..population import capture_of

            capture = capture_of(self.hub, population)
            if capture is None:
                raise Refusal(f"{GONE}: {entry_id}'s view was neither kept in memory nor saved")
            return Sample(entry_id, "population", capture, f"{entry_id}'s view of {hhmm(capture.at)}")
        try:
            result = resources_of(self.hub).results.get(entry_id, session.sitting)  # in memory (the session's journal)
        except Exception:
            result = None
        if result is not None:
            raise Refusal(f"{entry_id} is a result: the app runs on a population's view or a held clip's frames",
                          next="use the live view, or one of those")
        raise Refusal(f"there is no {entry_id} among the session's populations and held clips",
                      next="list the session's entries to find one")

    # --- the operations -------------------------------------------------------------------------------------------
    async def _segment(self, session, sample: Sample | None, implementation: str | None = None) -> dict:
        from ..population import segment

        population = await segment(self.hub, capture=sample.capture if sample is not None else None, origin="background",
                                   priority=PRIORITY, made_for=session.name, thread=(session.origin or {}).get("thread"),
                                   choice=implementation)  # a named cell finder runs raw: that one, or it says why not
        view = population.view or {}
        where = sample.words if sample is not None else stage_words(bool(view.get("sourceImage")))
        # Which model ran, by the catalog key Tools lists (its cells option's model, else the option's own id), as a
        # capability run records it: the researcher's Tab names the run by it ("Ran Cellpose-SAM"), never from the prose.
        from ..options import GROUPS

        ran = population.implementation or implementation or ""
        key = (getattr(GROUPS["cells"].get(ran), "model", "") or ran) if ran else ""
        return {"id": population.id, "title": population.title, "kind": "population", "version": population.revision,
                "ran": {"count": len(population.live()), **({"capability": key} if key else {})},
                "capture": {"frame": population.capture_id, "seq": view.get("seq"), "boot": view.get("boot") or None,
                            "at": view.get("at")},  # the frame's own time: a mission's timepoint (the ledger)
                "view": dict(view),
                "words": f"segmented {where}: {population.title} ({population.id}): {population.count_words()}"}

    async def _motion(self, session, sample: Sample | None, seconds: float | None, fresh: bool = False) -> dict:
        """What moves in the live view over its last seconds (M3.2's "motion" operation, the slide's own motion
        taken out by the hub's offsets), kept as a tracks item made for this researcher: never shown, in the
        "research" allowance, never "the motion" a request of the user's means unless shown or named."""
        from ..brain.toolbox.motion import DEFAULT_S, LONGEST_S, InterruptedCameraInterval, gather
        from ..motiontracks import keep_measured

        retained = sample is not None and sample.kind == "acquisition"
        if sample is not None and not retained:
            if sample.kind == "clip":
                raise Refusal(f"motion is not measured on held clip {sample.entry}: {CLIP_FPS_TOO_LOW}",
                              next="measure motion on the live view now (no input)")
            raise Refusal(f"{sample.entry}'s view is one frame: nothing can be seen moving in one view",
                          next="measure motion on the live view now (no input)")
        try:
            seconds = float(seconds) if seconds is not None else DEFAULT_S
        except (TypeError, ValueError):
            seconds = DEFAULT_S
        seconds = max(1.5, min(LONGEST_S, seconds))
        from ..motiontracks import MotionFrames
        from ..scale import Reading

        original_run = len(session.history)

        def check():
            if self.elsewhere(session):
                raise Refusal(ELSEWHERE)
            if session.status not in ("starting", "running") or len(session.history) != original_run or session.run is not None:
                raise Refusal("the original researcher run is no longer running")

        try:
            if retained:
                check()
                frames = sample.frames.retain()
                seconds = frames[-1][1].wall_at - frames[0][1].wall_at
            else:
                frames = await gather(self.hub, seconds, fresh=True, check=check) if fresh else await gather(self.hub, seconds)
        except InterruptedCameraInterval as exc:
            check()  # a retired owner cannot turn an earlier acquisition failure into new work
            # This recovery is an available researcher operation, not a request
            # for the user to move the microscope. The model decides whether to
            # spend its unchanged allowance on a separate upcoming interval.
            raise Refusal(str(exc), next=exc.next, interval=exc.interval) from None
        try:
            if len(frames) < 10 or frames[-1][1].wall_at - frames[0][1].wall_at < 1.2:
                # What was measured, not a guessed cause (a short or slow clip is not "the slide kept moving").
                span = frames[-1][1].wall_at - frames[0][1].wall_at if frames else 0.0
                raise Refusal(f"only {len(frames)} frames over {span:.1f} s of one continuous view could be used (at least "
                              "10 over 1.2 s are needed to follow spots: the slide moving starts the view again, and a slow "
                              "or paused feed gives fewer), so nothing could be followed", next=NEXT)
            # A forward window uses its original held camera optics, never a knob changed during acquisition.
            if fresh or retained:
                capture = frames[-1][1]
                scale = capture.scale or {}
                k = scale.get("umPerPx")
                reading = (Reading(k, k * (getattr(capture, "disc", 0) or 0) * 2,
                                   scale.get("objective"), scale.get("measured") is True) if k else None)
            else:
                reading = self.hub.scale_reading() if hasattr(self.hub, "scale_reading") else None
            scale_words = ""
            if reading is not None:
                scale_words = ((f"{reading.objective:g}x objective, " if reading.objective else "")
                               + ("measured scale" if reading.measured else "scale estimated from the objective"))
            item, reused, done = await keep_measured(self.hub, frames, reading, scale_words, seconds, draw=False,
                                                     origin="background", made_for=session.name,
                                                     thread=(session.origin or {}).get("thread"), **({"check": check} if fresh or retained else {}))
        finally:
            if isinstance(frames, MotionFrames):
                frames.release()
        m = done.output["movement"]
        again = f" (the same frames measured at {time.strftime('%H:%M:%S', time.localtime(done.execution.at))}, reused)" \
            if reused else ""
        return {"id": item.id, "title": item.title, "kind": "tracks",
                "ran": {"seconds": round(float(m.seconds), 1), "counted": len(m.counted), "moving": len(m.moving)},
                "capture": {"frame": item.capture_id, "seq": item.view.get("seq"), "boot": item.view.get("boot") or None,
                            "at": item.view.get("at")},
                "view": copy.deepcopy(item.view),
                "values": copy.deepcopy({"summary": item.summary, "span": item.span,
                                         "scale": item.scale, "producer": item.producer}),
                "words": (f"measured what moves in {sample.words if retained else 'the live view'} over {m.seconds:.1f} s, the slide's own motion taken "
                          f"out{again}: {item.title} ({item.id}): {done.output.get('words') or ''}").strip()}

    async def _enhance(self, session, sample: Sample | None, how: str | None, why: str) -> dict:
        from .. import enhance as methods
        from ..brain.registry import ToolFailed
        from ..capture import captures
        from ..resources import hhmm, resources_of

        how = str(how or "average").strip().lower()
        if how not in methods.HOWS:
            raise Refusal(f"enhance can {', '.join(methods.HOWS)}, not {how!r}",
                          next="ask for average, sharpest or stack (stack: the user's own last turn of the focus knob, "
                               "when there is one)")
        if sample is not None and sample.kind != "clip":
            raise Refusal(f"{sample.entry}'s view is one frame: there is nothing to average or choose from",
                          next="enhance the live view, or a held clip's frames")
        try:
            # The eyepiece disc alone (crop): what a researcher reads, in a quarter to half the memory of the frame.
            plan = (methods.held_plan(sample.frames, how, crop=True) if sample is not None
                    else methods.live_plan(self.hub, how, self.sweep, crop=True))
        except ToolFailed as exc:
            # A held clip's refusals name what a researcher can do instead; the live view's are the user's to act on.
            raise Refusal(str(exc), next=exc.next if sample is not None and exc.next else NEXT) from None
        # Its own peak memory, in the researchers' allowance (never the user's work's); said plainly when it can't fit.
        memory = captures(self.hub).memory
        need, room = plan.need, int(memory.allowances.get("research", 0))
        if need > room:
            raise Refusal(f"a clearer picture of this {plan.size[0]}x{plan.size[1]} view needs {need / MB:.0f} MB, and "
                          f"researchers' work has {room / MB:.0f} MB", next=NEXT)

        def make() -> tuple:
            # Only its words and the JPEG leave the thread: the picture's pixels are let go inside the reservation.
            result = plan.work()
            cut = (0, 0, result.image.shape[1], result.image.shape[0]) if plan.crop else methods.disc_cut(result, plan.fov)
            return result.words, methods.jpeg_of(result, cut)

        with memory.reserve("research", need, f"a clearer picture for {session.name}"):
            working = asyncio.ensure_future(asyncio.to_thread(make))
            try:
                words, jpeg = await asyncio.shield(working)
            except asyncio.CancelledError:
                # Let go when the researcher's wait ran out: a thread can't be stopped, so its memory stays reserved
                # and the one-at-a-time slot taken until it returns; nothing is made from it (#186 review SF1).
                await asyncio.wait([working])
                raise
        used = [f for f in plan.used if f is not None]
        if sample is not None:
            numbers = [sample.frames.index(f) for f in used if f in sample.frames]
            # A run of frames by its ends; frames with a moving one left out between them, each (never "1-5" for 1, 2, 4, 5).
            span = (f"frame {numbers[0]}" if len(numbers) == 1 else f"frames {numbers[0]}-{numbers[-1]}"
                    if numbers == list(range(numbers[0], numbers[-1] + 1)) else f"frames {', '.join(map(str, numbers))}")
            where = f"{span} of held clip {sample.entry} (held {hhmm(used[0].at)})"
            frames = [{**f.facts(n)} for n, f in zip(numbers, used)]
        else:
            where = stage_words(any(getattr(f, "source_image_json", None) for f in used))
            frames = [{"frame": getattr(f, "id", None), "seq": getattr(f, "seq", None), "boot": getattr(f, "boot", None),
                       "at": round(float(getattr(f, "wall_at", 0.0) or 0.0), 3)} for f in used]
        # The researcher's own words (its `why`) are kept with its run (`ran`), never as the user's request here: the
        # index says a result's request as "asked: …", outside the untrusted wrapper (#186 review B1).
        entry = resources_of(self.hub).results.add(
            "enhance", {"how": how, **({"input": sample.entry} if sample is not None else {})}, f"{words} ({where})",
            values={"frames": frames}, picture_bytes=[jpeg], info=plan.info,
            thread=(session.origin or {}).get("thread"), made_for=session.name)
        return {"id": entry["id"], "title": entry["title"], "kind": "result",
                "ran": {"how": how, **({"frames": [f["n"] for f in frames]} if sample is not None else {})},
                "words": f"made a clearer picture of {where}: {entry['title']} ({entry['id']}): {words}"}
