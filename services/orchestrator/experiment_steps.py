"""The app's live models and its researchers as workflow steps (Part 2.5, W5: experiments; docs/architecture/
scientific-workflows.md "Experiments").

Will, 29 Oct: "I want to run an experiment comparing Cellpose and micro-sam on this footage and have a researcher audit
it." An experiment (experiments.py) is an ordinary frozen workflow run in WorkflowService, so it needs three things the
scientific steps did not have, each an option beside what was there (nothing it had changes):

- **A frame of the live view as a retained input** (`keep_frame`): the camera's own JPEG kept as a source file, and the
  same picture's grey plane as the scene's one intensity array on its own grid (pixel units, its coordinate identity the
  scene itself), with the frame's facts as the view recorded them (`population.view_facts`: size, eyepiece disc, epoch,
  offset, the bridge's own numbers, the run). Nothing is resized; the picture is the one the camera sent.
- **A live model as a tool step** (`capability(operation, impl)`, "model:cells/cellpose-sam"): its pinned generation is
  the implementation's catalog identity (`generation`), so a model that changed is refused before it runs. It runs the
  model through the runner on the frame, exactly as a researcher's own run does (`population.segment`, priority
  "research": served only when nothing of the app's own waits, so clicks go first; made for the experiment, so it is never
  "the cells" a request means unless shown or named). Its cells are kept in Data as a population; its label map is
  published as the step's instance-labels output on the frame's grid, so the workflow's own comparison and measurement
  steps (method_workflow, scientific measurement) read it unchanged. The step's output records what ran (the runner's
  execution, what ran, time, a reuse), the population and its count.
- **A researcher as a workflow step through the app's own researchers** (`LOCAL`, provider "local-researcher"): the
  managed service stays the optional extension it was; this provider starts a researcher of this Project through the
  research manager (`ResearchManager.start`, admitted by the plan the user approved: `confirmed`), with the run's record as
  its input and its question and criteria as its brief. The step waits on that researcher (`awaiting-research`); when it
  finishes, its finding is kept as a research-findings document (not reviewed by anyone: `accepted` false) and the step
  completes with it; a researcher that failed or stopped fails the step with its words. Unknown termination is
  `uncertain`, never replayed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from types import SimpleNamespace

import numpy as np

from .scientific_comparisons import exact, identity
from .scientific_projects import InvalidProject

PREFIX = "model:"
OPERATIONS = ("cells",)  # a live model step's operations: what a workflow's comparison and measurement steps read
POLICIES = ("raw", "route")
LOCAL = "local-researcher"
AUDIT_EFFORTS = ("quick", "standard")  # an audit reads records: never a deep run or a deliverable of its own
FRAME_KIND = "experimentFrame"
RAN = "experimentModelRun"
MAX_JPEG = 8 << 20


def _token(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


# --- a live model as a tool step -----------------------------------------------------------------------------------
def capability(operation: str, impl_id: str) -> str:
    return f"{PREFIX}{operation}/{impl_id}"


def parse(value) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.startswith(PREFIX) or "/" not in value:
        return None
    operation, impl = value[len(PREFIX):].split("/", 1)
    return (operation, impl) if operation and impl else None


def owns(settings) -> bool:
    return isinstance(settings, dict) and parse(settings.get("capability")) is not None


def generation(impl) -> str:
    """What pins a live model in a saved workflow: the implementation, its catalog entry and its version."""
    from .operations import IMPL_MODEL

    return _token({"impl": impl.id, "model": IMPL_MODEL.get(impl.id, ""), "version": impl.version})


def settings_for(operation: str, impl, policy: str = "raw") -> dict:
    return {"capability": capability(operation, impl.id), "generation": generation(impl),
            "parameters": {"policy": policy}, "mode": "automatic"}


def impl_of(settings):
    """The live implementation a step's settings name, or the reason it cannot be one."""
    from .operations import OPERATIONS

    named = parse(settings.get("capability"))
    if named is None:
        raise InvalidProject("Choose a live model step by its operation and model")
    operation, impl_id = named
    if operation not in OPERATIONS_ALLOWED():
        raise InvalidProject(f"A live model step runs {', '.join(OPERATIONS)}, not {operation}")
    impl = OPERATIONS[operation].impl(impl_id)
    if impl is None:
        raise InvalidProject(f"{impl_id} is not a model for {operation} here")
    return operation, impl


def OPERATIONS_ALLOWED() -> tuple[str, ...]:  # noqa: N802 - a constant read at call time (tests may extend it)
    return OPERATIONS


def validate(hub, node) -> None:
    """Read-only readiness of a live model step: the model, its pinned generation, its one parameter, and that it can
    run here now (operations.availability: this edition, its key, what must be installed, its worker)."""
    from .operations import availability

    settings = node["settings"]
    operation, impl = impl_of(settings)
    if settings["generation"] != generation(impl):
        raise InvalidProject(f"{impl.label} changed since this workflow was saved; save a new workflow version")
    if set(settings["parameters"]) != {"policy"} or settings["parameters"]["policy"] not in POLICIES:
        raise InvalidProject('A live model step takes one parameter: policy "raw" (exactly that model) or "route"')
    if len(node["needs"]) != 1:
        raise InvalidProject("A live model step takes one frame")
    if hub is None:
        raise InvalidProject("A live model step runs in the app with its models")
    why = availability(hub, impl)
    if why:
        raise InvalidProject(f"{impl.label} can't run here ({why})")


# --- frames ----------------------------------------------------------------------------------------------------------
def keep_frame(store, session: str, *, jpeg: bytes, facts: dict, title: str, request: str):
    """One frame of the live view as a retained scene: its grey plane as the scene's intensity array on its own grid,
    the camera's JPEG kept beside it, and the view's facts. Retried with the same request, the same frame comes back."""
    import cv2

    from .scientific_families import write_array
    from .workflow_execution import keep_file
    from .workflows import token

    if not isinstance(jpeg, (bytes, bytearray)) or not jpeg or len(jpeg) > MAX_JPEG:
        raise InvalidProject("A frame needs the camera's picture, at most 8 MiB")
    gray = cv2.imdecode(np.frombuffer(bytes(jpeg), np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.ndim != 2 or not gray.size:
        raise InvalidProject("The frame's picture could not be decoded")
    identity_ = "p" + token({"experimentFrame": request, "session": session})[:32]
    folder = store.for_session(session, write=True)._folder(identity_)
    if (folder / "head.json").exists():
        return store.read(identity_, session)
    picture = keep_file(store, session, token({"experimentFrameJpeg": request}), "frame.jpg", bytes(jpeg))
    scene = store.create(session, {"kind": "scene", "title": title[:240], "state": {}, "resources": [], "runtimes": []},
                         identity=identity_)
    calibration = {"space": {"session": session, "id": scene.id, "revision": 1}, "axes": ["y", "x"],
                   "units": ["pixel", "pixel"], "affine": [[1, 0, 0], [0, 1, 0]]}
    ref = write_array(store, scene, "array.image", np.ascontiguousarray(gray), ["y", "x"],
                      {"semantic": "intensity", "calibration": calibration})
    doc = scene.document
    doc["resources"] = [ref]
    doc["state"]["scientificView"] = {"inputs": {"image": ref}}
    doc["state"][FRAME_KIND] = {**copy.deepcopy(facts), "picture": identity(picture),
                                "width": int(gray.shape[1]), "height": int(gray.shape[0])}
    return store.save(scene.id, session, doc, expected_revision=scene.revision)


def frame_facts(saved) -> dict:
    facts = saved.document["state"].get(FRAME_KIND)
    if not isinstance(facts, dict) or not isinstance(facts.get("picture"), dict):
        raise InvalidProject("A live model step needs a frame kept with its picture (an experiment's frame)")
    return facts


def frame_jpeg(store, saved) -> bytes:
    facts = frame_facts(saved)
    picture = exact(store, facts["picture"], saved.session)
    files = [r for r in picture.document["resources"] if str(r.get("component", "")).startswith("file.")]
    if len(files) != 1:
        raise InvalidProject("The frame's picture is not kept as one file")
    source = store.file_source(picture.id, saved.session, picture.revision, files[0])
    if source.size > MAX_JPEG:
        raise InvalidProject("The frame's picture is larger than a frame")
    return source.read(0, source.size)


def capture_of_frame(hub, saved, jpeg: bytes):
    """The frame as a Capture the runner can take: the live capture of that frame in the run it was made in, else read
    back under an id no live frame has (capture.read_back), exactly as a saved population's view is."""
    from .capture import captures
    from .fov import FieldOfView

    v = frame_facts(saved)
    fov = FieldOfView(*v["disc"], 1.0) if v.get("disc") else None
    info = SimpleNamespace(id=int(v.get("frame") or 0), width=int(v["width"]), height=int(v["height"]),
                           epoch=v.get("epoch"), dx=float(v.get("dx") or 0.0), dy=float(v.get("dy") or 0.0), fov=fov,
                           disc=v.get("discPx") or 0.0, wall_at=v.get("at"), seq=v.get("seq"), boot=v.get("boot") or "",
                           tracked=bool(v.get("tracked", True)), steady=True)
    return captures(hub).read_back(info, jpeg, v.get("scale"), v.get("run"))


def _geometry(native) -> dict:
    """The output's grid: the frame's own calibration (the same conversion the bright regions step writes)."""
    calibration = native.descriptor.get("calibration")
    if not calibration:
        return {"basis": [[0, 0], [1, 0], [0, 1]], "axisLabels": ["y", "x"], "units": ["pixel", "pixel"]}
    affine, units = calibration.get("affine"), calibration.get("units")
    if (calibration.get("axes") != ["y", "x"] or not isinstance(affine, list) or len(affine) != 2
            or any(len(r) != 3 for r in affine) or len(units or []) != 2):
        raise InvalidProject("This frame's calibration needs an explicit supported spatial conversion")
    basis = [[affine[0][2], affine[1][2]], [affine[0][2] + affine[0][0], affine[1][2] + affine[1][0]],
             [affine[0][2] + affine[0][1], affine[1][2] + affine[1][1]]]
    return {"basis": basis, "axisLabels": ["y", "x"], "units": units}


async def run_model(steps, claim) -> dict:
    """One live model on one frame: through the runner at research priority, its cells kept in Data, its label map the
    step's output on the frame's grid. Returns the attempt's outcome (the shared tail adds the workflow association)."""
    import asyncio

    from .population import segment
    from .scientific_outputs import BuiltinOutputSave
    from .workflow_execution import ArrayOutput
    from .workflows import token

    hub, store, node = steps.hub, steps.store, claim["node"]
    operation, impl = impl_of(node["settings"])
    steps.check(claim)
    source = exact(store, claim["inputs"][0], claim["session"])
    facts = frame_facts(source)
    row, native = steps.array(source, "intensity")
    if list(native.shape) != [int(facts["height"]), int(facts["width"])]:
        raise InvalidProject("The frame's array and its picture differ in size")
    day = getattr(steps, "day", None)
    if day is not None and getattr(day, "sid", claim["session"]) != claim["session"]:
        raise InvalidProject("Open this experiment's Project to go on; its frames and the runs so far are kept")
    jpeg = await asyncio.to_thread(frame_jpeg, store, source)
    capture = capture_of_frame(hub, source, jpeg)
    made_for = (steps.workflows.run(claim["run"], claim["session"]).document["title"] or "")[:80]
    experiment = (source.document["state"][FRAME_KIND].get("experiment") or {}) if isinstance(
        source.document["state"][FRAME_KIND].get("experiment"), dict) else {}
    population = await segment(hub, capture=capture, choice=impl.id, policy=node["settings"]["parameters"]["policy"],
                               origin="background", priority="research",
                               made_for=experiment.get("name") or made_for or "an experiment",
                               thread=experiment.get("thread"), operation=operation)
    steps.check(claim)
    labels = population.labels()
    if labels is None:
        labels = np.zeros(tuple(native.shape), np.uint16)
    if tuple(labels.shape) != tuple(native.shape):
        raise InvalidProject(f"{impl.label}'s cells were not on the frame's grid")
    record = runner_facts(hub, impl, population)
    geometry = _geometry(native)

    def publish():
        output = BuiltinOutputSave(store, source, claim["attempt"]["id"], token(claim["attempt"])[:32],
                                   {"semantic": "instance-labels", "axes": ["y", "x"]},
                                   producer={"implementation": impl.id, "version": generation(impl)})
        try:
            worker = ArrayOutput(np.ascontiguousarray(labels), geometry)
            worker.info["name"] = f"Cells · {impl.label}"[:120]
            if native.descriptor.get("calibration"):
                from env_worker.scientific_output_coordinates import fingerprint

                worker.info["coordinatesFrom"] = {"format": "same-input-grid-v1", "input": row["key"],
                                                  "reference": row["reference"],
                                                  "sha256": fingerprint(native.shape, native.dtype, native.read)}
            output.capture(worker)
            steps.check(claim)
            output.publish()
            saved = store.read(output.target, source.session)
        finally:
            output.close()
        doc = saved.document
        doc["state"][RAN] = record
        return store.save(saved.id, saved.session, doc, expected_revision=saved.revision,
                          transaction=token({"experimentModelRun": claim["attempt"], "base": identity(saved)}))

    return await asyncio.to_thread(publish)


def runner_facts(hub, impl, population) -> dict:
    """What ran, from the runner's own record of the population's execution (never the step's words)."""
    from .operations import runner

    made = population.made_by or {}
    execution = made.get("execution")
    record = runner(hub).get(execution) if execution else None
    reused = bool(made.get("reused"))
    ms = None if reused or record is None else round(float(record.ms), 1)
    local = impl.worker != "cloud"
    return {"impl": impl.id, "label": impl.label, "executed": population.implementation or impl.id,
            "execution": execution, "at": getattr(record, "at", None) or population.made_at, "ms": ms, "reused": reused,
            "population": population.id, "populationTitle": population.title, "count": len(population.live()),
            "boxes": [[round(float(v), 1) for v in m.box] for m in population.live()][:4000],
            "runs": "this Mac" if local else "cloud", "costUsd": 0.0 if local and not reused else None,
            "costBasis": ("a reuse: nothing new was spent" if reused else "runs on this Mac" if local
                          else "not reported for this model's runs")}


# --- a researcher through the app's own researchers ---------------------------------------------------------------
LOCAL_FIELDS = {"question", "criteria", "provider", "maximumUsd", "seconds", "tools", "output", "effort"}


def is_local(settings) -> bool:
    return isinstance(settings, dict) and settings.get("provider") == LOCAL


def validate_local(settings) -> None:
    from research_worker.budget import EFFORTS

    if (set(settings) != LOCAL_FIELDS
            or any(type(settings[k]) is not str or not 0 < len(settings[k]) <= 8000 for k in ("question", "criteria"))
            or settings["tools"] != [] or settings["output"] != "report" or settings["effort"] not in AUDIT_EFFORTS
            or type(settings["maximumUsd"]) not in (float, int) or not math.isfinite(settings["maximumUsd"])
            or not EFFORTS[settings["effort"]].cap_usd <= settings["maximumUsd"] <= 100
            or type(settings["seconds"]) is not int or not 1 <= settings["seconds"] <= 7200):
        raise InvalidProject("A researcher step through this app's researchers takes a question, criteria, a quick or "
                             "standard effort, an allowance of at least that effort's cap and up to $100, a 1–7200 "
                             "second limit, no executable tools and a report")


def manager_of(hub):
    resources = getattr(hub, "resources", None)
    manager = getattr(resources, "research", None) or getattr(hub, "study_research_manager", None)
    return manager if manager is not None and hasattr(manager, "start") else None


def readiness_local(hub, settings) -> None:
    validate_local(settings)
    manager = manager_of(hub)
    if manager is None:
        raise InvalidProject("No researchers run in this app, so this step cannot start one")
    from .research.manager import engine_key_refusal

    if getattr(getattr(manager, "_worker", None), "runs_a_model", True):
        missing = engine_key_refusal(getattr(getattr(hub, "settings", None), "research", None))
        if missing is not None:
            raise InvalidProject(str(missing))


def brief(settings, record) -> str:
    """What the researcher is asked: the question and criteria the user approved, and what to read (the records named
    by id in the run's record, read with its session tool); the record's words are the app's own facts."""
    reading = record.get("read") or []
    lines = [settings["question"].strip(), "", "How to judge it: " + settings["criteria"].strip(), "",
             "Read these records of this Project with your session tool before you judge anything (read each one in "
             "full by its id; they are what the run made):"]
    lines += [f"- {r.get('id')}: {r.get('what')}" for r in reading if isinstance(r, dict) and r.get("id")][:40]
    lines += ["", "Check the numbers in the comparison table against each model's own cells (their counts and "
              "boxes), say for each finding what holds and what does not, cite the records each point rests on, and "
              "say what the run cannot settle. Agreement between models is not accuracy: never rank a model right."]
    return "\n".join(lines)


async def start_local(steps, claim) -> dict:
    """Starts the audit researcher for this attempt (once: its run is recorded on the attempt) and leaves the step
    awaiting it."""
    from .research.policy import ResearchRefused

    settings = claim["node"]["settings"]
    readiness_local(steps.hub, settings)
    manager = manager_of(steps.hub)
    source = exact(steps.store, claim["inputs"][0], claim["session"])
    record = read_record(steps.store, source)
    origin = {"asked": False, "experiment": {"run": claim["run"], "attempt": claim["attempt"]["id"],
                                             "name": record.get("name")},
              **({"thread": record["thread"]} if record.get("thread") else {})}
    words = brief(settings, record)
    fake = getattr(steps, "fake_researcher", None)
    try:
        researcher = await manager.start(words, effort=settings["effort"], deliverable="research", origin=origin,
                                         words=words, asked_with=words, confirmed=True,
                                         expected_session=claim["session"], **({"fake": fake} if fake else {}))
    except ResearchRefused as exc:
        raise InvalidProject(str(exc)) from exc
    attempt = getattr(researcher, "_attempt", None)
    return {"status": "awaiting-research", "output": None,
            "research": {"provider": LOCAL, "id": researcher.rid, "day": researcher.day,
                         "runId": getattr(attempt, "id", None), "name": researcher.name},
            "note": f"{researcher.name} is auditing the run"}


def read_record(store, saved) -> dict:
    """The run's record a researcher step was given: a JSON source file the experiment kept."""
    files = [r for r in saved.document["resources"] if str(r.get("component", "")).startswith("file.")]
    if saved.document["kind"] != "source-file" or len(files) != 1:
        raise InvalidProject("A researcher step reads the run's record, kept as one file")
    source = store.file_source(saved.id, saved.session, saved.revision, files[0])
    try:
        value = json.loads(source.read(0, min(source.size, 4 << 20)))
    except ValueError as exc:
        raise InvalidProject("The run's record is not readable") from exc
    if not isinstance(value, dict):
        raise InvalidProject("The run's record is not readable")
    return value


TERMINAL = {"done": "completed", "partial": "completed", "failed": "failed", "timeout": "failed",
            "over_budget": "failed", "cancelled": "stopped", "interrupted": "failed"}


def collect_local(hub, workflows, claim, research, stopping=False) -> dict | None:
    """The audit researcher's outcome, once it has one (None while it works): its finding kept as a research-findings
    document, unreviewed; a failure or stop with its words; an unknown researcher uncertain (nothing is replayed)."""
    manager = manager_of(hub)
    researcher = manager.sessions.get(research.get("id")) if manager is not None else None
    if researcher is None:
        return {"status": "uncertain", "output": None,
                "note": "The researcher this step started is not loaded; nothing was replayed."}
    status = TERMINAL.get(str(researcher.status))
    if status is None:
        return None
    if status != "completed":
        why = {"stopped": "was stopped", "failed": "did not finish"}.get(status, status)
        return {"status": "stopped" if stopping else status, "output": None,
                "note": f"{researcher.name} {why} ({researcher.status}); no audit was written"}
    finding = copy.deepcopy(getattr(researcher, "result", None))
    content = {"result": finding, "researcher": researcher.rid, "name": researcher.name, "day": researcher.day,
               "runId": research.get("runId"), "status": researcher.status, "accepted": False}
    saved = workflows.store.create(claim["session"], {
        "kind": "research-findings", "title": f"Audit · {researcher.name}"[:240],
        "state": {"researchFindings": content}, "resources": [], "runtimes": []},
        identity="p" + _token({"audit": claim["attempt"]["id"], "researcher": researcher.rid})[:32])
    return {"status": "completed", "output": identity(saved), "note": f"{researcher.name} wrote its audit (not reviewed)"}
