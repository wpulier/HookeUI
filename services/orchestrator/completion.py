"""Pure workspace completion facts, separate from execution and model judgment.

Callers adapt normalized, server-bound workspace/source records to this seam. This
module neither authenticates a browser nor decodes/hashes its pixels: it compares
the resulting evidence with a command fence. It schedules, persists and writes
nothing. A confirmation is scoped to one report, not to the user's whole intent.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Literal, TypedDict


Record = Mapping[str, Any]
Predicate = Literal["source_exact", "media_ready", "visible", "open", "closed",
                    "independent_windows", "control", "rect", "content_size", "relative_geometry",
                    "zoom", "pan", "front", "evidence_on_live", "view_state"]


class Postcondition(TypedDict):
    id: str
    predicate: Predicate
    targets: list[str]
    expected: dict[str, Any]
    essential: bool
    requiredPhase: Literal["committed", "loaded", "drawn"]
    tolerance: float | None


class Confirmation(TypedDict):
    requestId: str
    actionId: str
    commandId: str
    status: str
    matched: list[str]
    expected: list[dict[str, Any]]
    targets: dict[str, dict[str, Any]]
    unmatched: list[dict[str, Any]]
    observed: list[dict[str, Any]]
    liveEvidence: list[dict[str, Any]]
    report: dict[str, Any] | None
    error: dict[str, Any] | None
    revision: int


_PREDICATES = {
    "source_exact": {"sourceRef"}, "media_ready": {"decoded", "drawn"},
    "visible": {"value"}, "open": {"value"}, "closed": {"value"},
    "view_state": {"opacity", "hidden", "group"},
    "independent_windows": {"count", "distinctRoots"},
    "control": {"each", "independent", "value"}, "rect": {"rect"},
    "content_size": {"minWidth", "minHeight"},
    "relative_geometry": {"relation", "gap"},
    "zoom": {"value"}, "pan": {"center"}, "front": {"above", "scope", "hostId"},
    "evidence_on_live": {"populationId", "revision", "sourceRef", "origin", "liveSourceRef", "memberIds", "requiredCount"},
}
_PHASES = {"committed": 0, "loading": 0, "loaded": 1, "decoded": 1, "drawn": 2, "closed": 2}
_SOURCE_FIELDS = ("key", "kind", "media", "identity", "provenance")
_CLIENT_FIELDS = ("workspaceId", "clientId", "clientEpoch", "connectionEpoch")
_TERMINAL = {"confirmed", "data_complete", "failed", "canceled", "timed_out",
             "blocked", "unobserved", "interrupted"}
_VISIBLE_FACTS = {"opened", "drawn", "arranged", "closed", "zoomed", "panned", "fronted", "on_live"}
_VIEW_FACTS = {"zoomed": "zoom", "panned": "pan", "fronted": "front"}
_REPORT_FRESH_S = 30.0
_CONTROL_TOLERANCE = 1e-6
_MEMBER_CHECK_S = 2.0
_MEMBER_TRAVEL_PX = 16.0
_GEOMETRY_SHARE = 0.005


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rect(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(_number(v) for v in value) and value[2] > 0 and value[3] > 0)


def _vector(value: Any, length: int) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == length and all(_number(v) for v in value)


def _ids(value: Any, limit: int) -> bool:
    return (isinstance(value, (list, tuple)) and 0 < len(value) <= limit
            and all(isinstance(v, str) and 0 < len(v) <= 256 for v in value) and len(set(value)) == len(value))


def _basis(value: Any) -> bool:
    return (isinstance(value, Mapping)
            and all(isinstance(value.get(k), str) and 0 < len(value[k]) <= 256 for k in ("captureId", "run", "boot", "scaleId"))
            and all(type(value.get(k)) is int and 0 <= value[k] <= 2**53 - 1 for k in ("seq", "epoch"))
            and _number(value.get("objective")) and value["objective"] > 0
            and _number(value.get("umPerPx")) and value["umPerPx"] > 0
            and _vector(value.get("imageSize"), 2) and all(v > 0 for v in value["imageSize"])
            and _vector(value.get("disc"), 3) and value["disc"][2] > 0
            and _vector(value.get("offset"), 2))


def _bounded_json(value: Any) -> bool:
    """Preflight new proof trees before copying, within existing byte/node budgets."""
    nodes, size = 0, 0

    def visit(v: Any, depth: int) -> bool:
        nonlocal nodes, size
        nodes += 1
        if nodes > 16384 or depth > 16 or size > 65536:
            return False
        if isinstance(v, Mapping):
            size += 2 + 2 * len(v)
            return all(isinstance(k, str) and visit(k, depth + 1) and visit(child, depth + 1) for k, child in v.items())
        if isinstance(v, (list, tuple)):
            size += 2 + len(v)
            return all(visit(child, depth + 1) for child in v)
        if isinstance(v, str):
            if len(v) > 65536:
                return False
            size += 2
            for c in v:
                size += 6 if ord(c) < 32 else 2 if c in ('"', '\\') else len(c.encode("utf-8"))
                if size > 65536:
                    return False
        elif v is None or type(v) is bool:
            size += 5
        elif _number(v) and (type(v) is not int or abs(v) <= 2**53 - 1):
            size += len(str(v))
        else:
            return False
        return size <= 65536

    return visit(value, 0) and size <= 65536


def _live_bounds(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) <= 8
            and all(isinstance(r, Mapping) and isinstance(r.get("members"), (list, tuple)) for r in value)
            and sum(len(r["members"]) for r in value) <= 512 and _bounded_json(value))


def _new_proof_bounds(expected: Sequence[Record]) -> bool:
    return (_bounded_json(expected)
            and sum(len(e.get("expected", {}).get("memberIds", ())) for e in expected
                    if e.get("predicate") == "evidence_on_live"
                    and isinstance(e.get("expected"), Mapping)
                    and isinstance(e["expected"].get("memberIds"), (list, tuple))) <= 512)


def _source_equal(wanted: Any, actual: Any, *, _depth: int = 0) -> bool:
    # SourceRef's ordered constituents are identity too. Renewable transport and
    # availability remain outside identity at every level, as in presentations.
    if _depth > 16:
        return False
    if not isinstance(wanted, Mapping) or not isinstance(actual, Mapping):
        return False
    if any(k not in wanted or k not in actual for k in _SOURCE_FIELDS):
        return False
    if (not isinstance(wanted.get("key"), str) or not wanted["key"]
            or wanted.get("kind") not in ("live", "saved-image", "video", "lesson", "written", "composite")
            or wanted.get("media") not in ("capture", "labelled", "reference", "raw", "derived", "annotated",
                                           "external-reference", "generated", "lesson", "written", "unknown")
            or not isinstance(wanted.get("identity"), Mapping) or not wanted["identity"]
            or not isinstance(wanted.get("provenance"), Mapping)):
        return False
    if any(not _exact(wanted[k], actual[k]) for k in _SOURCE_FIELDS):
        return False
    if wanted.get("digest") is not None and not _exact(wanted["digest"], actual.get("digest")):
        return False
    left, right = wanted.get("components", ()), actual.get("components", ())
    return (isinstance(left, (list, tuple)) and isinstance(right, (list, tuple))
            and len(left) == len(right) <= 8
            and all(_source_equal(a, b, _depth=_depth + 1) for a, b in zip(left, right)))


def _exact(a: Any, b: Any) -> bool:
    """Keep identity values typed: a bool or string cannot stand in for a revision."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return set(a) == set(b) and all(_exact(v, b[k]) for k, v in a.items())
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_exact(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _same_number(a: Any, b: Any) -> bool:
    """A displayed number as the browser's JSON gives it: 1 for 1.0. Booleans and every other type stay exact."""
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    return _exact(a, b)


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _failure(fence: Record, boundary: str, code: str, reason: str) -> dict[str, Any]:
    return {"actionId": fence.get("actionId"), "boundary": boundary, "code": code,
            "reason": _text(reason), "retryable": False, "next": None}


@dataclass(frozen=True)
class _Mismatch:
    code: str
    boundary: str
    reason: str
    observed: Any = None
    pending: bool = False


def _valid_expectation(e: Record) -> bool:
    if not isinstance(e, Mapping):
        return False
    predicate = e.get("predicate")
    targets, values = e.get("targets"), e.get("expected")
    if predicate not in _PREDICATES or not isinstance(values, Mapping):
        return False
    if set(values) - _PREDICATES[predicate] or not isinstance(e.get("id"), str) or not e["id"]:
        return False
    if (not isinstance(targets, (list, tuple)) or not targets or len(targets) > 40
            or any(not isinstance(t, str) or not t for t in targets) or len(set(targets)) != len(targets)):
        return False
    if e.get("requiredPhase") not in ("committed", "loaded", "drawn"):
        return False
    tolerance = e.get("tolerance")
    if tolerance is not None and (not _number(tolerance) or tolerance < 0):
        return False
    if predicate == "view_state":
        return bool(values) and all((k == "opacity" and _number(v) and 0 <= v <= 1)
            or (k == "hidden" and type(v) is bool) or (k == "group" and (v is None or isinstance(v, str) and 0 < len(v) <= 256))
            for k, v in values.items())
    if predicate == "source_exact":
        return _source_equal(values.get("sourceRef"), values.get("sourceRef"))
    if predicate in ("visible", "open", "closed"):
        return isinstance(values.get("value"), bool)
    if predicate == "media_ready":
        return bool(values) and all(isinstance(v, bool) for v in values.values())
    if predicate == "independent_windows":
        return (type(values.get("count")) is int and values["count"] > 0
                and isinstance(values.get("distinctRoots"), bool))
    if predicate == "control":
        each = values.get("each")
        return (isinstance(each, (list, tuple)) and bool(each)
                and all(isinstance(c, str) and c for c in each)
                and ("value" not in values or _number(values["value"]))
                and ("independent" not in values or isinstance(values["independent"], bool)))
    if predicate == "rect":
        return _rect(values.get("rect"))
    if predicate == "content_size":
        return (bool(values) and all(_number(v) and v > 0 for v in values.values()))
    if predicate == "zoom":
        return _number(values.get("value")) and 0 < values["value"] <= 64
    if predicate == "pan":
        return _vector(values.get("center"), 2) and all(0 <= v <= 1 for v in values["center"])
    if predicate == "front":
        return (len(targets) == 1 and (values.get("above") == [] or _ids(values.get("above"), 64))
                and targets[0] not in values["above"]
                and (("scope" not in values and "hostId" not in values)
                     or (values.get("scope") == "workspace-windows" and isinstance(values.get("hostId"), str) and 0 < len(values["hostId"]) <= 256)))
    if predicate == "evidence_on_live":
        return (len(targets) == 1 and isinstance(values.get("populationId"), str) and bool(values["populationId"])
                and type(values.get("revision")) is int and values["revision"] >= 0
                and _source_equal(values.get("sourceRef"), values.get("sourceRef"))
                and _source_equal(values.get("liveSourceRef"), values.get("liveSourceRef"))
                and values["liveSourceRef"]["kind"] == "live" and _basis(values.get("origin"))
                and _ids(values.get("memberIds"), 512) and type(values.get("requiredCount")) is int
                and values["requiredCount"] == len(values["memberIds"]))
    return (values.get("relation") in ("left", "right", "above", "below")
            and _number(values.get("gap", 0)) and values.get("gap", 0) >= 0 and len(targets) == 2)


def valid_postconditions(expected: Any) -> bool:
    """Validate bounded desired predicates only; no authority or completion proof.

    Declaration adapters can use the comparator's value rules without copying
    its private vocabulary. Targets here are validation labels, not leases.
    """
    try:
        return (isinstance(expected, (list, tuple)) and 0 < len(expected) <= 16
                and all(isinstance(e, Mapping) and isinstance(e.get("predicate"), str) for e in expected)
                and _new_proof_bounds(expected) and all(_valid_expectation(e) for e in expected)
                and len({e["id"] for e in expected}) == len(expected))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        return False


def _origin_issue(report: Record | None, fence: Record, now: float) -> _Mismatch | None:
    if report is None:
        return _Mismatch("CLIENT_GONE", "observe", "No Studio observation is available.")
    client, wanted = report.get("client", {}), fence.get("client", {})
    if any(not wanted.get(k) or client.get(k) != wanted[k] for k in _CLIENT_FIELDS):
        return _Mismatch("REPORT_STALE", "observe", "The observation belongs to another client or connection.")
    if not fence.get("appRunId") or report.get("appRunId") != fence["appRunId"]:
        return _Mismatch("APP_RESTARTED", "observe", "The observation belongs to another app run.")
    seq, before = report.get("seq"), fence.get("afterReportSeq")
    received, issued = report.get("receivedAtMono"), fence.get("issuedMono")
    # A studio in the background reports only when it changes or is shown again, so its last report stands
    # until then (screen.Screen._current_client): its age is no reason to doubt it.
    stale = now - received >= _REPORT_FRESH_S if _number(received) and not _background(report) else False
    if (type(seq) is not int or type(before) is not int or seq <= before
            or not _number(received) or not _number(issued)
            or received < issued or received > now or stale):
        return _Mismatch("REPORT_STALE", "observe", "The observation is replayed, stale or not causally newer.")
    return None


def _background(report: Record) -> bool:
    """The studio's page is not on the user's display (another app or tab in front, a minimised window). It is
    still the studio: what it is sent is applied, and the browser paints it once the page is shown again. So it
    is waiting to be seen, never a failure (live 2026-10-11)."""
    return report.get("visible") is not True


def _fence_issue(pid: str, item: Record | None, ack: Record | None, lease: Record,
                 fence: Record) -> _Mismatch | None:
    if ack is None:
        return _Mismatch("REPORT_STALE", "observe", "The target has no matching command acknowledgment.")
    for generation in ("lifetimeGeneration", "sourceGeneration"):
        if (generation not in lease or ack.get(generation) != lease[generation]
                or (item is not None and item.get(generation) != lease[generation])):
            return _Mismatch("TARGET_REVISED", "commit", "The target lifetime or source generation changed.")
    versions = lease.get("committedPropertyVersions", {})
    if not versions or any(ack.get("committedPropertyVersions", {}).get(k) != v for k, v in versions.items()):
        return _Mismatch("REPORT_STALE", "observe", "The acknowledgment names a different committed property version.")
    if item is not None:
        if item.get("workspaceId") != fence["client"]["workspaceId"]:
            return _Mismatch("REPORT_STALE", "observe", "The target belongs to another workspace.")
        if any(item.get("propertyVersions", {}).get(k) != v
               or item.get("propertyOwners", {}).get(k) != lease.get("owner") for k, v in versions.items()):
            return _Mismatch("OWNERSHIP_STALE", "commit", "A touched property was changed or adopted by the user.")
        if lease.get("sourceRef") is not None and not _source_equal(lease["sourceRef"], item.get("sourceRef")):
            return _Mismatch("SOURCE_MISMATCH", "commit", "The target no longer displays its admitted producing source.")
    token = lease.get("sourceToken")
    if token is not None and (ack.get("sourceToken") != token or (item is not None and item.get("sourceToken") != token)):
        return _Mismatch("SOURCE_MISMATCH", "commit", "The loaded source token differs from the admitted source.")
    return None


def _ready(item: Record, ack: Record, required: str) -> _Mismatch | None:
    if item.get("phase") in ("failed", "error") or ack.get("phase") in ("failed", "error"):
        error = item.get("error") or ack.get("error") or {}
        code = error.get("code") if error.get("code") in ("MEDIA_LOAD_FAILED", "MEDIA_DECODE_FAILED") else "MEDIA_LOAD_FAILED"
        return _Mismatch(code, "load", _text(error.get("reason")) or "The renderer could not load the requested content.", error)
    rank = _PHASES[required]
    if min(_PHASES.get(item.get("phase"), -1), _PHASES.get(ack.get("phase"), -1)) < rank:
        return _Mismatch("REPORT_STALE", "observe", "The content has not reached the required rendering phase.",
                         item.get("phase"), pending=True)
    if required == "drawn" and (not item.get("drawRef") or not ack.get("drawRef")):
        return _Mismatch("REPORT_STALE", "observe", "No actual content draw is recorded.", pending=True)
    return None


def _visible(item: Record, report: Record | None = None) -> bool:
    """Shown in the studio. While the studio is in the background every item reports not visible (nothing is
    painted there); what its layout shows is 'tab-hidden' (screen.laid_out), and is shown: a window hidden or faded to
    nothing in the layout is not, whatever it drew before."""
    if item.get("open") is not True or item.get("connected") is not True or item.get("staged") is not False:
        return False
    if item.get("visible") is True:
        return True
    visibility = item.get("visibility")
    state = visibility.get("state") if isinstance(visibility, Mapping) else visibility
    return report is not None and _background(report) and state == "tab-hidden"


def _closure_issue(i: Record | None, ack: Record, lease: Record, report: Record) -> _Mismatch | None:
    if ack.get("phase") != "closed" or (i is not None and i.get("open") is not False):
        return _Mismatch("TOPOLOGY_MISMATCH", "draw", "The original target has not been retired.", i)
    if i is not None:
        return None  # Current closed item already passed the property/source fence.
    versions, owners = ack.get("observedPropertyVersions"), ack.get("observedPropertyOwners")
    explicit = versions is not None or owners is not None
    if explicit:
        if type(ack.get("reportSeq")) is not int or ack["reportSeq"] != report.get("seq"):
            return _Mismatch("REPORT_STALE", "observe", "The removal result is not an observation of this report.", pending=True)
        if not isinstance(versions, Mapping) or not isinstance(owners, Mapping):
            return _Mismatch("REPORT_STALE", "observe", "The removal result has no current property ownership.", pending=True)
        if any(not _exact(versions.get(p), v) or owners.get(p) != lease.get("owner")
               for p, v in lease["committedPropertyVersions"].items()):
            return _Mismatch("OWNERSHIP_STALE", "commit", "The removed target's touched properties were changed or adopted by the user.")
        return None
    if report.get("complete") is True:
        return None  # Authoritative absence plus the causal close receipt.
    return _Mismatch("REPORT_STALE", "observe", "A historical close receipt cannot establish current closure in an incomplete report.", pending=True)


def _new_ack_issue(i: Record | None, ack: Record, lease: Record, report: Record) -> _Mismatch | None:
    if (ack.get("appRunId") != report.get("appRunId") or not _exact(ack.get("reportSeq"), report.get("seq"))):
        return _Mismatch("REPORT_STALE", "observe", "The effect acknowledgment is not from this run and report.", pending=True)
    if any(not _exact(ack.get(k), lease.get(k)) or (i is not None and not _exact(i.get(k), lease.get(k)))
           for k in ("lifetimeGeneration", "sourceGeneration")):
        return _Mismatch("TARGET_REVISED", "commit", "The effect's typed target lifetime or source generation changed.")
    versions, owners = lease.get("committedPropertyVersions", {}), ack.get("committedPropertyOwners")
    if (not isinstance(owners, Mapping) or any(not _exact(ack.get("committedPropertyVersions", {}).get(p), v)
                                              or owners.get(p) != lease.get("owner") for p, v in versions.items())):
        return _Mismatch("OWNERSHIP_STALE", "commit", "The effect acknowledgment does not retain its touched property ownership.")
    if i is not None and any(not _exact(i.get("propertyVersions", {}).get(p), v)
                            or i.get("propertyOwners", {}).get(p) != lease.get("owner") for p, v in versions.items()):
        return _Mismatch("OWNERSHIP_STALE", "commit", "The actual effect's touched properties were superseded.")
    if i is not None and ack.get("sourceKey") is not None and ack["sourceKey"] != (i.get("sourceRef") or {}).get("key"):
        return _Mismatch("SOURCE_MISMATCH", "draw", "The effect acknowledgment names another producing source.")
    return None


def _registered_control(i: Record, name: str, pid: str, lease: Record) -> _Mismatch | None:
    controls = i.get("controls")
    state = controls.get(name) if isinstance(controls, Mapping) else None
    version = lease.get("committedPropertyVersions", {}).get(name)
    if not isinstance(state, Mapping) or state.get("present") is not True:
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The registered effect control is not observed.", state, pending=True)
    if (not state.get("id") or state.get("role") != name or state.get("target") != pid
            or state.get("enabled") is not True or state.get("editable") is not False
            or type(version) is not int or not _exact(state.get("stateRevision"), version)
            or not _exact(i.get("propertyVersions", {}).get(name), version)
            or i.get("propertyOwners", {}).get(name) != lease.get("owner")):
        return _Mismatch("CONTROL_UNAVAILABLE", "draw", "The registered control is stale, unavailable or targets another view.", state)
    return None


def _view_issue(e: Record, pid: str, i: Record, ack: Record, lease: Record, report: Record) -> _Mismatch | None:
    name, wanted = e["predicate"], e["expected"]
    pending = _registered_control(i, name, pid, lease)
    if pending and not pending.pending:
        return pending
    baseline = lease.get("baseViewDrawRef")
    paint = i.get("viewDrawRef")
    if (not isinstance(baseline, str) or not 0 < len(baseline) <= 256
            or not isinstance(paint, str) or not 0 < len(paint) <= 256
            or not _exact(paint, ack.get("viewDrawRef")) or _exact(paint, baseline)):
        pending = pending or _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The changed view has no matching new observed paint.", paint, pending=True)
    controls = i.get("controls")
    state = controls.get(name) if isinstance(controls, Mapping) else None
    view = i.get("view")
    actual = i.get("z") if name == "front" else view.get(name) if isinstance(view, Mapping) else None
    valid = _vector(actual, 2) and all(0 <= v <= 1 for v in actual) if name == "pan" else _number(actual)
    if not valid or (name == "zoom" and not 0 < actual <= 64):
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The actual view value is unknown or invalid.", actual, pending=True)
    if name != "pan":
        control = state.get("observedValue") if isinstance(state, Mapping) else None
        if not _number(control):
            pending = pending or _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The registered control's actual scalar value is unverified.", control, pending=True)
        elif abs(control - actual) > _CONTROL_TOLERANCE:
            return _Mismatch("CONTROL_UNAVAILABLE", "draw", "The observed control and actual view disagree.", control)
    if name in ("zoom", "pan"):
        values = [actual] if name == "zoom" else actual
        expected = [wanted["value"]] if name == "zoom" else wanted["center"]
        if any(abs(a - b) > _CONTROL_TOLERANCE for a, b in zip(values, expected)):
            return _Mismatch("CONTROL_UNAVAILABLE", "draw", "The actual view does not show the requested value.", actual)
    else:
        if wanted.get("scope") == "workspace-windows":
            scope = report.get("foreground")
            if (not isinstance(scope, Mapping) or scope.get("scope") != wanted["scope"]
                    or scope.get("hostId") != wanted["hostId"] or scope.get("complete") is not True):
                return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Workspace-window foreground coverage is incomplete.", pending=True)
            roots = scope.get("roots")
            if (not isinstance(roots, (list, tuple)) or len(roots) > 64
                    or any(not isinstance(root, Mapping) or not isinstance(root.get("rootId"), str)
                           or not root["rootId"] or not _number(root.get("z")) for root in roots)
                    or len({root["rootId"] for root in roots}) != len(roots)):
                return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Workspace-window foreground roots are ambiguous.", pending=True)
            order = {root["rootId"]: root["z"] for root in roots}
            if i.get("rootId") not in order or order[i["rootId"]] != actual:
                return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The requested window has no matching foreground root.", pending=True)
            # An omitted native window cannot be hidden by a scope completeness flag.
            if any(_visible(other) and other.get("sourceRef") and (other.get("rootId") not in order
                   or not _number(other.get("z")) or order[other["rootId"]] != other["z"])
                   for other in report.get("items", ())):
                return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "A visible window is missing or inconsistent in foreground coverage.", pending=True)
            if any(root != i["rootId"] and z >= actual for root, z in order.items()):
                return _Mismatch("TOPOLOGY_MISMATCH", "draw", "The requested window is not in front of the workspace content roots.", actual)
            if any(root not in order for root in wanted["above"]):
                return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "An originally visible foreground root is no longer observed.", pending=True)
            return pending
        if report.get("complete") is not True:
            pending = pending or _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Foreground order is unknown in an incomplete workspace.", pending=True)
        others = {}
        for other in report.get("items", ()):
            if other.get("id") == pid:
                continue
            # One explicit exclusion suffices; a missing flag is not exclusion.
            if (other.get("staged") is True or any(other.get(k) is False for k in ("open", "visible", "connected"))):
                continue
            if not _visible(other):
                pending = pending or _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "A registered root's foreground participation is unknown.", pending=True)
                continue
            others[other["id"]] = other
        if not i.get("rootId"):
            return _Mismatch("TOPOLOGY_MISMATCH", "draw", "Foreground order does not establish an independent registered root.")
        for other in others.values():
            if not other.get("rootId") or other["rootId"] == i["rootId"]:
                return _Mismatch("TOPOLOGY_MISMATCH", "draw", "Foreground order does not establish an independent registered root.")
            if _number(other.get("z")):
                if actual <= other["z"]:
                    return _Mismatch("TOPOLOGY_MISMATCH", "draw", "The requested view is not in front of the other visible roots.", actual)
            else:
                pending = pending or _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "A registered sibling's foreground order is unknown.", pending=True)
        if any(t not in others for t in wanted["above"]):
            pending = pending or _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "A registered sibling's foreground order is unknown.", pending=True)
    return pending


def _compatible_basis(a: Record, b: Record) -> bool:
    return (all(_exact(a[k], b[k]) for k in ("run", "boot", "epoch", "objective", "scaleId", "umPerPx", "imageSize"))
            and all(abs(x - y) <= a["disc"][2] * _GEOMETRY_SHARE for x, y in zip(a["disc"], b["disc"])))


def _origin_bound(source: Record, origin: Record, population: str, revision: int) -> bool:
    identity, provenance = source.get("identity", {}), source.get("provenance", {})
    scale = provenance.get("scale")
    return (identity.get("capture") == origin["captureId"] and identity.get("population") == population
            and _exact(identity.get("revision"), revision)
            and all(_exact(provenance.get(k), origin[k]) for k in ("run", "boot", "seq", "epoch", "objective"))
            and isinstance(scale, Mapping) and scale.get("id") == origin["scaleId"]
            and _exact(scale.get("umPerPx"), origin["umPerPx"]) and _exact(scale.get("objective"), origin["objective"]))


def _known_live_conflict(origin: Record, current: Any, receipt: Record, item: Record) -> str | None:
    """Reject independently typed conflicts without filling any unknown field."""
    labels = {"run": "app run", "boot": "camera session", "epoch": "motion epoch", "objective": "objective",
              "scaleId": "scale identity", "umPerPx": "measured scale", "imageSize": "image size", "seq": "frame number"}

    def known(key: str, value: Any) -> bool:
        if key in ("run", "boot", "scaleId"):
            return isinstance(value, str) and 0 < len(value) <= 256
        if key in ("epoch", "seq"):
            return type(value) is int and 0 <= value <= 2**53 - 1
        if key in ("objective", "umPerPx"):
            return _number(value) and value > 0
        return _vector(value, 2) and all(v > 0 for v in value)  # imageSize

    basis = current if isinstance(current, Mapping) else {}
    for key in ("run", "boot", "epoch", "objective", "scaleId", "umPerPx", "imageSize"):
        value = basis.get(key)
        if known(key, value) and not _exact(value, origin[key]):
            return f"The live {labels[key]} differs from the original capture."
    disc = basis.get("disc")
    if (_vector(disc, 3) and disc[2] > 0
            and any(abs(a - b) > origin["disc"][2] * _GEOMETRY_SHARE for a, b in zip(origin["disc"], disc))):
        return "The live eyepiece disc differs from the original capture geometry."
    identity = receipt["liveSourceRef"]["identity"]
    for key in ("run", "epoch"):
        if known(key, basis.get(key)) and known(key, identity.get(key)) and not _exact(basis[key], identity[key]):
            return f"The live {labels[key]} differs from its producing source."
    frame = item.get("drawRef")
    if isinstance(frame, Mapping):
        for actual, label in ((basis, "registration basis"), (receipt.get("drawRef"), "registration drawing")):
            if not isinstance(actual, Mapping):
                continue
            for key in ("run", "boot", "seq"):
                if known(key, actual.get(key)) and known(key, frame.get(key)) and not _exact(actual[key], frame[key]):
                    return f"The {label}'s {labels[key]} differs from the actual live frame."
    if (known("imageSize", basis.get("imageSize")) and known("imageSize", item.get("mediaSize"))
            and not _exact(basis["imageSize"], item["mediaSize"])):
        return "The live registration image size differs from the actual drawn content."
    return None


def _live_issue(e: Record, pid: str, i: Record, ack: Record, lease: Record, report: Record) -> _Mismatch | None:
    issue = _registered_control(i, "overlay", pid, lease)
    if issue:
        return issue
    receipts = report.get("liveEvidence", ())
    if not _live_bounds(receipts):
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Live registration evidence is missing or exceeds its bound.", pending=True)
    rows = [r for r in receipts if r.get("presentationId") == pid]
    if len(rows) != 1:
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "No unique current live registration receipt is available.", pending=True)
    r, wanted = rows[0], e["expected"]
    if "commandId" in ack and any(r.get(k) != ack.get(k) for k in ("requestId", "actionId", "commandId")):
        return _Mismatch("REPORT_STALE", "observe", "Live evidence belongs to another action occurrence.", r, pending=True)
    if (not _source_equal(wanted["sourceRef"], r.get("sourceRef"))
            or not _source_equal(wanted["liveSourceRef"], r.get("liveSourceRef"))
            or not _source_equal(wanted["liveSourceRef"], i.get("sourceRef"))
            or not _exact(r.get("populationId"), wanted["populationId"]) or not _exact(r.get("revision"), wanted["revision"])
            or not _exact(r.get("origin"), wanted["origin"])):
        return _Mismatch("SOURCE_MISMATCH", "draw", "The live registration does not bind the original producing capture and revision.", r)
    origin, now = r["origin"], r.get("liveBasis")
    if not _origin_bound(r["sourceRef"], origin, wanted["populationId"], wanted["revision"]):
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The original capture's producing optics and registration basis are unverified.", r, pending=True)
    frame = i.get("drawRef")
    state = r.get("registration")
    if isinstance(state, Mapping) and state.get("state") not in (None, "unknown", "verified"):
        return _Mismatch("ALIGNMENT_FAILED", "draw", _text(state.get("reason")) or "The members are not registered on the live view.", r)
    if (isinstance(state, Mapping) and state.get("state") == "verified"
            and isinstance(state.get("basis"), str) and 0 < len(state["basis"]) <= 256
            and state["basis"] != "per-member-pixel-match"):
        return _Mismatch("ALIGNMENT_FAILED", "draw", _text(state.get("reason")) or "The registration does not verify each member by its own pixels.", r)
    conflict = _known_live_conflict(origin, now, r, i)
    if conflict:
        return _Mismatch("ALIGNMENT_FAILED", "draw", conflict, r)
    if not _basis(now):
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The current live registration basis is unverified.", r, pending=True)
    if (not _compatible_basis(origin, now)
            or any(not _exact(r["liveSourceRef"]["identity"].get(k), now[k]) for k in ("run", "epoch"))
            or not isinstance(frame, Mapping) or not _exact(r.get("drawRef"), frame)
            or any(not _exact(frame.get(k), now[k]) for k in ("run", "boot", "seq"))
            or not _exact(i.get("mediaSize"), now["imageSize"])):
        reason = (r.get("registration") or {}).get("reason") if isinstance(r.get("registration"), Mapping) else None
        return _Mismatch("ALIGNMENT_FAILED", "draw", _text(reason) or "The capture and actual live frame have incompatible registration, optics or geometry.", r)
    if not isinstance(state, Mapping) or state.get("state") in (None, "unknown"):
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Current member registration is unverified.", r, pending=True)
    if state.get("state") != "verified" or state.get("basis") != "per-member-pixel-match":
        return _Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Current per-member pixel verification is unobserved.", r, pending=True)
    members = r["members"]
    ids = [m.get("memberId") if isinstance(m, Mapping) else None for m in members]
    if not _ids(ids, 512) or set(ids) != set(wanted["memberIds"]):
        return _Mismatch("ALIGNMENT_FAILED", "draw", "Registration does not cover the distinct requested members.", r)
    verified, drawn = 0, 0
    received = report.get("receivedAtMono")
    for m in members:
        basis, at = m.get("checkedBasis"), m.get("checkedAtMono")
        valid = (m.get("verified") is True and _basis(basis) and _compatible_basis(now, basis)
                 and basis["seq"] <= now["seq"] and _number(at) and _number(received)
                 and 0 <= received - at <= _MEMBER_CHECK_S
                 and math.hypot(*(x - y for x, y in zip(now["offset"], basis["offset"]))) <= _MEMBER_TRAVEL_PX)
        verified += bool(valid)
        drawn += bool(valid and m.get("drawn") is True and _exact(m.get("drawRef"), frame))
    if (type(r.get("verifiedCount")) is not int or type(r.get("drawnCount")) is not int
            or r["verifiedCount"] != verified or r["drawnCount"] != drawn
            or drawn != wanted["requiredCount"]):
        return _Mismatch("ALIGNMENT_FAILED", "draw", "Only the members verified and actually drawn on the live frame satisfy this goal.", r)
    return None


def _target_predicate(e: Record, pid: str, i: Record | None, ack: Record, lease: Record,
                      report: Record) -> _Mismatch | None:
    name, values = e["predicate"], e["expected"]
    if name == "closed":
        if values["value"] is not True:
            return _Mismatch("CAPABILITY_UNSUPPORTED", "admission", "Use open to require an open target.")
        return _closure_issue(i, ack, lease, report)
    if i is None:
        return _Mismatch("REPORT_STALE", "observe", "The target is not represented by this observation.", pending=True)
    waiting = _ready(i, ack, e["requiredPhase"])
    if waiting:
        return waiting
    if name in ("zoom", "pan", "front", "evidence_on_live"):
        if not _visible(i, report):
            return _Mismatch("TOPOLOGY_MISMATCH", "draw", "The effect is not visible on the registered view.", i)
        return (_live_issue(e, pid, i, ack, lease, report) if name == "evidence_on_live"
                else _view_issue(e, pid, i, ack, lease, report))
    if name == "source_exact":
        actual, wanted = i.get("sourceRef"), values["sourceRef"]
        if not _source_equal(wanted, actual):
            return _Mismatch("SOURCE_MISMATCH", "draw", "The actual producing source differs from the request.", actual)
        if wanted["kind"] == "live":
            frame = i.get("drawRef")
            if (not isinstance(frame, Mapping) or frame.get("run") != wanted["identity"].get("run")
                    or not frame.get("boot") or type(frame.get("seq")) is not int):
                return _Mismatch("SOURCE_MISMATCH", "draw", "The live sample has no matching drawn-frame identity.", frame)
            ack_frame = ack.get("drawRef")
            if (not isinstance(ack_frame, Mapping) or ack_frame.get("run") != frame["run"]
                    or ack_frame.get("boot") != frame["boot"] or type(ack_frame.get("seq")) is not int
                    or ack_frame["seq"] > frame["seq"]):
                return _Mismatch("SOURCE_MISMATCH", "draw", "The command and actual live drawing refer to different streams.", ack_frame)
        else:
            payload, admitted = i.get("payloadBinding", {}), lease.get("payloadBinding", {})
            if (payload.get("decodedToken") != i.get("sourceToken")
                    or not admitted.get("servedDigest") or payload.get("servedDigest") != admitted["servedDigest"]
                    or (wanted.get("digest") is not None and payload.get("originalDigest") != wanted["digest"])):
                return _Mismatch("SOURCE_MISMATCH", "draw", "The decoded payload differs from the admitted bytes.", payload)
    elif name == "media_ready":
        size = i.get("mediaSize")
        if (i.get("sourceRef") or {}).get("kind") == "written":
            # Text has no intrinsic image dimensions. Its verified bytes and
            # actual content box establish readiness; source_exact checks the digest.
            content = i.get("contentRect")
            size = content[2:] if _rect(content) else None
        if values.get("decoded") and not (isinstance(size, (list, tuple)) and len(size) == 2
                                           and all(_number(v) and v > 0 for v in size)):
            return _Mismatch("MEDIA_DECODE_FAILED", "load", "No nonzero decoded content dimensions were observed.", size)
        if values.get("drawn") and (i.get("phase") != "drawn" or not i.get("drawRef")):
            return _Mismatch("REPORT_STALE", "observe", "Decoded content has not been drawn.", pending=True)
    elif name == "view_state":
        actual = i.get("view") or {}
        if any(not _same_number(actual.get(k), v) for k, v in values.items()):
            return _Mismatch("CONTROL_UNAVAILABLE", "draw", "The displayed window state differs from the request.", actual)
    elif name in ("visible", "open"):
        actual = _visible(i, report) if name == "visible" else i.get("open")
        if actual != values["value"]:
            return _Mismatch("TOPOLOGY_MISMATCH", "draw", "The target is not in the requested visible/open state.", actual)
    elif name == "control":
        controls = i.get("controls", {})
        for control in values["each"]:
            state = controls.get(control, {})
            version = i.get("propertyVersions", {}).get(control)
            if (state.get("enabled") is not True or state.get("target") != pid
                    or (version is not None and state.get("stateRevision") != version)):
                return _Mismatch("CONTROL_UNAVAILABLE", "draw", "A requested control is unavailable or targets another window.", state)
            actual = i.get(control)
            if "value" in values and (not _number(actual) or abs(actual - values["value"]) > _CONTROL_TOLERANCE):
                return _Mismatch("CONTROL_UNAVAILABLE", "draw", "The control does not show its requested value.", actual)
    elif name == "rect":
        actual = i.get("rect")
        tolerance = e.get("tolerance") or 0
        if not _rect(actual) or any(abs(a - b) > tolerance for a, b in zip(actual, values["rect"])):
            return _Mismatch("GEOMETRY_MISMATCH", "draw", "The actual window geometry differs from the admitted geometry.", actual)
    elif name == "content_size":
        actual = i.get("contentRect")
        if (not _rect(actual) or actual[2] < values.get("minWidth", 0)
                or actual[3] < values.get("minHeight", 0)):
            return _Mismatch("GEOMETRY_MISMATCH", "draw", "The visible content is smaller than the requested size.", actual)
    return None


def _predicate(e: Record, items: Mapping[str, Record], acks: Mapping[str, Record], leases: Record,
               report: Record) -> _Mismatch | None:
    name, targets, values = e["predicate"], e["targets"], e["expected"]
    issues = []
    for pid in targets:
        issue = _target_predicate(e, pid, items.get(pid), acks[pid], leases[pid], report)
        if issue:
            issues.append((pid, issue))
    if issues:
        selected = next((issue for _, issue in issues if not issue.pending), issues[0][1])
        if len(issues) == 1:
            return selected
        observed = [{"presentationId": pid, "code": issue.code, "boundary": issue.boundary,
                     "reason": issue.reason, "observed": issue.observed} for pid, issue in issues]
        return _Mismatch(selected.code, selected.boundary, selected.reason, observed, selected.pending)
    if name == "independent_windows":
        roots = [items[t].get("rootId") for t in targets]
        if (len(targets) != values["count"] or not all(_visible(items[t], report) for t in targets)
                or (values["distinctRoots"] and (not all(roots) or len(set(roots)) != len(roots)))):
            return _Mismatch("TOPOLOGY_MISMATCH", "draw", "The report does not show the requested independent visible windows.", roots)
    if name == "relative_geometry":
        a, b = [items[t].get("rect") for t in targets]
        if not _rect(a) or not _rect(b):
            return _Mismatch("GEOMETRY_MISMATCH", "draw", "The relation has no usable measured geometry.", [a, b])
        relation, gap = values["relation"], values.get("gap", 0)
        fits = {"left": a[0] + a[2] + gap <= b[0], "right": b[0] + b[2] + gap <= a[0],
                "above": a[1] + a[3] + gap <= b[1], "below": b[1] + b[3] + gap <= a[1]}
        if not all(_visible(items[t], report) for t in targets) or not fits[relation]:
            return _Mismatch("GEOMETRY_MISMATCH", "draw", "The windows do not satisfy the requested measured relation.", [a, b])
    return None


def compare(expected: Sequence[Postcondition], report: Record | None, fence: Record, *, now: float) -> Confirmation:
    """Compare one closed set of expectations with one causally fenced report.

    Rejected reports are unobserved; unfinished valid rendering is pending until
    the monotonic deadline. A definitive predicate mismatch is failed, preserving
    any other matched predicates. This function does not wait or change ownership.
    """
    if not _number(now) or not _number(fence.get("deadlineMono")):
        raise ValueError("confirmation needs finite server monotonic time and deadline")
    result: Confirmation = {"requestId": fence.get("requestId", ""), "actionId": fence.get("actionId", ""),
                            "commandId": fence.get("commandId", ""), "status": "pending", "matched": [],
                            "expected": [], "targets": {}, "unmatched": [], "observed": [], "liveEvidence": [],
                            "report": None, "error": None, "revision": 1}

    def reject(issue: _Mismatch, status: str) -> Confirmation:
        result.update(status=status, error=_failure(fence, issue.boundary, issue.code, issue.reason))
        return result

    if (expected and len(expected) <= 16 and all(isinstance(e, Mapping) for e in expected)
            and any(e.get("predicate") in ("zoom", "pan", "front", "evidence_on_live") for e in expected)
            and not _new_proof_bounds(expected)):
        return reject(_Mismatch("VERIFICATION_UNAVAILABLE", "observe", "The new effect proof exceeds its bounded copy allowance."), "unobserved")
    if not expected or len(expected) > 16 or any(not _valid_expectation(e) for e in expected):
        return reject(_Mismatch("CAPABILITY_UNSUPPORTED", "admission", "The requested postcondition is unsupported or malformed."), "failed")
    result["expected"] = deepcopy(list(expected))
    origin = _origin_issue(report, fence, now)
    if origin:
        return reject(origin, "unobserved")
    assert report is not None
    raw_items, raw_acks = report.get("items", ()), report.get("commandResults", ())
    if (not isinstance(raw_items, (list, tuple)) or len(raw_items) > 40
            or not isinstance(raw_acks, (list, tuple)) or len(raw_acks) > 40
            or any(not isinstance(i, Mapping) or not i.get("id") for i in raw_items)):
        return reject(_Mismatch("REPORT_STALE", "observe", "The report is malformed or exceeds its bound."), "unobserved")
    items = {i["id"]: i for i in raw_items}
    if len(items) != len(raw_items):
        return reject(_Mismatch("REPORT_STALE", "observe", "The report has ambiguous duplicate instance identities."), "unobserved")
    if any(e["predicate"] == "evidence_on_live" for e in expected):
        evidence = report.get("liveEvidence", ())
        if not _live_bounds(evidence):
            return reject(_Mismatch("VERIFICATION_UNAVAILABLE", "observe", "Live evidence is malformed or exceeds its bounded copy allowance."), "unobserved")
        result["liveEvidence"] = deepcopy(list(evidence))
    targets = set(t for e in expected for t in e["targets"])
    front_scope = any(e["predicate"] == "front" for e in expected)
    result["observed"] = deepcopy([i for i in raw_items if front_scope or i["id"] in targets])
    result["report"] = {"client": deepcopy(report["client"]), "appRunId": report["appRunId"],
                        "seq": report["seq"], "receivedAtMono": report["receivedAtMono"],
                        "complete": report.get("complete") is True}
    if front_scope and "foreground" in report:
        result["report"]["foreground"] = deepcopy(report["foreground"])
    acks: dict[str, Record] = {}
    for ack in raw_acks:
        if not isinstance(ack, Mapping):
            continue
        if all(ack.get(k) == fence.get(k) and fence.get(k) for k in ("requestId", "actionId", "commandId")):
            pid = ack.get("presentationId")
            if pid in acks:
                return reject(_Mismatch("REPORT_STALE", "observe", "The target has conflicting duplicate command results."), "unobserved")
            acks[pid] = ack
    leases = fence.get("targets", {})
    result["targets"] = deepcopy({pid: dict(leases[pid]) for pid in targets if pid in leases})
    unacknowledged = []  # targets a studio in the background has not acknowledged yet: it does as it draws
    for pid in sorted(targets):
        if pid not in leases:
            return reject(_Mismatch("OWNERSHIP_STALE", "admission", "The requested target has no admitted lease."), "failed")
        issue = _fence_issue(pid, items.get(pid), acks.get(pid), leases[pid], fence)
        if issue is None and any(pid in e["targets"] and e["predicate"] in ("zoom", "pan", "front", "evidence_on_live")
                                 for e in expected):
            issue = _new_ack_issue(items.get(pid), acks[pid], leases[pid], report)
        if issue:
            if issue.boundary == "observe" and _background(report):
                unacknowledged.append(pid)
                continue
            return reject(issue, "unobserved" if issue.boundary == "observe" else "failed")
    if unacknowledged:
        return reject(_Mismatch("STUDIO_IN_BACKGROUND", "observe",
                                "The studio is in the background: this shows once the studio is in view again.",
                                unacknowledged, pending=True), "pending")
    essential_issues = []
    for e in expected:
        issue = _predicate(e, items, acks, leases, report)
        if issue is None:
            result["matched"].append(e["id"])
        else:
            result["unmatched"].append({"predicateId": e["id"], "code": issue.code,
                                        "expected": deepcopy(e["expected"]), "observed": deepcopy(issue.observed)})
            if e.get("essential", True):
                essential_issues.append(issue)
    if not essential_issues:
        result["status"] = "confirmed"
        if report["receivedAtMono"] >= fence["deadlineMono"]:
            reject(_Mismatch("CONFIRM_TIMEOUT", "observe", "The matching drawing was observed after the confirmation deadline."), "timed_out")
    else:
        definite = next((i for i in essential_issues if not i.pending), None)
        if definite:
            reject(definite, "failed")
        elif _background(report):
            # Nothing is painted while the studio is in the background, so nothing unfinished can be seen or
            # timed out there: it waits to be seen (the caller's own limit ends the wait).
            reject(_Mismatch("STUDIO_IN_BACKGROUND", "observe",
                             "The studio is in the background: this shows once the studio is in view again.",
                             pending=True), "pending")
        elif now >= fence["deadlineMono"]:
            reject(_Mismatch("CONFIRM_TIMEOUT", "observe", "The required draw was not confirmed before its deadline."), "timed_out")
        else:
            reject(essential_issues[0], "pending")
    return result


def _report_ref(value: Any) -> Record | None:
    if not isinstance(value, Mapping):
        return None
    client = value.get("client")
    if (not isinstance(client, Mapping) or any(not client.get(k) for k in _CLIENT_FIELDS)
            or not value.get("appRunId") or type(value.get("seq")) is not int
            or not _number(value.get("receivedAtMono"))):
        return None
    return value


def _same_origin(left: Record, right: Record) -> bool:
    return (_exact(left.get("appRunId"), right.get("appRunId"))
            and all(_exact(left["client"].get(k), right["client"].get(k)) for k in _CLIENT_FIELDS))


def _latest_report(outcome: Record) -> Record | None:
    latest = None
    for name in ("confirmation", "lateObservation"):
        observation = outcome.get(name)
        report = _report_ref(observation.get("report")) if isinstance(observation, Mapping) else None
        if report is not None and (latest is None or (_same_origin(report, latest) and report["seq"] > latest["seq"])):
            latest = report
    return latest


def observe_late(outcome: Record, confirmation: Record, *, at: float) -> dict[str, Any]:
    """Fold only the same occurrence; terminal history never becomes timely success."""
    if not _number(at):
        raise ValueError("late observation needs finite monotonic time")
    result = deepcopy(dict(outcome))
    if (not outcome.get("requestId") or not outcome.get("actionId")
            or any(confirmation.get(k) != outcome[k] for k in ("requestId", "actionId"))
            or (outcome.get("commandIds") and confirmation.get("commandId") not in outcome["commandIds"])):
        return result
    if confirmation == outcome.get("confirmation") or confirmation == outcome.get("lateObservation"):
        return result
    previous = _latest_report(outcome)
    report = _report_ref(confirmation.get("report"))
    if previous is not None and (report is None or not _same_origin(report, previous)
                                 or report["seq"] <= previous["seq"]
                                 or report["receivedAtMono"] < previous["receivedAtMono"]):
        return result  # Arrival time/local revision never supersede causal evidence.
    result["revision"] = outcome.get("revision", 0) + 1
    if outcome.get("status") in _TERMINAL:
        result["lateObservation"] = deepcopy(dict(confirmation))
        result["lateObservedAtMono"] = at
    else:
        result["confirmation"] = deepcopy(dict(confirmation))
        result["status"] = confirmation.get("status", "unobserved")
        result["error"] = deepcopy(confirmation.get("error"))
        if result["status"] == "confirmed":
            result["lastPhase"] = "drawn"
            result["confirmedAt"] = at
    return result


def first_failed_boundary(outcomes: Sequence[Record]) -> dict[str, Any] | None:
    """Select a root cause in dependency/plan order, never by arrival timestamp."""
    by_id = {r.get("actionId"): r for r in outcomes if r.get("actionId")}

    def root(row: Record) -> Record:
        seen = set()
        while row.get("actionId") not in seen:
            seen.add(row.get("actionId"))
            cause = (row.get("error") or {}).get("causeActionId")
            if not cause:
                cause = next((d for d in row.get("dependsOn", ()) if d in by_id
                              and by_id[d].get("status") in ("failed", "canceled", "timed_out", "blocked")), None)
            if cause not in by_id:
                break
            row = by_id[cause]
        return row

    candidates = {}
    for row in outcomes:
        if row.get("status") not in ("failed", "canceled", "timed_out", "blocked", "partial", "interrupted"):
            continue
        selected = root(row)
        error = selected.get("error")
        if error and error.get("code") != "DUPLICATE_IGNORED":
            candidates[selected.get("actionId")] = selected
    if not candidates:
        return None
    chosen = min(candidates.values(), key=lambda r: (r.get("round", 0), r.get("plannedStep", 0), r.get("actionId", "")))
    return deepcopy(chosen["error"])


def _fact(row: Record, kind: str, text: str, *, scope: str = "data") -> dict[str, Any]:
    return {"id": str(row.get("actionId", "")) + ":" + kind, "actionId": row.get("actionId"),
            "kind": kind, "scope": scope, "text": _text(text),
            "sourceRefs": deepcopy(row.get("sourceRefs", [])[:4]),
            "presentationIds": list(row.get("presentationIds", [])[:12])}


def _live_sources_proved(confirmation: Record, proof: Sequence[Record], fact: Record) -> bool:
    report = _report_ref(confirmation.get("report"))
    receipts, observed, leases = confirmation.get("liveEvidence"), confirmation.get("observed"), confirmation.get("targets")
    if (report is None or not _live_bounds(receipts) or not isinstance(observed, (list, tuple))
            or len(observed) > 40 or not isinstance(leases, Mapping)):
        return False
    items = {i.get("id"): i for i in observed if isinstance(i, Mapping)}
    bound = []
    for pid in fact["presentationIds"]:
        conditions = [e for e in proof if e["predicate"] == "evidence_on_live" and pid in e["targets"]]
        if pid not in items or pid not in leases or not conditions:
            return False
        for e in conditions:
            ack = {**items[pid], **{k: confirmation[k] for k in ("requestId", "actionId", "commandId")}}
            if _target_predicate(e, pid, items[pid], ack, leases[pid], {**report, "liveEvidence": receipts}):
                return False
            bound.extend((e["expected"]["sourceRef"], e["expected"]["liveSourceRef"]))
    return (all(any(_source_equal(s, b) for b in bound) for s in fact["sourceRefs"])
            and all(any(_source_equal(b, s) for s in fact["sourceRefs"]) for b in bound))


def _sources_proved(confirmation: Record, proof: Sequence[Record], fact: Record) -> bool:
    """Bind every claimed source to its exact target in the retained comparison.

    Closure identifies the retired target from its fenced closed item or, after
    removal, its admitted source lease. Neither needs a visible source picture.
    Other targets need the captured item as well as an exact source predicate or
    admitted source. Broad outcome source lists cannot establish this relation.
    """
    sources, targets = fact.get("sourceRefs"), fact.get("presentationIds")
    if (not isinstance(sources, (list, tuple)) or not 0 < len(sources) <= 4
            or not isinstance(targets, (list, tuple)) or not 0 < len(targets) <= 12
            or _report_ref(confirmation.get("report")) is None):
        return False
    if fact["kind"] == "on_live":
        return _live_sources_proved(confirmation, proof, fact)
    leases, observed = confirmation.get("targets"), confirmation.get("observed")
    if (not isinstance(leases, Mapping) or not isinstance(observed, (list, tuple))
            or len(observed) > 40 or any(not isinstance(i, Mapping) or not i.get("id") for i in observed)):
        return False
    items = {i["id"]: i for i in observed}
    if len(items) != len(observed):
        return False
    bound = []
    for pid in targets:
        lease = leases.get(pid)
        if not isinstance(lease, Mapping):
            return False
        candidates = [e["expected"]["sourceRef"] for e in proof
                      if e["predicate"] == "source_exact" and pid in e["targets"]]
        if lease.get("sourceRef") is not None:
            candidates.append(lease["sourceRef"])
        closing = fact["kind"] == "closed" and any(
            e["predicate"] == "closed" and e["expected"]["value"] is True
            and pid in e["targets"] for e in proof)
        item = items.get(pid)
        if closing and item is not None and item.get("open") is False:
            versions = lease.get("committedPropertyVersions")
            if (isinstance(versions, Mapping) and versions
                    and all(k in lease and _exact(item.get(k), lease[k])
                            for k in ("lifetimeGeneration", "sourceGeneration"))
                    and all(_exact(item.get("propertyVersions", {}).get(p), v)
                            and item.get("propertyOwners", {}).get(p) == lease.get("owner")
                            for p, v in versions.items())):
                candidates.append(item.get("sourceRef"))
        if not candidates or any(not _source_equal(candidates[0], s) for s in candidates):
            return False
        if item is not None:
            if not _source_equal(candidates[0], item.get("sourceRef")):
                return False
        elif not closing:
            return False
        bound.append(candidates[0])
    # A single producing source may legitimately serve several windows. Compound
    # facts compare source sets in both directions, without assigning by order.
    associated = (all(any(_source_equal(s, b) for b in bound) for s in sources)
                  and all(any(_source_equal(b, s) for s in sources) for b in bound))
    if associated and fact["kind"] in _VIEW_FACTS:
        report = {**confirmation["report"], "items": observed}
        return all(_target_predicate(e, pid, items.get(pid), items.get(pid, {}), leases[pid], report) is None
                   for e in proof if e["predicate"] == _VIEW_FACTS[fact["kind"]]
                   for pid in e["targets"] if pid in targets)
    return associated


def _proved_predicates(row: Record, fact: Record) -> tuple[Record, ...]:
    """A supplied fact kind needs the typed predicates actually checked by compare."""
    confirmation = row.get("confirmation") or {}
    expected, matched = confirmation.get("expected"), confirmation.get("matched")
    if (confirmation.get("status") != "confirmed"
            or any(confirmation.get(k) != row.get(k) for k in ("requestId", "actionId"))
            or not confirmation.get("commandId")
            or (row.get("commandIds") and confirmation["commandId"] not in row["commandIds"])
            or not isinstance(expected, (list, tuple)) or not 0 < len(expected) <= 16
            or not isinstance(matched, (list, tuple)) or len(matched) > 16
            or any(not isinstance(e, Mapping) for e in expected)
            or (any(e.get("predicate") in ("zoom", "pan", "front", "evidence_on_live") for e in expected)
                and not _new_proof_bounds(expected))
            or any(not _valid_expectation(e) for e in expected)
            or (row.get("expected") is not None and not _exact(row["expected"], expected))):
        return ()
    ids = [e["id"] for e in expected]
    if len(set(ids)) != len(ids):
        return ()
    requested = fact.get("predicateIds")
    if requested is not None and (not isinstance(requested, (list, tuple)) or not requested
                                   or len(requested) > 16 or any(k not in ids or k not in matched for k in requested)):
        return ()
    targets = set(fact["presentationIds"])
    related = targets | {t for e in expected if e["predicate"] == "front" and targets.intersection(e["targets"])
                         for t in e["expected"]["above"]}
    proof = tuple(e for e in expected if e["id"] in matched and related.intersection(e["targets"]))
    if any(e.get("essential", True) and e["id"] not in matched for e in expected if related.intersection(e["targets"])):
        return ()
    kind = fact["kind"]
    if kind == "closed":
        sufficient = [e for e in proof if e["predicate"] == "closed" and e["expected"]["value"] is True]
    elif kind == "opened":
        sufficient = [e for e in proof if e["predicate"] in ("open", "visible") and e["expected"]["value"] is True]
    elif kind == "drawn":
        sufficient = [e for e in proof if e["predicate"] != "closed" and
                      (e["requiredPhase"] == "drawn" or (e["predicate"] == "media_ready" and e["expected"].get("drawn")))]
    elif kind in _VIEW_FACTS or kind == "on_live":
        sufficient = [e for e in proof if e["predicate"] == _VIEW_FACTS.get(kind, "evidence_on_live")]
    else:  # arranged needs measured spatial proof; opening a picture is not it.
        sufficient = [e for e in proof if e["predicate"] in ("rect", "relative_geometry")]
    covered = {pid for e in sufficient for pid in e["targets"]}
    essential_coverage = {pid for e in sufficient if e.get("essential", True) for pid in e["targets"]}
    # Optional measurements prove their own facts; matching once does not make
    # them a dependency of every other fact on the same target.
    proof = tuple(e for e in proof if e.get("essential", True) or e["predicate"] == "source_exact"
                  or (requested is not None and e["id"] in requested)
                  or (e in sufficient and not set(e["targets"]) <= essential_coverage))
    return proof if targets <= covered and _sources_proved(confirmation, proof, fact) else ()


def _current(row: Record, fact: Record, report: Record | None, proof: Sequence[Record]) -> bool:
    if report is None or report.get("visible") is not True:
        return False
    confirmation = row["confirmation"]
    prior, current = _report_ref(confirmation.get("report")), _report_ref(report)
    if (prior is None or current is None or not _same_origin(current, prior)
            or current["seq"] < prior["seq"] or current["receivedAtMono"] < prior["receivedAtMono"]):
        return False
    raw_items = report.get("items", ())
    if (not isinstance(raw_items, (list, tuple)) or len(raw_items) > 40
            or any(not isinstance(i, Mapping) or not i.get("id") for i in raw_items)):
        return False
    items = {i["id"]: i for i in raw_items}
    if len(items) != len(raw_items):
        return False
    targets, sources = fact.get("presentationIds", ()), fact.get("sourceRefs", ())
    if not targets or not sources:
        return False
    previous = {i.get("id"): i for i in confirmation.get("observed", ())}
    leases = confirmation.get("targets", {})
    if any(t not in leases for e in proof for t in e["targets"]):
        return False
    acks = {}
    for e in proof:
        for t in e["targets"]:
            i = items.get(t)
            if i is not None:
                # Current-state revalidation reads actual renderer facts. It is
                # not a new command confirmation and needs no replayed old ack.
                acks[t] = ({**i, **{k: confirmation[k] for k in ("requestId", "actionId", "commandId")}}
                           if any(p["predicate"] == "evidence_on_live" and t in p["targets"] for p in proof) else i)
            elif report.get("complete") is True and e["predicate"] == "closed":
                acks[t] = {"phase": "closed"}
            else:
                acks[t] = next((ack for ack in report.get("commandResults", ())
                                if isinstance(ack, Mapping) and ack.get("presentationId") == t
                                and all(ack.get(k) == confirmation.get(k) for k in ("requestId", "actionId", "commandId"))), {})
    proof_targets = {t for e in proof for t in e["targets"]}
    for t in proof_targets:
        properties = {"source", "open"}
        for e in proof:
            if t not in e["targets"]:
                continue
            if e["predicate"] in ("rect", "relative_geometry", "content_size"):
                properties.add("rect")
            if e["predicate"] == "control" and "value" in e["expected"]:
                properties.update(e["expected"]["each"])
            if e["predicate"] in ("zoom", "pan", "front", "evidence_on_live"):
                properties.add("overlay" if e["predicate"] == "evidence_on_live" else e["predicate"])
        i, before = items.get(t), previous.get(t)
        closing = any(e["predicate"] == "closed" and t in e["targets"] for e in proof)
        if closing:
            if i is None:
                if report.get("complete") is not True and _fence_issue(t, None, acks[t], leases[t], {"client": report["client"]}):
                    return False
                continue  # _predicate below requires complete/current removal evidence.
            if i.get("open") is not False:
                return False
            if any(not _exact(i.get("propertyVersions", {}).get(p), v)
                   or i.get("propertyOwners", {}).get(p) != leases[t].get("owner")
                   for p, v in leases[t]["committedPropertyVersions"].items()):
                return False
        elif i is None or before is None or not _visible(i, report) or i.get("phase") != "drawn" or not i.get("drawRef"):
            return False
        if t in targets and not any(_source_equal(s, i.get("sourceRef")) for s in sources):
            return False
        if before is not None and not _source_equal(before.get("sourceRef"), i.get("sourceRef")):
            return False
        if any(not _exact(i.get(k), leases[t].get(k)) for k in ("lifetimeGeneration", "sourceGeneration")):
            return False
        token = leases[t].get("sourceToken")
        if token is None and before is not None:
            token = before.get("sourceToken")
        if token is not None and not _exact(i.get("sourceToken"), token):
            return False
        if before is not None and any(not _exact(i.get("propertyVersions", {}).get(k), before.get("propertyVersions", {}).get(k))
                                      or i.get("propertyOwners", {}).get(k) != before.get("propertyOwners", {}).get(k) for k in properties):
            return False
    return all(_predicate(e, items, acks, leases, report) is None for e in proof)


def _observed_value(row: Record, fact: Record, proof: Sequence[Record]) -> Any:
    kind = fact["kind"]
    if kind == "fronted":
        return {"above": list(next(e["expected"]["above"] for e in proof if e["predicate"] == "front"))}
    if kind == "on_live":
        e = next(e for e in proof if e["predicate"] == "evidence_on_live")
        receipt = next(r for r in row["confirmation"]["liveEvidence"] if r["presentationId"] in e["targets"])
        return {"requested": e["expected"]["requiredCount"], "verified": receipt["verifiedCount"], "drawn": receipt["drawnCount"]}
    name = _VIEW_FACTS[kind]
    items = {i["id"]: i for i in row["confirmation"]["observed"]}
    values = {pid: deepcopy(items[pid]["view"][name]) for pid in fact["presentationIds"]}
    return next(iter(values.values())) if len(values) == 1 else values


def _goal_row(row: Record) -> dict[str, Any] | None:
    goal = row.get("goal")
    if not isinstance(goal, Mapping) or not isinstance(goal.get("id"), str) or not 0 < len(goal["id"]) <= 256:
        return None
    expected, confirmation = goal.get("expected"), goal.get("confirmation")
    if (not isinstance(expected, (list, tuple)) or not 0 < len(expected) <= 16
            or any(not isinstance(e, Mapping) for e in expected) or not _new_proof_bounds(expected)
            or any(not _valid_expectation(e) for e in expected)
            or not isinstance(confirmation, Mapping) or not _exact(confirmation.get("expected"), expected)
            or any(confirmation.get(k) != row.get(k) for k in ("requestId", "actionId"))
            or confirmation.get("commandId") not in row.get("commandIds", ())):
        return None
    return {**row, "expected": expected, "confirmation": confirmation}


def _goal_current(row: Record | None, report: Record | None) -> bool:
    """Revalidate the whole admitted goal, independent of narrower fact kinds."""
    if row is None or row["confirmation"].get("status") != "confirmed":
        return False
    confirmation, expected = row["confirmation"], row["expected"]
    ids, matched = [e["id"] for e in expected], confirmation.get("matched")
    if (len(set(ids)) != len(ids) or not isinstance(matched, (list, tuple)) or len(matched) > 16
            or any(not isinstance(mid, str) or mid not in ids for mid in matched)
            or len(set(matched)) != len(matched)
            or any(e.get("essential", True) and e["id"] not in matched for e in expected)):
        return False
    proof = tuple(e for e in expected if e.get("essential", True) and e["id"] in matched)
    leases, observed = confirmation.get("targets"), confirmation.get("observed")
    if not proof or not isinstance(leases, Mapping) or not isinstance(observed, (list, tuple)) or len(observed) > 40:
        return False
    items = {i.get("id"): i for i in observed if isinstance(i, Mapping)}
    for pid in {t for e in proof for t in e["targets"]}:
        conditions = [e for e in proof if pid in e["targets"]]
        live = next((e for e in conditions if e["predicate"] == "evidence_on_live"), None)
        if live is not None:
            kind, sources = "on_live", [live["expected"]["sourceRef"], live["expected"]["liveSourceRef"]]
        else:
            kind = next((k for k, p in _VIEW_FACTS.items() if any(e["predicate"] == p for e in conditions)), "drawn")
            if any(e["predicate"] == "closed" for e in conditions):
                kind = "closed"
            lease = leases.get(pid)
            if not isinstance(lease, Mapping):
                return False
            source = next((e["expected"]["sourceRef"] for e in conditions if e["predicate"] == "source_exact"), lease.get("sourceRef"))
            if source is None and kind == "closed":
                source = items.get(pid, {}).get("sourceRef")
            sources = [source]
        fact = {"kind": kind, "presentationIds": [pid], "sourceRefs": sources}
        if not _sources_proved(confirmation, proof, fact) or not _current(row, fact, report, proof):
            return False
    return True


def _goal_limitation(row: Record, goal_row: Record | None) -> dict[str, Any]:
    goal = row["goal"]
    error = (goal_row["confirmation"].get("error") or {}) if goal_row is not None else {}
    fact = _fact(row, "limitation", error.get("reason") or "The requested goal is unmet; current fulfillment is unverified.")
    fact.update(goalId=goal.get("id"), goalStatus="unmet")
    if error.get("code"):
        fact["code"] = error["code"]
    return fact


def supported_facts(outcomes: Sequence[Record], current_report: Record | None, *, max_facts: int = 3) -> tuple[dict[str, Any], ...]:
    """Project bounded factual prose; failures/pending outrank extra success facts.

    Producer summaries and arbitrary success wording are never copied. This is a
    small fallback projection, not intent interpretation or final-model synthesis.
    """
    if type(max_facts) is not int or max_facts <= 0:
        raise ValueError("fact allowance must be a positive integer")
    limit = min(max_facts, 3)
    important, successes = [], []
    words = {"opened": "The requested view is open.", "drawn": "The requested content is drawn.",
             "arranged": "The requested views are arranged.", "closed": "The requested view was closed.",
             "read": "The requested retained information was read.", "saved": "The requested result was saved.",
             "computed": "The requested computation finished.", "zoomed": "The requested view is zoomed.",
             "panned": "The requested view is panned.", "fronted": "The requested view is in front.",
             "on_live": "The requested members are verified and drawn on the live view."}
    for row in outcomes:
        status = row.get("status")
        goal_row = _goal_row(row)
        has_goal = isinstance(row.get("goal"), Mapping)
        if status in ("failed", "blocked", "partial"):
            error = row.get("error") or {}
            important.append(_fact(row, "failure", error.get("reason") or "The requested result could not be completed."))
            if has_goal:
                important.append(_goal_limitation(row, goal_row))
            continue
        if status == "pending":
            important.append(_fact(row, "pending", "The requested result is still being prepared."))
            if has_goal:
                important.append(_goal_limitation(row, goal_row))
            continue
        if status in ("canceled", "timed_out", "interrupted", "unobserved"):
            wording = {"canceled": "The request was canceled; it is not complete.",
                       "timed_out": "The result was not confirmed before the deadline.",
                       "interrupted": "The request was interrupted; its result is unconfirmed.",
                       "unobserved": "The result's appearance in the Studio is unconfirmed."}
            important.append(_fact(row, "limitation", wording[status]))
            if has_goal:
                important.append(_goal_limitation(row, goal_row))
            continue
        goal_current = _goal_current(goal_row, current_report)
        accepted = False
        for supplied in row.get("facts", ()):
            kind = supplied.get("kind")
            if kind not in words or supplied.get("actionId") != row.get("actionId"):
                continue
            if not supplied.get("sourceRefs") or any(not any(_source_equal(s, r) for r in row.get("sourceRefs", ()))
                                                      for s in supplied["sourceRefs"]):
                continue
            if kind in _VISIBLE_FACTS:
                if (status != "confirmed" or row.get("completionScope") != "workspace"
                        or (row.get("confirmation") or {}).get("status") != "confirmed"
                        or not supplied.get("presentationIds")
                        or not set(supplied["presentationIds"]).issubset(row.get("presentationIds", ()))):
                    continue
                proof_row = row
                goal_fact = supplied.get("goalId") is not None or kind == "on_live"
                if goal_fact:
                    if goal_row is None or supplied.get("goalId") != row["goal"]["id"]:
                        continue
                    proof_row = goal_row
                proof = _proved_predicates(proof_row, supplied)
                if not proof:
                    continue
                current = _current(proof_row, supplied, current_report, proof)
                past = {"opened": "open", "drawn": "drawn", "arranged": "arranged", "closed": "closed",
                        "zoomed": "zoomed", "panned": "panned", "fronted": "in front", "on_live": "drawn on the live view"}[kind]
                text = words[kind] if current else f"The requested view was {past} earlier; its current state is unconfirmed."
                scope = "current" if current else "historical"
            else:
                required_scope = {"read": "data_read", "saved": "data_write", "computed": "compute"}[kind]
                if status != "data_complete" or row.get("completionScope") != required_scope:
                    continue
                text, scope = _text(supplied.get("text")) or words[kind], "data"
            projected = _fact(row, kind, text, scope=scope)
            projected["sourceRefs"] = deepcopy(supplied["sourceRefs"][:4])
            projected["presentationIds"] = list(supplied.get("presentationIds", [])[:12])
            if kind in _VIEW_FACTS or kind == "on_live":
                projected["value"] = _observed_value(proof_row, supplied, proof)
            if kind in _VISIBLE_FACTS and goal_fact:
                projected.update(goalId=row["goal"]["id"], goalStatus="confirmed" if goal_current else "unmet")
            successes.append(projected)
            accepted = True
        if has_goal and not goal_current:
            important.append(_goal_limitation(row, goal_row))
        if not accepted:
            important.append(_fact(row, "limitation", "No supported completion fact is available for this result."))
    # Facts stay attributable; receipt repeats do not manufacture repeated prose.
    unique = {}
    for fact in important + successes:
        unique.setdefault((fact["actionId"], fact["kind"], fact["scope"], fact.get("goalId")), fact)
    return tuple(deepcopy(list(unique.values())[:limit]))
