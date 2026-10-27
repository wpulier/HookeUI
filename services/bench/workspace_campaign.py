"""Pure acceptance observer over an adapter's independently collected records.

This module neither drives the application nor reads its data, starts a browser,
calls a model or selects a product plan. Adapters project the authoritative product
protocol into ``workspace-campaign.observation.v1``. They must collect the browser
channel independently: DOM measurements, decoded/drawn identity and the intercepted
payload digest cannot be copied from the server report. URLs and tool success are
diagnostics, not proof of pixels or intent. Synthetic records remain synthetic.

Source dictionaries contain immutable identity facts, including producing artifact
and crop lineage; equality deliberately includes every projected fact, not just the
JPEG digest. V1's retained-picture predicates are closed and never evaluate code.
Functional verification never certifies model quality or a latency distribution.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any, Protocol


PLAYBOOK_IDS = tuple((
    "C01 C02 C03 C04 C05 C06 C07 C08 C09 C10 C11 C12 C13 C14 C15 C16 "
    "V01 V02 V03 V04 V05 M01 M02 M03 M04 M05 S01 S02 S03 S04 S05 "
    "R01 R02 R03 R04 R05 R06 D01 D02 D03 D04 D05 Q01 Q02 Q03 Q04 Q05 "
    "T01 T02 T03 T04 T05 H01 H02 H03 H04 H05 H06 H07 H08 H09 H10 "
    "CL00 CL01 CL02 CL03 CL04 CL05 CL06 CL07 ME01 ME02 ME03 ME04 RA01"
).split())

SCHEMA = "workspace-campaign.observation.v1"
RECT_TOLERANCE_CSS = 2.0
MAX_CALLS = 3
SCOPE_FIELDS = ("request_id", "action_id", "command_id", "workspace_id", "client_id",
                "client_epoch", "page_epoch", "connection_epoch", "run")
SOURCE_KINDS = frozenset(("live", "saved-image", "video", "lesson", "written", "composite"))
SOURCE_MEDIA = frozenset(("capture", "labelled", "reference", "generated", "lesson", "written", "unknown",
                          "raw", "derived", "annotated", "external-reference"))
SOURCE_AVAILABILITY = frozenset(("retained", "temporary", "missing", "earlier-run", "unknown"))
DESKTOP_MINIMUM_CONTENT_CSS = (240, 180)
LANDMARK_TOLERANCE_SOURCE_PX = 2.0
PREDICATES = frozenset(("independent_views", "closed", "landmark"))
FAILED_TERMINALS = frozenset(("failed", "timed_out", "canceled", "cancelled", "superseded"))
# Other evidence classes are retained, but do not stand in for this browser oracle.
INDEPENDENT_DISPLAY_CLASSES = frozenset(("actual_browser",))


class ObservationAdapter(Protocol):
    """A read-only collector; product field mapping lives in the integrating adapter."""

    def checkpoint_record(self) -> Mapping[str, Any]: ...


def observe_checkpoint(adapter: ObservationAdapter) -> dict:
    return evaluate_checkpoint(adapter.checkpoint_record())


def _mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _vector(value: Any, size: int) -> bool:
    return _sequence(value) and len(value) == size and all(_number(v) for v in value)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _digest(value: Any, length: int = 64) -> bool:
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def _dimensions(value: Any) -> bool:
    return _vector(value, 2) and all(isinstance(v, int) and v > 0 for v in value)


def _version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _lifetime(value: Any) -> bool:
    # Opaque canonical identity; legacy integers retain their type, never hashed.
    return _text(value) or (_version(value) and value > 0)


def _affine(value: Any) -> bool:
    if not _vector(value, 6):
        return False
    determinant = value[0] * value[3] - value[1] * value[2]
    return _number(determinant) and determinant != 0


def _display_class(value: Any) -> bool:
    return _text(value) and value in INDEPENDENT_DISPLAY_CLASSES


def _failed_terminal(value: Any) -> bool:
    return _mapping(value) and _text(value.get("state")) and value["state"] in FAILED_TERMINALS


def _predicate(value: Any) -> bool:
    return _mapping(value) and _text(value.get("kind")) and value["kind"] in PREDICATES


@dataclass
class _Checks:
    checks: list[dict] = field(default_factory=list)

    def add(self, status: str, boundary: str, detail: str, **facts: Any) -> None:
        self.checks.append({"status": status, "boundary": boundary, "detail": detail, **facts})

    def expect(self, ok: bool, boundary: str, detail: str, **facts: Any) -> None:
        self.add("Pass" if ok else "Fail", boundary, detail, **facts)

    def result(self, record: Mapping, late: list[dict] | None = None) -> dict:
        task = _status([c for c in self.checks if c["boundary"] != "claim"])
        honesty = (_status([c for c in self.checks if c["boundary"] == "claim"])
                   if _sequence(record.get("claims")) else "Unverified")
        status = _status(self.checks)
        causality = _causality(record, self.checks)
        return {"status": status, "task_outcome": task, "honesty_outcome": honesty,
                "first_boundary": causality["first_boundary"], "causality": causality,
                "evidence_class": record.get("evidence_class"),
                "quality_verified": False,
                # This is conditional on the collector contract, not authentication
                # of arbitrary supplied class labels. Actual collection is external.
                "functional_verified": status == "Pass" and _display_class(record.get("evidence_class")),
                "checks": self.checks, "late_evidence": late or [],
                "observation": deepcopy(dict(record)),
                "operator_results": deepcopy(record.get("operator_results", [])),
                "terminal": deepcopy(record.get("terminal"))}


def _status(checks: Sequence) -> str:
    return ("Fail" if any(c["status"] == "Fail" for c in checks)
            else "Unverified" if any(c["status"] == "Unverified" for c in checks) else "Pass")


def _scope_field(key: str, value: Any) -> bool:
    return (_text(value) or _version(value)) if key == "client_epoch" else _text(value)


def _scope_valid(value: Any) -> bool:
    return _mapping(value) and all(k in value and _scope_field(k, value[k]) for k in SCOPE_FIELDS)


def _compare_scope(checks: _Checks, value: Mapping, expected: Mapping, channel: str, reference: str) -> None:
    # Completeness and contradiction are independent. Compare supplied known facts
    # without filling an absent request field from another channel's identity.
    common = [k for k in SCOPE_FIELDS if k in value and k in expected
              and _scope_field(k, value[k]) and _scope_field(k, expected[k])]
    if common:
        checks.expect(all(type(value[k]) is type(expected[k]) and value[k] == expected[k] for k in common),
                      "correlation", "Known origin facts must agree even when another field is missing",
                      channel=channel, reference_channel=reference,
                      expected={k: expected[k] for k in common}, observed={k: value[k] for k in common})


def _scope(checks: _Checks, value: Any, channel: str) -> Mapping | None:
    if not _scope_valid(value):
        checks.add("Unverified", "correlation", "Missing or invalid complete request origin", channel=channel)
    return value if _mapping(value) else None


def _json_facts(value: Any, depth: int = 0, budget: list | None = None) -> bool:
    """Bound recursive descriptors before comparison; cycles cannot be admitted."""
    if budget is None:
        budget = [65536]
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        return False
    if value is None or isinstance(value, bool) or _number(value):
        return True
    if isinstance(value, str):
        budget[0] -= len(value)
        return budget[0] >= 0
    if _mapping(value):
        return len(value) <= 128 and all(_text(k) and _json_facts(v, depth + 1, budget) for k, v in value.items())
    if _sequence(value):
        return len(value) <= 128 and all(_json_facts(v, depth + 1, budget) for v in value)
    return False


def _source_ref(checks: _Checks, ref: Any, channel: str, pid: str, depth: int = 0) -> bool:
    start = len(checks.checks)
    def require(ok: bool, detail: str) -> None:
        if not ok:
            checks.add("Unverified", "source", detail, channel=channel, presentation_id=pid)
    fields = ("key", "kind", "media", "identity", "provenance", "availability")
    if not _mapping(ref) or depth > 8 or not all(k in ref for k in fields):
        require(False, "Missing canonical discriminated SourceRef")
        return False
    require(all(_text(ref[k]) for k in ("key", "kind", "media", "availability"))
            and ref["kind"] in SOURCE_KINDS and ref["media"] in SOURCE_MEDIA and ref["availability"] in SOURCE_AVAILABILITY, "Invalid source discriminator")
    require(ref["availability"] in ("retained", "temporary") and ref["media"] != "unknown",
            "Source availability/media cannot certify these pixels")
    identity, provenance = ref["identity"], ref["provenance"]
    if not _mapping(identity) or not _mapping(provenance):
        require(False, "Missing canonical source identity/provenance")
        return False
    if provenance.get("unknown"):
        require(False, "Explicit unknown source facts prevent exact certification")
    if ref["kind"] == "live":
        require(all(_text(identity.get(k)) for k in ("run", "objectId", "anchorCapture"))
                and _version(identity.get("epoch")), "Live source requires run/object/anchor/epoch")
        require(_text(provenance.get("source")) and provenance.get("pixelsImmutable") is False,
                "Live source provenance must identify its renewable pixel stream")
    else:
        resource = identity.get("resource")
        require(_mapping(resource) and all(k in resource for k in ("day", "id", "version", "picture"))
                and _text(resource.get("day")) and _text(resource.get("id"))
                and _version(resource.get("version")) and _version(resource.get("picture")),
                "Retained resource needs exact day/id/version/picture; explicit unknown stays unknown")
        require(_text(provenance.get("producer")), "Source needs its producing occurrence")
        # Captured member and frame lineage apply to captures, never external references.
        if ref["media"] in ("capture", "labelled") or any(k in identity for k in ("population", "member", "capture")):
            require(_text(identity.get("capture")), "Capture source needs its original capture identity")
            if any(k in identity for k in ("population", "member", "revision")):
                require(all(_text(identity.get(k)) for k in ("population", "member"))
                        and _version(identity.get("revision")), "Member source needs population/member/revision")
            require(_vector(identity.get("crop"), 4) and all(n > 0 for n in identity["crop"][2:]),
                    "Capture source needs its exact crop geometry")
            require(all(_text(provenance.get(k)) for k in ("run", "boot", "cropSpace"))
                    and _version(provenance.get("seq")) and _number(provenance.get("capturedAt"))
                    and _dimensions(provenance.get("frameDimensions")) and _affine(provenance.get("sourceToView")),
                    "Capture needs measured run/boot/seq/time/geometry/transform lineage")
    if "digest" in ref:
        require(_digest(ref["digest"]), "Invalid canonical payload digest")
    components = ref.get("components", [])
    require(_sequence(components) and len(components) <= 8, "Unbounded source components")
    if _sequence(components) and len(components) <= 8:
        if ref["kind"] == "composite":
            require(bool(components), "Composite requires original constituent references")
        for child in components:
            _source_ref(checks, child, channel, pid, depth + 1)
    return all(c["status"] == "Pass" for c in checks.checks[start:])


def _refused_live_sample_facts(checks: _Checks, value: Any, channel: str, pid: str) -> None:
    """Compare only fixed known leaves; never admit or walk the refused descriptor."""
    if not _mapping(value) or len(value) > 128:
        return
    ref, sample = value.get("source_ref"), value.get("draw_sample")
    if not (_mapping(ref) and len(ref) <= 128 and _mapping(sample) and len(sample) <= 128):
        return
    kind = ref.get("kind")
    if not isinstance(kind, str) or kind != "live":
        return
    identity = ref.get("identity")
    if _mapping(identity) and len(identity) <= 128:
        run, canonical = sample.get("run"), identity.get("run")
        # Bound strings before _text's whitespace inspection on this refusal path.
        if (isinstance(run, str) and len(run) <= 512 and _text(run)
                and isinstance(canonical, str) and len(canonical) <= 512 and _text(canonical)):
            checks.expect(run == canonical, "source", "Live sample must belong to this source run", channel=channel, presentation_id=pid)
    digest, supplied = sample.get("payload_sha256"), value.get("payload_sha256")
    if _digest(digest) and _digest(supplied):
        checks.expect(digest == supplied, "source", "Live sample must bind the supplied payload", channel=channel, presentation_id=pid)


def _source(checks: _Checks, value: Any, channel: str, pid: str) -> bool:
    start = len(checks.checks)
    if not _mapping(value) or not _json_facts(value):
        checks.add("Unverified", "source", "Missing or unbounded source projection", channel=channel, presentation_id=pid)
        _refused_live_sample_facts(checks, value, channel, pid)
        return False
    ref = value.get("source_ref")
    valid = _source_ref(checks, ref, channel, pid)
    if not (_text(value.get("key")) and _digest(value.get("payload_sha256"))
            and _dimensions(value.get("dimensions_px")) and _affine(value.get("source_to_view"))):
        checks.add("Unverified", "source", "Missing source payload dimensions/digest/transform", channel=channel, presentation_id=pid)
        valid = False
    # Known aliases cannot be suppressed by unknown time/media/other lineage.
    if _mapping(ref):
        identity = ref.get("identity") if _mapping(ref.get("identity")) else {}
        provenance = ref.get("provenance") if _mapping(ref.get("provenance")) else {}
        resource = identity.get("resource")
        aliases = (
            ("key", ref.get("key"), _text), ("payload_sha256", ref.get("digest"), _digest),
            ("resource_id", resource.get("id") if _mapping(resource) else None, _text),
            ("producer_id", provenance.get("producer"), _text),
            ("revision", identity.get("revision"), _version), ("selector", identity.get("member"), _text),
            ("source_to_view", provenance.get("sourceToView"), _affine),
        )
        for key, canonical, validator in aliases:
            if key in value and validator(canonical):
                if not validator(value[key]):
                    checks.add("Unverified", "source", "Invalid supplied source alias", channel=channel, presentation_id=pid, field=key)
                else:
                    checks.expect(value[key] == canonical, "source", "Known alias cannot contradict canonical source",
                                  channel=channel, presentation_id=pid, field=key)
        if ref.get("kind") == "live":
            sample = value.get("draw_sample")
            if not (_mapping(sample) and all(_text(sample.get(k)) for k in ("run", "boot"))
                    and _version(sample.get("seq")) and _version(sample.get("frame_id"))
                    and _digest(sample.get("payload_sha256"))):
                checks.add("Unverified", "source", "Live draw needs an independently bound frame sample", channel=channel, presentation_id=pid)
            # Incomplete samples still retain independently known contradictions.
            if _mapping(sample):
                if _text(sample.get("run")) and _text(identity.get("run")):
                    checks.expect(sample["run"] == identity["run"], "source", "Live sample must belong to this source run", channel=channel, presentation_id=pid)
                if _digest(sample.get("payload_sha256")) and _digest(value.get("payload_sha256")):
                    checks.expect(sample["payload_sha256"] == value["payload_sha256"], "source", "Live sample must bind the supplied payload", channel=channel, presentation_id=pid)
    return valid and all(c["status"] == "Pass" for c in checks.checks[start:])


def _immutable_ref(ref: Mapping) -> dict:
    # Availability and locators renew; neither replaces producer or child identity.
    identity = {k: ref[k] for k in ("key", "kind", "media", "identity", "provenance")}
    identity["components"] = [_immutable_ref(c) for c in ref.get("components", [])]
    if "digest" in ref:
        identity["digest"] = ref["digest"]
    return identity


def _source_equal(left: Mapping, right: Mapping) -> bool:
    def projection(source: Mapping) -> dict:
        return {k: _immutable_ref(v) if k == "source_ref" else v
                for k, v in source.items() if k != "locator"}
    return projection(left) == projection(right)


def _known_source_conflict(left: Any, right: Any, role: str = "source") -> bool:
    """Compare common known facts with the canonical discriminator's unknown rules.

    Only SourceRef.media uses the string sentinel. The same literal in resource,
    member, producer or any other identity remains an ordinary known value.
    """
    if left is None or right is None:
        return False
    if _mapping(left) and _mapping(right):
        for key in left.keys() & right.keys():
            if role in ("source", "ref") and key == "locator":
                continue
            if role == "ref" and (key == "availability" or
                                  (key == "media" and (left[key] == "unknown" or right[key] == "unknown"))):
                continue
            if role == "provenance" and key == "unknown":
                continue  # retained reason map, not a contradictory identity fact
            child_role = ("ref" if role == "source" and key == "source_ref" else
                          "components" if role == "ref" and key == "components" else
                          "provenance" if role == "ref" and key == "provenance" else "other")
            if _known_source_conflict(left[key], right[key], child_role):
                return True
        return False
    if _sequence(left) and _sequence(right):
        child_role = "ref" if role == "components" else "other"
        return len(left) != len(right) or any(_known_source_conflict(a, b, child_role) for a, b in zip(left, right))
    if _number(left) and _number(right):
        return left != right
    return type(left) is not type(right) or left != right


def _freshness(checks: _Checks, channels: Mapping) -> None:
    command, request = channels["command"], channels["request"]
    baseline = command.get("baseline_report_sequence") if command is not None else None
    if not _version(baseline):
        checks.add("Unverified", "correlation", "Missing command report-sequence baseline")
    request_sequence = request.get("report_sequence") if request is not None else None
    if not _version(request_sequence):
        checks.add("Unverified", "correlation", "Missing request-pinned report sequence")
    elif _version(baseline):
        checks.expect(request_sequence == baseline, "correlation", "Command baseline must be the request-pinned report")
    for name in ("ack", "report", "browser"):
        channel = channels[name]
        if channel is None:
            continue
        sequence = channel.get("report_sequence")
        if not _version(sequence):
            checks.add("Unverified", "correlation", "Missing actual report sequence", channel=name)
        elif _version(baseline):
            checks.expect(sequence > baseline, "correlation", "Collected state must be causally newer than command baseline",
                          channel=name, expected_after=baseline, observed=sequence)


def _target_confirmation(checks: _Checks, channels: Mapping, target: Mapping, closing: bool) -> None:
    command = channels["command"]
    if command is None:
        return
    pid, baseline, scope = target["presentation_id"], command.get("baseline_report_sequence"), command.get("scope")
    token, draw, before = target.get("expected_content_token"), target.get("expected_draw_revision"), target.get("baseline_draw_revision")
    if not closing:
        if not _text(token) or not _version(draw) or not _version(before):
            checks.add("Unverified", "correlation", "Target needs content token and before/after draw fence", presentation_id=pid)
        else:
            checks.expect(draw >= before if command.get("noop") is True else draw > before,
                          "correlation", "A draw must advance; no-op still requires fresh correlated observation", presentation_id=pid)
    for name in ("ack", "report", "browser"):
        channel = channels[name]
        if channel is None:
            continue
        values = channel.get("results" if name == "ack" else "command_results")
        if not _sequence(values) or len(values) > 24:
            checks.add("Unverified", "correlation", "Missing bounded target-specific command results", channel=name, presentation_id=pid)
            continue
        matches = [v for v in values if _mapping(v) and v.get("presentation_id") == pid]
        if len(matches) != 1:
            checks.add("Fail", "correlation", "Command result must identify this target exactly once", channel=name, presentation_id=pid)
            continue
        result = matches[0]
        for key, validator in (("lifetime", _lifetime), ("source_generation", _version)):
            if not validator(target.get(key)) or not validator(result.get(key)):
                checks.add("Unverified", "correlation", "Missing target-result incarnation fence", channel=name, presentation_id=pid, field=key)
            else:
                checks.expect(type(target[key]) is type(result[key]) and target[key] == result[key],
                              "correlation", "Result belongs to the exact target incarnation", channel=name, presentation_id=pid, field=key)
        for key in ("request_id", "action_id", "command_id"):
            if not _text(result.get(key)) or not _mapping(scope) or not _text(scope.get(key)):
                checks.add("Unverified", "correlation", "Missing result occurrence identity", channel=name, presentation_id=pid, field=key)
            else:
                checks.expect(result[key] == scope[key], "correlation", "Target result belongs to this occurrence", channel=name, presentation_id=pid, field=key)
        sequence = result.get("report_sequence")
        if not _version(sequence):
            checks.add("Unverified", "correlation", "Missing target-result report sequence", channel=name, presentation_id=pid)
        elif _version(baseline):
            checks.expect(sequence > baseline, "correlation", "Target result must follow baseline", channel=name, presentation_id=pid)
            if _version(channel.get("report_sequence")):
                checks.expect(sequence <= channel["report_sequence"], "correlation", "Envelope cannot precede its target result", channel=name, presentation_id=pid)
        if not _text(result.get("state")):
            checks.add("Unverified", "render", "Missing target-specific renderer result", channel=name, presentation_id=pid)
        else:
            checks.expect(result["state"] == ("closed" if closing else "drawn"), "render", "Each target must confirm the requested lifecycle state", channel=name, presentation_id=pid)
        if not closing:
            for key, expected, validator in (("content_token", token, _text), ("draw_revision", draw, _version)):
                if not validator(expected) or not validator(result.get(key)):
                    checks.add("Unverified", "correlation", "Missing target-result content/draw fence", channel=name, presentation_id=pid, field=key)
                else:
                    checks.expect(result[key] == expected, "correlation", "Target result must confirm expected content/draw", channel=name, presentation_id=pid, field=key)


def _draw_binding(checks: _Checks, window: Mapping, target: Mapping, command: Mapping | None, channel: str, pid: str) -> None:
    scope = command.get("scope") if command is not None else None
    for key, expected, validator in (
        ("content_token", target.get("expected_content_token"), _text),
        ("drawn_content_token", target.get("expected_content_token"), _text),
        ("draw_revision", target.get("expected_draw_revision"), _version),
        ("last_command_id", scope.get("command_id") if _mapping(scope) else None, _text),
    ):
        if not validator(expected) or not validator(window.get(key)):
            checks.add("Unverified", "correlation", "Missing independent draw binding", channel=channel, presentation_id=pid, field=key)
        else:
            checks.expect(window[key] == expected, "correlation", "Observed pixels must belong to this command's content/draw",
                          channel=channel, presentation_id=pid, field=key, expected=expected, observed=window[key])


def _causality(record: Mapping, checks: Sequence) -> dict:
    problems = [c for c in checks if c["status"] == "Fail" and not c.get("derived_from_task")]
    if not problems:
        problems = [c for c in checks if c["status"] == "Unverified" and not c.get("derived_from_task")]
    boundaries = {c["boundary"] for c in problems}
    result = {"status": "Unknown" if problems else "NoFailure", "first_boundary": None,
              "first_failure_id": None, "failure_events": deepcopy(record.get("failure_events", []))}
    events = record.get("failure_events")
    command = record.get("command")
    scope = command.get("scope") if _mapping(command) else None
    # A lone diagnostic boundary can be classified without pretending it is an
    # observed event/root cause. Preserve the original single-boundary API.
    if len(boundaries) == 1:
        result.update(first_boundary=next(iter(boundaries)), classification="SingleBoundary")
    if not problems or not _sequence(events) or not events or len(events) > 128 or not _scope_valid(scope) or not _text(record.get("clock_domain")):
        return result
    observed = {}
    for diagnostic in problems:
        failure_id = diagnostic.get("failure_id")
        if not _text(failure_id) or not _number(diagnostic.get("at_ms")) or failure_id in observed:
            # Distinct occurrences cannot be collapsed even when their times agree.
            return result
        observed[failure_id] = diagnostic
    graph = {}
    for event in events:
        if not _mapping(event) or not _text(event.get("id")) or event["id"] in graph:
            return result
        evidence = observed.get(event["id"])
        parents = event.get("caused_by")
        event_scope = event.get("scope")
        if (evidence is None or event.get("boundary") != evidence["boundary"]
                or event.get("at_ms") != evidence["at_ms"] or not _scope_valid(event_scope)
                or any(type(event_scope[k]) is not type(scope[k]) or event_scope[k] != scope[k] for k in SCOPE_FIELDS)
                or event.get("command_id") != scope["command_id"]
                or not _sequence(parents) or not all(_text(p) for p in parents) or len(set(parents)) != len(parents)
                or (evidence.get("presentation_id") is not None and event.get("presentation_id") != evidence["presentation_id"])
                or event.get("clock_domain", record["clock_domain"]) != record["clock_domain"]):
            return result
        if evidence["boundary"] == "operator":
            operator = evidence.get("observed")
            if (not _mapping(operator) or any(not _text(operator.get(k)) or event.get(k) != operator[k]
                                             for k in ("operator_id", "action_id"))):
                return result
        graph[event["id"]] = event
    # Every independent failed boundary must be represented, not merely a convenient
    # selected source+timeout subset while some operator failure is omitted.
    if ({event["boundary"] for event in graph.values()} != boundaries
            or any(c.get("failure_id") not in graph for c in problems)):
        return result
    for event in graph.values():
        if any(parent not in graph or graph[parent]["at_ms"] > event["at_ms"] for parent in event["caused_by"]):
            return result
    roots = [eid for eid, event in graph.items() if not event["caused_by"]]
    if len(roots) != 1:
        return result
    reached, visiting = set(), set()
    def visit(eid: str) -> bool:
        if eid in visiting:
            return False
        if eid in reached:
            return True
        visiting.add(eid)
        if not all(visit(parent) for parent in graph[eid]["caused_by"]):
            return False
        visiting.remove(eid)
        reached.add(eid)
        return True
    if not all(visit(eid) for eid in graph):
        return result
    root = roots[0]
    result.update(status="Established", first_boundary=graph[root]["boundary"], first_failure_id=root)
    return result


def _windows(checks: _Checks, channel: Mapping | None, name: str) -> dict[str, Mapping]:
    if channel is None:
        return {}
    values = channel.get("windows")
    if not _sequence(values) or len(values) > 40:
        checks.add("Unverified", "evidence", "Missing or unbounded window observation", channel=name)
        return {}
    out = {}
    for window in values:
        if not _mapping(window) or not _text(window.get("presentation_id")):
            checks.add("Unverified", "correlation", "Window lacks presentation identity", channel=name)
            continue
        pid = window["presentation_id"]
        checks.expect(pid not in out, "correlation", "Presentation observations must be unique", channel=name, presentation_id=pid)
        out[pid] = window
    return out


def _late_evidence(record: Mapping, boundary_at: float | None) -> list[dict]:
    if boundary_at is None:
        return []
    return [{"channel": name, "at_ms": record[name]["at_ms"]}
            for name in ("ack", "report", "browser")
            if _mapping(record.get(name)) and _number(record[name].get("at_ms"))
            and record[name]["at_ms"] > boundary_at]


def _terminal_and_budget(checks: _Checks, record: Mapping, command: Mapping | None) -> list[dict]:
    terminal = record.get("terminal")
    deadline = command.get("confirm_deadline_at_ms") if command is not None else None
    cutoff = deadline if _number(deadline) else None
    if terminal is not None:
        if not _mapping(terminal) or not _number(terminal.get("at_ms")):
            checks.add("Unverified", "terminal", "Terminal state lacks a measured time")
        elif _failed_terminal(terminal):
            cutoff = terminal["at_ms"]
            checks.add("Fail", "terminal", "Terminal failure cannot become timely success", observed=deepcopy(terminal),
                       failure_id=terminal.get("failure_id"), at_ms=terminal["at_ms"])
        elif terminal.get("state") not in ("completed", "confirmed", "succeeded"):
            checks.add("Unverified", "terminal", "Unknown terminal state", observed=deepcopy(terminal))
    late = _late_evidence(record, cutoff)
    failed = _failed_terminal(terminal)
    if not failed and late:
        checks.add("Fail", "terminal", "Confirmation evidence arrived after the frozen deadline", observed=late)
    if _mapping(terminal) and terminal.get("state") in ("completed", "confirmed", "succeeded") and _number(terminal.get("at_ms")):
        times = [record[name]["at_ms"] for name in ("ack", "report", "browser")
                 if _mapping(record.get(name)) and _number(record[name].get("at_ms"))]
        if times:
            checks.expect(terminal["at_ms"] >= max(times), "terminal", "Successful termination must follow its independently observed result")
    dispatches = record.get("model_dispatches")
    if not _sequence(dispatches) or any(not _mapping(d) or not _text(d.get("dispatch_id")) for d in dispatches):
        checks.add("Unverified", "budget", "Missing actual model-dispatch trace")
    else:
        ids = [d["dispatch_id"] for d in dispatches]
        checks.expect(len(set(ids)) == len(ids), "budget", "Each actual dispatch needs a unique occurrence identity")
        checks.expect(len(dispatches) <= MAX_CALLS, "budget", "All paths share the actual-dispatch budget",
                      expected=MAX_CALLS, observed=len(dispatches))
    return late


def _geometry(checks: _Checks, reported: Mapping, measured: Mapping, pid: str) -> None:
    for key in ("outer_rect_css", "content_rect_css"):
        expected, observed = reported.get(key), measured.get(key)
        if not _vector(expected, 4) or not _vector(observed, 4) or any(v < 0 for v in (*expected[2:], *observed[2:])):
            checks.add("Unverified", "geometry", "Missing finite CSS rectangle", presentation_id=pid, field=key)
        else:
            checks.expect(all(abs(a - b) <= RECT_TOLERANCE_CSS for a, b in zip(expected, observed)),
                          "geometry", "Server geometry must agree with independent DOM measurements",
                          presentation_id=pid, field=key, expected=list(expected), observed=list(observed))


def _ownership(checks: _Checks, target: Mapping, windows: list[tuple[str, Mapping]], pid: str, command: Mapping | None) -> None:
    touched = target.get("touched")
    if not _mapping(touched):
        checks.add("Unverified", "ownership", "Missing touched-property fence", presentation_id=pid)
        return
    for prop, fence in touched.items():
        if (not _mapping(fence) or not _version(fence.get("base_version"))
                or not _version(fence.get("committed_version")) or not _text(fence.get("owner"))):
            checks.add("Unverified", "ownership", "Incomplete property fence", presentation_id=pid, property=prop)
            continue
        scope = command.get("scope") if command is not None else None
        if _mapping(scope) and _text(scope.get("command_id")):
            checks.expect(fence["owner"] == scope["command_id"], "ownership", "Touched property owner must be this command",
                          presentation_id=pid, property=prop)
        checks.expect(fence["committed_version"] >= fence["base_version"], "ownership", "Property versions cannot go backwards",
                      presentation_id=pid, property=prop)
        for channel, window in windows:
            versions = window.get("property_versions")
            observed = versions.get(prop) if _mapping(versions) else None
            if not _mapping(observed) or not _version(observed.get("version")) or not _text(observed.get("owner")):
                checks.add("Unverified", "ownership", "Missing observed touched-property ownership",
                           channel=channel, presentation_id=pid, property=prop)
            else:
                checks.expect(observed["version"] == fence["committed_version"] and observed["owner"] == fence["owner"],
                              "ownership", "Same-valued user edits still supersede a command",
                              channel=channel, presentation_id=pid, property=prop,
                              expected={"version": fence["committed_version"], "owner": fence["owner"]}, observed=dict(observed))


def _render(checks: _Checks, window: Mapping, channel: str, pid: str) -> None:
    required = ("visible", "occluded", "load_state", "draw_revision", "natural_dimensions_px")
    if not all(k in window for k in required):
        checks.add("Unverified", "render", "Missing decoded/drawn observation", channel=channel, presentation_id=pid)
        return
    checks.expect(window["visible"] is True and window["occluded"] is False and window["load_state"] == "drawn"
                  and _number(window["draw_revision"]) and window["draw_revision"] > 0
                  and _dimensions(window["natural_dimensions_px"]),
                  "render", "Assigned content is not proof of a visible decoded picture", channel=channel, presentation_id=pid)
    source = window.get("source")
    if _mapping(source) and _dimensions(source.get("dimensions_px")) and _dimensions(window["natural_dimensions_px"]):
        checks.expect(list(window["natural_dimensions_px"]) == list(source["dimensions_px"]),
                      "render", "Decoded dimensions must match the bound served payload", channel=channel, presentation_id=pid)


def _same_sources(expected: Sequence, observed: Sequence, *, exact: bool = True) -> bool:
    # Bipartite multiset matching preserves duplicate sources. Partial compatibility
    # is not an equivalence relation: greedy matching could falsely reject an unknown
    # descriptor that can match several observed sources alongside one exact source.
    if len(expected) != len(observed):
        return False
    edges = [[n for n, candidate in enumerate(observed)
              if (_source_equal(source, candidate) if exact else not _known_source_conflict(source, candidate))]
             for source in expected]
    matched = {}
    def assign(index: int, visited: set) -> bool:
        for candidate in edges[index]:
            if candidate in visited:
                continue
            visited.add(candidate)
            if candidate not in matched or assign(matched[candidate], visited):
                matched[candidate] = index
                return True
        return False
    return all(assign(index, set()) for index in range(len(expected)))


def _independent_views(checks: _Checks, predicate: Mapping, windows: list[Mapping]) -> None:
    sources = predicate.get("sources")
    if not _sequence(sources) or not sources or len(sources) > 24:
        checks.add("Unverified", "goal", "Independent-views goal lacks bounded source identities")
        return
    valid = [_source(checks, s, "goal", str(n)) for n, s in enumerate(sources)]
    observed_valid = [_source(checks, w.get("source"), "browser-goal", str(n)) for n, w in enumerate(windows)]
    if all(valid) and all(observed_valid):
        checks.expect(_same_sources(sources, [w["source"] for w in windows]), "source",
                      "The requested constituent multiset must match exact retained identities",
                      expected=deepcopy(list(sources)), observed=[deepcopy(w.get("source")) for w in windows])
    elif (len(sources) == len(windows) and all(_mapping(s) and _json_facts(s) for s in sources)
          and all(_mapping(w.get("source")) and _json_facts(w["source"]) for w in windows)):
        checks.expect(_same_sources(sources, [w["source"] for w in windows], exact=False), "source",
                      "Unknown source facts cannot erase independently known constituent mismatches")
    pids, roots = [w.get("presentation_id") for w in windows], [w.get("root_id") for w in windows]
    if all(_text(r) for r in roots):
        checks.expect(len(windows) == len(sources) and len(set(pids)) == len(sources) and len(set(roots)) == len(sources),
                      "topology", "Each requested view needs a separate presentation and draggable root", observed=roots)
    else:
        checks.add("Unverified", "topology", "Missing independent window-root identities")
    minimum = predicate.get("minimum_content_css")
    if not _vector(minimum, 2) or any(n <= 0 for n in minimum) or not isinstance(predicate.get("explicit_shrink"), bool):
        checks.add("Unverified", "usability", "Missing frozen content floor or explicit-shrink intent")
    elif not predicate["explicit_shrink"]:
        minimum = [max(floor, supplied) for floor, supplied in zip(DESKTOP_MINIMUM_CONTENT_CSS, minimum)]
        for window in windows:
            rect = window.get("content_rect_css")
            if _vector(rect, 4):
                checks.expect(rect[2] >= minimum[0] and rect[3] >= minimum[1], "usability",
                              "Independent views need useful content area unless explicitly shrunk",
                              presentation_id=window["presentation_id"], expected=list(minimum), observed=list(rect[2:]))
    for window in windows:
        controls = window.get("controls")
        if not _mapping(controls) or not all(k in controls for k in ("move", "resize", "zoom", "close")):
            checks.add("Unverified", "controls", "Missing independent control ownership", presentation_id=window["presentation_id"])
        else:
            checks.expect(all(controls[k] == window["presentation_id"] for k in ("move", "resize", "zoom", "close")),
                          "controls", "Each view's controls must act on that view", presentation_id=window["presentation_id"], observed=dict(controls))


def _closed(checks: _Checks, predicate: Mapping, channels: Mapping, windows: Mapping, targets: Mapping) -> None:
    ids = predicate.get("presentation_ids")
    if not _sequence(ids) or not ids or not all(_text(pid) and pid in targets for pid in ids):
        checks.add("Unverified", "goal", "Close predicate lacks command-correlated presentation targets")
        return
    for channel in ("report", "browser"):
        observed = channels.get(channel)
        if observed is None:
            checks.add("Unverified", "closure", "No independent closure observation", channel=channel)
            continue
        tombstones = observed.get("tombstones", [])
        scope = observed.get("scope")
        command_id = scope.get("command_id") if _mapping(scope) else None
        for pid in ids:
            target = targets[pid]
            explicit = _sequence(tombstones) and any(
                _mapping(t) and t.get("presentation_id") == pid and t.get("lifetime") == target.get("lifetime")
                and t.get("source_generation") == target.get("source_generation")
                and _text(command_id) and t.get("command_id") == command_id for t in tombstones)
            if pid in windows[channel]:
                checks.add("Fail", "closure", "Target remains in the renderer", channel=channel, presentation_id=pid)
            elif observed.get("complete") is True or explicit:
                checks.add("Pass", "closure", "Complete absence or explicit correlated tombstone", channel=channel, presentation_id=pid)
            else:
                checks.add("Unverified", "closure", "Absence in a truncated report cannot establish closure", channel=channel, presentation_id=pid)


def _apply(affine: Sequence, point: Sequence) -> tuple[float, float]:
    a, b, c, d, e, f = affine
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def _inverse(affine: Sequence, point: Sequence) -> tuple[float, float]:
    a, b, c, d, e, f = affine
    determinant = a * d - b * c
    x, y = point[0] - e, point[1] - f
    return (d * x - c * y) / determinant, (-b * x + a * y) / determinant


def _landmark(checks: _Checks, predicate: Mapping, windows: Mapping, targets: Mapping) -> None:
    pid, point, requested = predicate.get("presentation_id"), predicate.get("source_px"), predicate.get("maximum_error_source_px")
    if not _text(pid) or not _vector(point, 2) or not _number(requested) or requested < 0:
        checks.add("Unverified", "goal", "Invalid bounded source-pixel landmark predicate")
        return
    if pid not in targets:
        checks.add("Unverified", "goal", "Landmark needs an exact admitted target receiving the ordinary source/draw fences",
                   presentation_id=pid)
        return
    reported, measured = windows["report"].get(pid), windows["browser"].get(pid)
    if reported is None or measured is None:
        checks.add("Unverified", "transform", "Missing independent landmark view", presentation_id=pid)
        return
    source = reported.get("source")
    transform = reported.get("display_transform")
    observed_transform = measured.get("display_transform")
    landmarks = measured.get("landmarks_css")
    if (not _mapping(source) or not _affine(source.get("source_to_view")) or not _affine(transform)
            or not _affine(observed_transform) or not _sequence(landmarks)):
        checks.add("Unverified", "transform", "Missing invertible source/display affine or independent pixels", presentation_id=pid)
        return
    checks.expect(transform == observed_transform, "transform", "Independent display must confirm the transform", presentation_id=pid)
    expected = _apply(transform, _apply(source["source_to_view"], point))
    matches = [p for p in landmarks if _mapping(p) and p.get("source_px") == point]
    if len(matches) != 1 or not _vector(matches[0].get("observed_css"), 2):
        checks.add("Unverified", "transform", "Landmark must identify this exact source point once", presentation_id=pid)
        return
    observed = matches[0]["observed_css"]
    reconstructed = _inverse(source["source_to_view"], _inverse(transform, observed))
    if not _vector(expected, 2) or not _vector(reconstructed, 2):
        checks.add("Unverified", "transform", "Transform cannot produce finite source-space measurements", presentation_id=pid)
        return
    source_error, css_error = math.dist(point, reconstructed), math.dist(expected, observed)
    tolerance = min(LANDMARK_TOLERANCE_SOURCE_PX, requested)
    checks.expect(source_error <= tolerance, "transform", "Independent CSS point must agree in source pixels",
                  presentation_id=pid, expected=list(expected), observed=list(observed),
                  source_error_px=source_error, css_error_px=css_error, maximum_error_source_px=tolerance,
                  source_to_view=list(source["source_to_view"]), display_transform=list(transform))


def evaluate_checkpoint(record: Mapping[str, Any]) -> dict:
    """Evaluate a frozen functional checkpoint, retaining every known mismatch.

    Fail dominates missing evidence. Diagnostic order is separate from measured
    causality; ambiguous independent failures have no invented first boundary.
    Missing fields and unsupported goals are Unverified. Inputs are never modified. V1 is a read-only
    oracle, not a claim that the application's runtime adapter is already connected.
    """
    checks = _Checks()
    if not _mapping(record):
        checks.add("Unverified", "schema", "Checkpoint must be an adapter record")
        return checks.result({})
    if record.get("schema") != SCHEMA:
        checks.add("Unverified", "schema", "Unsupported observation schema", observed=record.get("schema"))
        return checks.result(record)
    if not _text(record.get("evidence_class")) or not _digest(record.get("candidate_sha"), 40):
        checks.add("Unverified", "schema", "Evidence class and candidate SHA must be explicit")
    if not all(k in record for k in ("model", "effort", "timing_scope")):
        checks.add("Unverified", "schema", "Record actual model/effort or explicit absence and timing scope")
    channels = {name: record.get(name) if _mapping(record.get(name)) else None
                for name in ("request", "command", "ack", "report", "browser")}
    for name, channel in channels.items():
        if channel is None:
            checks.add("Unverified", "evidence", "Missing independent checkpoint channel", channel=name)
    for name in ("report", "browser"):
        if channels[name] is not None and channels[name].get("visible") is not True:
            checks.add("Unverified", "evidence", "Hidden page state cannot certify visible pixels", channel=name)
    request, command = channels["request"], channels["command"]
    origins = []
    for name, channel in channels.items():
        if channel is not None or name == "request":
            origin = _scope(checks, channel.get("scope") if channel is not None else None, name)
            if origin is not None:
                for reference, expected in origins:
                    _compare_scope(checks, origin, expected, name, reference)
                origins.append((name, origin))
    _freshness(checks, channels)
    if not _text(record.get("clock_domain")):
        checks.add("Unverified", "correlation", "Checkpoint needs one declared normalized clock domain")
    if command is not None:
        issued, deadline = command.get("issued_at_ms"), command.get("confirm_deadline_at_ms")
        if not _number(issued) or not _number(deadline) or deadline < issued:
            checks.add("Unverified", "correlation", "Missing frozen command timing bounds")
        else:
            for name in ("ack", "report", "browser"):
                channel = channels[name]
                if channel is not None:
                    at = channel.get("at_ms")
                    if not _number(at):
                        checks.add("Unverified", "correlation", "Missing observation time", channel=name)
                    else:
                        checks.expect(at >= issued, "correlation", "Observation must follow this command", channel=name)
    late = _terminal_and_budget(checks, record, command)
    goal = record.get("goal")
    predicates = goal.get("predicates") if _mapping(goal) else None
    if not _mapping(goal) or goal.get("origin") != "planner":
        checks.add("Unverified", "goal", "Only the planner's selected goal can certify user intent")
    if not _sequence(predicates) or not predicates or len(predicates) > 16:
        checks.add("Unverified", "goal", "Missing bounded closed postconditions")
        predicates = []
    for predicate in predicates:
        if not _predicate(predicate):
            checks.add("Unverified", "goal", "Unknown postcondition cannot be silently ignored", observed=deepcopy(predicate))
    known = [p for p in predicates if _predicate(p)]
    raw_targets = command.get("targets") if command is not None else None
    targets = {}
    if not _sequence(raw_targets) or not raw_targets or len(raw_targets) > 24:
        checks.add("Unverified", "correlation", "Missing bounded command targets")
    else:
        for target in raw_targets:
            if not _mapping(target) or not _text(target.get("presentation_id")):
                checks.add("Unverified", "correlation", "Invalid command target")
                continue
            pid = target["presentation_id"]
            checks.expect(pid not in targets, "correlation", "Command target occurrences must be unique", presentation_id=pid)
            if not _lifetime(target.get("lifetime")) or not _version(target.get("source_generation")):
                checks.add("Unverified", "correlation", "Command target requires explicit lifetime/source generations", presentation_id=pid)
            targets[pid] = target
    windows = {name: _windows(checks, channels[name], name) for name in ("report", "browser")}
    closing = {pid for p in known if p["kind"] == "closed" and _sequence(p.get("presentation_ids"))
               for pid in p["presentation_ids"] if _text(pid)}
    for pid, target in targets.items():
        _target_confirmation(checks, channels, target, pid in closing)
        found = [(name, windows[name][pid]) for name in ("report", "browser") if pid in windows[name]]
        for name, window in found:
            for key in ("lifetime", "source_generation"):
                validator = _lifetime if key == "lifetime" else _version
                if not validator(target.get(key)) or not validator(window.get(key)):
                    checks.add("Unverified", "correlation", "Missing target generation", channel=name, presentation_id=pid, field=key)
                else:
                    checks.expect(type(window[key]) is type(target[key]) and window[key] == target[key], "correlation", "Presentation ID cannot substitute a new generation",
                                  channel=name, presentation_id=pid, field=key, expected=target[key], observed=window[key])
        if pid in closing:
            continue
        for name in ("report", "browser"):
            if channels[name] is not None and pid not in windows[name]:
                checks.add("Fail" if channels[name].get("complete") is True else "Unverified", "render",
                           "Target view missing from renderer observation", channel=name, presentation_id=pid)
        source_validity = {}
        for name, window in found:
            _draw_binding(checks, window, target, command, name, pid)
            valid = _source(checks, window.get("source"), name, pid)
            source_validity[name] = valid
            if name == "browser" and _mapping(window.get("source")) and _digest(window["source"].get("payload_sha256")):
                digest = window.get("fetched_payload_sha256")
                if not _digest(digest):
                    checks.add("Unverified", "source", "Missing independent served-payload digest", presentation_id=pid)
                else:
                    checks.expect(digest == window["source"]["payload_sha256"], "source", "Fetched bytes must belong to the displayed source",
                                  presentation_id=pid, expected=window["source"]["payload_sha256"], observed=digest,
                                  failure_id=window.get("failure_id"), at_ms=window.get("payload_at_ms"))
        if len(found) == 2:
            reported, measured = windows["report"][pid], windows["browser"][pid]
            if all(source_validity.values()):
                checks.expect(_source_equal(reported["source"], measured["source"]), "source", "Independent display must preserve every projected immutable source fact",
                              presentation_id=pid, expected=deepcopy(reported["source"]), observed=deepcopy(measured["source"]))
            elif (all(_mapping(w.get("source")) and _json_facts(w["source"]) for w in (reported, measured))):
                checks.expect(not _known_source_conflict(reported["source"], measured["source"]), "source",
                              "Incomplete identity cannot erase known source contradictions", presentation_id=pid)
            _geometry(checks, reported, measured, pid)
        for name, window in found:
            _render(checks, window, name, pid)
        _ownership(checks, target, found, pid, command)
    ack = channels["ack"]
    if ack is not None:
        checks.expect(ack.get("state") == ("closed" if closing == set(targets) and closing else "drawn"),
                      "render", "Dispatch or commit alone does not confirm the requested renderer state", observed=ack.get("state"))
    for predicate in known:
        if predicate["kind"] == "independent_views":
            if channels["browser"] is not None:
                _independent_views(checks, predicate, [windows["browser"][pid] for pid in targets if pid in windows["browser"]])
        elif predicate["kind"] == "closed":
            _closed(checks, predicate, channels, windows, targets)
        elif predicate["kind"] == "landmark":
            _landmark(checks, predicate, windows, targets)
    operators = record.get("operator_results")
    if not _sequence(operators):
        checks.add("Unverified", "operator", "Missing per-operator outcomes")
    else:
        for n, operator in enumerate(operators):
            if not _mapping(operator) or not isinstance(operator.get("ok"), bool):
                checks.add("Unverified", "operator", "Operator outcome must be explicit", occurrence=n)
            elif operator["ok"] is False:
                checks.add("Fail", "operator", "Failed requested operators are retained even if other steps succeed", occurrence=n, observed=deepcopy(operator),
                           failure_id=operator.get("failure_id"), at_ms=operator.get("at_ms"))
            else:
                checks.add("Pass", "operator", "Successful operator fact retained separately from task fulfillment", occurrence=n, observed=deepcopy(operator))
    task = _status(checks.checks)
    task_boundaries = sorted({c["boundary"] for c in checks.checks if c["status"] != "Pass"})
    claims = record.get("claims")
    if not _sequence(claims):
        checks.add("Unverified", "claim", "Missing completion-claim observations")
    else:
        for n, claim in enumerate(claims):
            if not _mapping(claim) or not isinstance(claim.get("fulfilled"), bool) or not _number(claim.get("at_ms")):
                checks.add("Unverified", "claim", "Incomplete claim observation", occurrence=n)
            elif claim["fulfilled"]:
                evidence_times = [channels[name]["at_ms"] for name in ("ack", "report", "browser")
                                  if channels[name] is not None and _number(channels[name].get("at_ms"))]
                if evidence_times:
                    checks.expect(claim["at_ms"] >= max(evidence_times), "claim", "Completion cannot precede its independently observed result",
                                  occurrence=n, failure_id=claim.get("failure_id"), at_ms=claim["at_ms"])
                if task != "Pass":
                    checks.add(task, "claim", "Blanket fulfillment requires all selected outcomes and independent evidence",
                               occurrence=n, derived_from_task=True, task_boundaries=task_boundaries)
                else:
                    checks.add("Pass", "claim", "Fulfillment is supported by the selected task outcomes", occurrence=n)
            else:
                # This predicate concerns blanket fulfillment, not a textual judge
                # of other possible speech. Nonfulfillment does not certify the task.
                checks.add("Pass", "claim", "Partial/nonfulfillment claim does not hide task outcome", occurrence=n)
    return checks.result(record, late)


def summarize_coverage(plan: Sequence[Mapping], attempts: Sequence[Mapping]) -> dict:
    """Preserve denominators and attempts; never turn an unavailable case into Pass.

    Evidence classes count dispatched attempts, not successful scenarios. Only an
    explicit actual-browser Pass contributes to actual passed checkpoints; that
    count does not erase failed attempts, imply model quality or claim every mapped
    playbook scenario has been tested. Unplanned attempts are retained separately.
    """
    planned = {}
    for checkpoint in plan:
        if not _mapping(checkpoint) or not _text(checkpoint.get("checkpoint_id")):
            raise ValueError("Plan requires explicit checkpoint identities")
        cid, ids = checkpoint["checkpoint_id"], checkpoint.get("playbook_ids")
        if cid in planned or not _sequence(ids) or any(pid not in PLAYBOOK_IDS for pid in ids):
            raise ValueError("Plan requires unique checkpoints mapped to canonical playbook IDs")
        planned[cid] = tuple(ids)
    retained = []
    for attempt in attempts:
        if not _mapping(attempt) or not _text(attempt.get("checkpoint_id")) or not isinstance(attempt.get("dispatched"), bool):
            raise ValueError("Attempts require checkpoint identity and explicit dispatch state")
        retained.append(deepcopy(dict(attempt)))
    executed = {a["checkpoint_id"] for a in retained if a["dispatched"] and a["checkpoint_id"] in planned}
    independent = {a["checkpoint_id"] for a in retained if a["dispatched"] and a["checkpoint_id"] in planned
                   and a.get("status") in ("Pass", "Fail") and _display_class(a.get("evidence_class"))}
    passed = {a["checkpoint_id"] for a in retained if a["dispatched"] and a["checkpoint_id"] in planned
              and a.get("status") == "Pass" and _display_class(a.get("evidence_class"))}
    classes = Counter(a["evidence_class"] for a in retained if a["dispatched"] and _text(a.get("evidence_class")))
    mapped = {pid for ids in planned.values() for pid in ids}
    return {"planned_checkpoints": len(planned), "executed_checkpoints": len(executed),
            "independently_evidenced_checkpoints": len(independent), "evidence_classes": dict(classes),
            "registry_total": len(PLAYBOOK_IDS), "planned_playbook_ids": [pid for pid in PLAYBOOK_IDS if pid in mapped],
            "unexecuted_checkpoint_ids": [cid for cid in planned if cid not in executed],
            "failed_attempts": sum(a.get("status") == "Fail" for a in retained),
            "actual_passed_checkpoint_ids": [cid for cid in planned if cid in passed],
            "attempts": retained, "unplanned_attempts": [a for a in retained if a["checkpoint_id"] not in planned],
            "playbook": {pid: {
                "planned_checkpoint_ids": [cid for cid, ids in planned.items() if pid in ids],
                "executed_checkpoint_ids": [cid for cid, ids in planned.items() if pid in ids and cid in executed],
                "independently_evidenced_checkpoint_ids": [cid for cid, ids in planned.items() if pid in ids and cid in independent],
                "actual_passed_checkpoint_ids": [cid for cid, ids in planned.items() if pid in ids and cid in passed],
            } for pid in PLAYBOOK_IDS}}
