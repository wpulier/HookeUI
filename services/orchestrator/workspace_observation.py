"""Bounded workspace facts and request pins, with no observer or language resolver.

Screen adapters supply an accepted client and server-enriched SourceRefs. This module
validates value contracts, not authentication, source bytes, renderer truth or capture
authority. CSS geometry never becomes specimen geometry without an explicit transform.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


class ObservationError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _refuse(code: str, detail: str):
    raise ObservationError(code, detail)


@dataclass(frozen=True)
class ClientRef:
    workspace_id: str
    client_id: str
    page_epoch: str
    connection_epoch: str

    def __post_init__(self):
        for value in (self.workspace_id, self.client_id, self.page_epoch, self.connection_epoch):
            _id(value)

    def wire(self) -> dict:
        return dict(workspaceId=self.workspace_id, clientId=self.client_id,
                    pageEpoch=self.page_epoch, connectionEpoch=self.connection_epoch)


@dataclass(frozen=True)
class Limits:
    items: int = 40
    sources: int = 40
    targets: int = 16
    edges: int = 120
    candidates: int = 8
    contacts: int = 2
    wire_bytes: int = 256 * 1024
    normalized_bytes: int = 64 * 1024
    image_bytes: int = 6 * 1024 * 1024
    image_pixels: int = 4_000_000
    image_edge: int = 1920
    depth: int = 16
    nodes: int = 16384

    def __post_init__(self):
        for value in self.__dict__.values():
            _integer(value)
        # Callers may tighten the contract, never enlarge admitted collection/input bounds.
        ceilings = {"items": 40, "sources": 40, "targets": 16, "edges": 120,
                    "candidates": 8, "contacts": 2, "wire_bytes": 256 * 1024,
                    "normalized_bytes": 64 * 1024, "image_bytes": 6 * 1024 * 1024,
                    "image_pixels": 4_000_000, "image_edge": 1920, "depth": 16, "nodes": 16384}
        if any(getattr(self, key) > ceiling for key, ceiling in ceilings.items()):
            _refuse("INVALID_REPORT", "limits exceed the workspace contract")


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(v) for key, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(v) for key, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Observation:
    _data: Mapping
    client: ClientRef
    app_run_id: str
    received_at: float

    def wire(self) -> dict:
        return _thaw(self._data)


@dataclass(frozen=True)
class RequestSnapshot:
    observation: Observation
    snapshot_id: str
    timing: str
    requested_at: float
    report_age_ms: float
    availability: str

    def wire(self) -> dict:
        return dict(snapshotId=self.snapshot_id, timing=self.timing,
                    reportAgeMs=self.report_age_ms, availability=self.availability,
                    observation=self.observation.wire())


@dataclass(frozen=True)
class Binding:
    _data: Mapping

    def wire(self) -> dict:
        return _thaw(self._data)


def _mapping(value, code="INVALID_REPORT") -> dict:
    if not isinstance(value, dict):
        _refuse(code, "expected an object")
    return value


def _array(value) -> list:
    if not isinstance(value, list):
        _refuse("INVALID_REPORT", "expected an array")
    return value


def _id(value, code="INVALID_REPORT") -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        _refuse(code, "expected a nonempty bounded identity")
    return value


def _integer(value, code="INVALID_REPORT") -> int:
    if type(value) is not int or value < 0 or value > 2**53 - 1:
        _refuse(code, "expected a nonnegative safe integer")
    return value


def _lifetime(value) -> str | int:
    """Windows owns opaque incarnations; preserve legacy integers without coercion."""
    return _id(value) if type(value) is str else _integer(value)


def _number(value, *, nonnegative=False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        _refuse("INVALID_GEOMETRY", "expected a finite number, not a boolean")
    if nonnegative and value < 0:
        _refuse("INVALID_GEOMETRY", "expected a nonnegative number")
    return value


def _vector(value, length: int, *, dimensions=False) -> list:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        _refuse("INVALID_GEOMETRY", "unexpected vector length")
    result = [_number(v) for v in value]
    if dimensions and any(v <= 0 for v in result):
        _refuse("INVALID_GEOMETRY", "dimensions must be positive")
    return result


def _rect(value) -> list:
    result = _vector(value, 4)
    if result[2] < 0 or result[3] < 0:
        _refuse("INVALID_GEOMETRY", "negative rectangle dimensions")
    return result


def _boolean(value) -> bool:
    if type(value) is not bool:
        _refuse("INVALID_REPORT", "expected a boolean")
    return value


def _preflight(value, limits: Limits, *, omit_key: str | None = None):
    """Walk before serialization/copy, bounding depth, nodes and string allocation."""
    count = 0
    text_bytes = 0

    def visit(v, depth):
        nonlocal count, text_bytes
        count += 1
        if count > limits.nodes:
            _refuse("REPORT_TOO_LARGE", "input node limit")
        if depth > limits.depth:
            _refuse("INVALID_REPORT", "input nesting limit")
        if isinstance(v, dict):
            for key, child in v.items():
                if not isinstance(key, str):
                    _refuse("INVALID_REPORT", "non-string key")
                if depth == 0 and key == omit_key:
                    continue  # separately bounded leased capture bytes, never serialized
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(v, list):
            for child in v:
                visit(child, depth + 1)
        elif isinstance(v, str):
            # Bound before UTF-8 allocation too (UTF-8 is at least one byte/character).
            text_bytes += len(v)
            if text_bytes > limits.wire_bytes:
                _refuse("REPORT_TOO_LARGE", "input string limit")
        elif v is not None and type(v) not in (int, float, bool):
            _refuse("INVALID_REPORT", "non-JSON input")
        elif type(v) is int and abs(v) > 2**53 - 1:
            _refuse("INVALID_REPORT", "unsafe integer")

    visit(value, 0)
    sized = value if omit_key is None else {k: v for k, v in value.items() if k != omit_key}
    if len(json.dumps(sized, separators=(",", ":"), ensure_ascii=True).encode()) > limits.wire_bytes:
        _refuse("REPORT_TOO_LARGE", "input byte limit")


def _json_value(value):
    """Copy bounded JSON facts; reject nonfinite facts even outside geometry fields."""
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if type(value) is float and not math.isfinite(value):
        _refuse("INVALID_REPORT", "nonfinite fact")
    return value


def _match_client(value, client: ClientRef, *, closed=False):
    value = _mapping(value, "CLIENT_MISMATCH")
    if closed and set(value) != set(client.wire()):
        _refuse("CLIENT_MISMATCH", "unknown or incomplete capture client identity")
    if any(value.get(key) != expected for key, expected in client.wire().items()):
        _refuse("CLIENT_MISMATCH", "workspace, client, page or connection differs")


SOURCE_KINDS = frozenset(("live", "saved-image", "video", "lesson", "written", "composite", "web-page"))
# Recovered P5 values are authoritative provenance too. Preserve their spelling;
# "capture" does not by itself establish clean/raw pixels or permit relabeling.
MEDIA_KINDS = frozenset(("raw", "derived", "annotated", "external-reference", "capture", "labelled", "reference",
                         "generated", "lesson", "written", "unknown"))
SOURCE_AVAILABILITY = frozenset(("retained", "temporary", "missing", "earlier-run", "unknown"))


def _source(value) -> dict:
    value = _mapping(value, "INVALID_SOURCE")
    for key, choices in (("kind", SOURCE_KINDS), ("media", MEDIA_KINDS), ("availability", SOURCE_AVAILABILITY)):
        if not isinstance(value.get(key), str) or value[key] not in choices:
            _refuse("INVALID_SOURCE", f"invalid source {key}")
    result = {"key": _id(value.get("key"), "INVALID_SOURCE"),
              **{key: value[key] for key in ("kind", "media", "availability")}}
    for key in ("identity", "provenance"):
        result[key] = _json_value(_mapping(value.get(key), "INVALID_SOURCE"))
    resource = result["identity"].get("resource")
    if resource is not None:
        resource = _mapping(resource, "INVALID_SOURCE")
        _id(resource.get("day"), "INVALID_SOURCE")
        _id(resource.get("id"), "INVALID_SOURCE")
        for key in ("version", "picture"):
            if key not in resource:
                _refuse("INVALID_SOURCE", "resource version/member unknown must be explicit null")
            if resource.get(key) is not None:
                _integer(resource[key], "INVALID_SOURCE")
    if "components" in value:
        components = _array(value["components"])
        if len(components) > 8:
            _refuse("INVALID_SOURCE", "source component bound exceeded")
        result["components"] = [_source(component) for component in components]
    if "locator" in value:
        locator = value["locator"]
        if locator is not None and (not isinstance(locator, str) or len(locator) > 2048):
            _refuse("INVALID_SOURCE", "source locator must be bounded transport metadata")
        result["locator"] = locator
    if "digest" in value:
        result["digest"] = None if value["digest"] is None else _id(value["digest"], "INVALID_SOURCE")
    return result


def normalize_source(value) -> dict:
    """Project the closed source wire contract; callers supply source authority."""
    return _source(value)


def _same_fact(before, after) -> bool:
    """Typed structural identity, including embedded canonical producing refs."""
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        source_fields = {"key", "kind", "media", "identity", "provenance", "availability"}
        if source_fields <= before.keys() and source_fields <= after.keys():
            return _same_source(before, after)
        return before.keys() == after.keys() and all(_same_fact(v, after[k]) for k, v in before.items())
    if isinstance(before, (tuple, list)) and isinstance(after, (tuple, list)):
        return len(before) == len(after) and all(_same_fact(a, b) for a, b in zip(before, after))
    return type(before) is type(after) and before == after


def _same_source(before: Mapping, after: Mapping | None) -> bool:
    """Availability/locator are dated facts, never immutable producer identity."""
    if after is None or not all(_same_fact(before[k], after.get(k))
                                for k in ("key", "kind", "media", "identity", "provenance")):
        return False
    if before.get("digest") is not None and not _same_fact(before["digest"], after.get("digest")):
        return False
    a, b = before.get("components", ()), after.get("components", ())
    return len(a) == len(b) and all(_same_source(left, right) for left, right in zip(a, b))


def _scalar(value):
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        return _number(value)
    if isinstance(value, str) and len(value) <= 256:
        return value
    _refuse("INVALID_REPORT", "observed control/style value must be a bounded scalar")


def _reasons(value) -> list:
    values = _array(value)
    if len(values) > 8:
        _refuse("REPORT_TOO_LARGE", "observation reason bound exceeded")
    return [_id(v) for v in values]


def _control(value) -> dict:
    value = _mapping(value)
    result = {"id": _id(value.get("id")), "targetId": _id(value.get("targetId")),
              "role": _id(value.get("role")), "enabled": _boolean(value.get("enabled"))}
    editable = _boolean(value.get("editable", result["role"] in ("textbox", "searchbox")))
    if "editable" in value:
        result["editable"] = editable
    if "present" in value:
        result["present"] = None if value["present"] is None else _boolean(value["present"])
    if "stateRevision" in value:
        result["stateRevision"] = None if value["stateRevision"] is None else _integer(value["stateRevision"])
    if "observedValue" in value and not editable:
        result["observedValue"] = _scalar(value["observedValue"])
    if "unknown" in value:
        result["unknown"] = _reasons(value["unknown"])
    # Never retain editable observedValue or arbitrary DOM value/text fields.
    return result


def _style(value) -> dict:
    value = _mapping(value)
    result = {}
    for key in ("opacity", "fillOpacity", "lineWidth", "colour", "color", "stroke", "fill", "emphasis"):
        if key not in value:
            continue
        if key in ("opacity", "fillOpacity", "lineWidth") and value[key] is not None:
            result[key] = _number(value[key], nonnegative=True)
            if key != "lineWidth" and result[key] > 1:
                _refuse("INVALID_GEOMETRY", "drawn opacity must be between zero and one")
        else:
            result[key] = _scalar(value[key])
    if "unknown" in value:
        result["unknown"] = _reasons(value["unknown"])
    return result


def _payload(value) -> dict:
    value = _mapping(value)
    result = {}
    for key in ("decodedToken", "servedDigest", "originalDigest", "token", "servedPayloadId", "payloadId", "contentType"):
        if key in value:
            result[key] = None if value[key] is None else _id(value[key])
    for key in ("transform", "transcode"):
        if key in value:
            result[key] = _json_value(value[key])
    if "unknown" in value:
        result["unknown"] = _reasons(value["unknown"])
    return result


def _visibility(value):
    states = {"visible", "closed", "tab-hidden", "css-hidden", "off-viewport", "clipped", "occluded", "unknown"}
    if isinstance(value, str):
        if value not in states:
            _refuse("INVALID_REPORT", "unknown visibility state")
        return value
    value = _mapping(value)
    if not isinstance(value.get("state"), str) or value["state"] not in states:
        _refuse("INVALID_REPORT", "unknown visibility state")
    result = {"state": value["state"]}
    if "scope" in value:
        result["scope"] = _id(value["scope"])
    for key in ("reasons", "unknown"):
        if key in value:
            result[key] = _reasons(value[key])
    return result


def _transform(value) -> dict:
    value = _mapping(value)
    result = {"sourceToWorkspace": _vector(value.get("sourceToWorkspace"), 6),
              "originalSize": _vector(value.get("originalSize"), 2, dimensions=True)}
    if "transformRevision" in value:
        result["transformRevision"] = _integer(value["transformRevision"])
    if "sourceToContent" in value or "contentOrigin" in value:
        result["sourceToContent"] = _vector(value.get("sourceToContent"), 6)
        result["contentOrigin"] = _vector(value.get("contentOrigin"), 2)
        expected = result["sourceToContent"][:]
        expected[4] += result["contentOrigin"][0]
        expected[5] += result["contentOrigin"][1]
        if any(abs(a - b) > 1e-6 for a, b in zip(expected, result["sourceToWorkspace"])):
            _refuse("TRANSFORM_MISMATCH", "content and workspace affine maps disagree")
    return result


def _live_sample(value) -> dict:
    """Dated renderer draw facts; never reconstructed from a desired live anchor."""
    value = _mapping(value)
    required = {"run", "boot", "seq"}
    if not required <= value.keys() or set(value) - (required | {"frame", "epoch"}):
        _refuse("INVALID_REPORT", "live sample needs the closed run/boot/seq/frame/epoch fields")
    result = {"run": _id(value["run"]),
              "boot": None if value["boot"] is None else _id(value["boot"]),
              "seq": None if value["seq"] is None else _integer(value["seq"])}
    for key in ("frame", "epoch"):
        if key in value:
            result[key] = None if value[key] is None else _integer(value[key])
    return result


def _legacy_fields(value, required=(), optional=()):
    value = _mapping(value)
    if not set(required) <= value.keys() or set(value) - (set(required) | set(optional)):
        _refuse("INVALID_REPORT", "unrecognized structural observation fields")
    return value


def _legacy_text(value, bound, *, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > bound:
        _refuse("INVALID_REPORT", "structural observation text exceeds its bound")
    return value


def _legacy_style(value):
    value = _legacy_fields(value, optional=("opacity", "fillOpacity", "lineWidth", "colour", "color",
                                             "stroke", "fill", "emphasis"))
    result = {}
    for key, fact in value.items():
        if key in ("opacity", "fillOpacity", "lineWidth"):
            result[key] = _number(fact, nonnegative=True)
            if key != "lineWidth" and fact > 1:
                _refuse("INVALID_REPORT", "structural opacity exceeds one")
        elif key == "fill":
            result[key] = _boolean(fact)
        elif key == "emphasis":
            emphasis = _legacy_fields(fact, ("members", "rest"))
            members = [_id(member) for member in _array(emphasis["members"])]
            if len(members) > 40:
                _refuse("REPORT_TOO_LARGE", "structural emphasis exceeds its bound")
            if len(set(members)) != len(members) or emphasis["rest"] not in ("hide", "fade"):
                _refuse("INVALID_REPORT", "invalid structural emphasis")
            result[key] = {"members": members, "rest": emphasis["rest"]}
        else:
            result[key] = _legacy_text(fact, 256)
    return result


SPECIMEN_STATES = ("fill", "float", "hidden")  # how a Tab holds the specimen (the live view): workspace-views-model.ts
SPECIMEN_ASKED = {"fill": "fill", "float": "float", "hide": "hidden"}  # tabs specimen, as the Tab strip then reports it


# What a thing on a Tab is (apps/studio/src/workspace-model.ts ITEM_KINDS), and a Tab's own kind.
ITEM_KINDS = ("table", "outlines", "tracks", "image", "reference", "document", "graph", "video", "web-page",
              "magnifier", "note")
TAB_KINDS = ("work", "output", "compare", "reader")


def _holds(value) -> dict:
    """A Tab's items counted by kind (workspace-model.ts `holds`). Lenient, the one bad part left out (the finalisation
    plan's rule 4: one bad part never takes down the whole): a kind it does not know or a count out of bounds is
    dropped, never the report."""
    if not isinstance(value, dict):
        return {}
    return {kind: n for kind, n in value.items()
            if kind in ITEM_KINDS and type(n) is int and 0 < n <= 999}


def _specimen(value) -> dict:
    """A Tab's specimen as its Tab strip reports it (main.ts, workspace-views.ts specimenReport): filling the Tab behind
    its windows, floating as a window (its place in workspace px, when laid out), or hidden from the Tab."""
    value = _legacy_fields(value, ("state",), ("rect",))
    if value["state"] not in SPECIMEN_STATES or ("rect" in value and value["state"] != "float"):
        _refuse("INVALID_REPORT", "invalid structural specimen")
    return {"state": value["state"], **({"rect": _rect(value["rect"])} if "rect" in value else {})}


def _legacy(value):
    """Closed DOM observations, never native sources, ownership or paint receipts."""
    value = _legacy_fields(value, ("schema", "scope"), ("title", "shows", "source", "beside", "level",
        "pictures", "step", "placement", "covers", "opacity", "style", "work", "workReady", "document", "workspace",
        "drawn", "members"))
    if value["schema"] != "studio-surface-v1" or value["scope"] != "dom-observation":
        _refuse("INVALID_REPORT", "unknown structural observation scope")
    result = {"schema": value["schema"], "scope": value["scope"]}
    for key, fact in value.items():
        if key in ("schema", "scope"):
            continue
        if key in ("title", "shows", "source", "beside", "step", "placement"):
            bound = 120 if key == "title" else 2048 if key in ("source", "beside") else 256
            result[key] = _legacy_text(fact, bound, nullable=key in ("shows", "source", "beside", "step"))
        elif key == "level":
            result[key] = None if fact is None else _number(fact)
            if fact is not None and result[key] <= 0:
                _refuse("INVALID_REPORT", "structural magnification must be positive")
        elif key in ("covers", "workReady"):
            result[key] = _boolean(fact)
        elif key == "opacity":
            result[key] = _number(fact, nonnegative=True)
            if fact > 1:
                _refuse("INVALID_REPORT", "structural opacity exceeds one")
        elif key == "pictures":
            pictures = _array(fact)
            if len(pictures) > 2:
                _refuse("REPORT_TOO_LARGE", "singleton structural picture bound exceeded")
            result[key] = []
            for picture in pictures:
                picture = _legacy_fields(picture, ("source", "scale"))
                scale = _number(picture["scale"])
                if scale <= 0:
                    _refuse("INVALID_REPORT", "structural picture scale must be positive")
                result[key].append({"source": _legacy_text(picture["source"], 2048), "scale": scale})
        elif key == "style":
            result[key] = _legacy_style(fact)
        elif key == "workspace":
            context = _legacy_fields(fact, ("project", "tab"), ("tabTitle", "tabs"))
            result[key] = {k:_id(context[k]) for k in ("project", "tab")}
            if "tabTitle" in context:  # the Tab in front's title, as its Tab strip shows it (main.ts)
                result[key]["tabTitle"] = _legacy_text(context["tabTitle"], 80)
            if "tabs" in context:  # the Project's open Tabs in strip order, one in front (Hands: focus, "the main tab")
                tabs = _array(context["tabs"])
                if len(tabs) > 24:
                    _refuse("REPORT_TOO_LARGE", "structural Tab list exceeds its bound")
                result[key]["tabs"] = [{"id": _id(t["id"]), "title": _legacy_text(t["title"], 80),
                                        "front": _boolean(t["front"]),
                                        **({"specimen": _specimen(t["specimen"])} if "specimen" in t else {}),
                                        **({"kind": t["kind"]} if t.get("kind") in TAB_KINDS else {}),
                                        **({"holds": _holds(t["holds"])} if "holds" in t else {})}
                                       for t in (_legacy_fields(t, ("id", "title", "front"), ("specimen", "kind", "holds"))
                                                 for t in tabs)]
                if tabs and sum(t["front"] for t in result[key]["tabs"]) != 1:
                    _refuse("INVALID_REPORT", "exactly one Tab is in front")
        elif key == "drawn":  # the outlines drawn on the live view now (Hands: highlight and clear verified)
            drawn = _array(fact)
            if len(drawn) > 60:
                _refuse("REPORT_TOO_LARGE", "structural drawn outlines exceed their bound")
            result[key] = [{"id": _id(d["id"]), "kind": _id(d["kind"])}
                           for d in (_legacy_fields(d, ("id", "kind")) for d in drawn)]
        elif key == "members":  # a population layer's members and how many are drawn (Hands: outline verified)
            members = _legacy_fields(fact, ("count", "drawn"))
            count, drawn_n = _integer(members["count"]), _integer(members["drawn"])
            if count < 0 or drawn_n < 0 or drawn_n > count:
                _refuse("INVALID_REPORT", "invalid structural member counts")
            result[key] = {"count": count, "drawn": drawn_n}
        elif key == "document":
            document = _legacy_fields(fact, ("project", "tab", "placement", "resource", "representation", "state", "focused"))
            resource = _legacy_fields(document["resource"], ("day", "id", "version", "picture"), ("outputId",))
            result[key] = {k: _id(document[k]) for k in ("project", "tab", "placement", "representation", "state")}
            result[key]["focused"] = _boolean(document["focused"])
            result[key]["resource"] = {"day": _id(resource["day"]), "id": _id(resource["id"]),
                **{k: None if resource[k] is None else _integer(resource[k]) for k in ("version", "picture")},
                **({"outputId": _id(resource["outputId"])} if "outputId" in resource else {})}
        elif key == "work":
            work = _legacy_fields(fact, ("researcherId", "resource"))
            resource = _legacy_fields(work["resource"], ("day", "id", "version"))
            result[key] = {"researcherId": _id(work["researcherId"]), "resource": {
                "day": _id(resource["day"]), "id": _id(resource["id"]),
                "version": None if resource["version"] is None else _integer(resource["version"])}}
    return result


def _legacy_item(item):
    fixed = {"workspace-tabs": "panel", "live": "live", "outlines": "outlines", "magnifier": "magnifier", "viewer": "viewer",
             "slidemap": "slidemap", "card": "card", "made-today": "made-today", "sidebar": "sidebar", "sessions": "sessions"}
    kind = fixed.get(item["id"])
    for prefix, candidate in (("layer:", "population"), ("tracks:", "tracks"), ("document:", "document")):
        if item["id"].startswith(prefix):
            _id(item["id"][len(prefix):])
            kind = candidate
    if kind is None or item["kind"] != kind:
        _refuse("INVALID_REPORT", "unregistered structural studio surface")
    if (item.get("sourceKey") is not None or item.get("sourceToken") is not None
            or item["propertyVersions"] or item["propertyOwners"] or item.get("payloadBinding") is not None
            or item.get("liveSample") is not None or item.get("heldSample") is not None or item.get("viewDrawRef") is not None
            or item.get("viewTransform") is not None or item.get("staged") is not None):
        _refuse("INVALID_REPORT", "structural observations cannot supply native authority")
    load = item.get("load")
    if load is None or set(load) - {"state", "unknown"} or load["state"] not in ("unknown", "empty"):
        _refuse("INVALID_REPORT", "structural native load authority must remain unknown or empty")


def _scientific_paint(value, item):
    document = item.get('view', {}).get('legacy', {}).get('document', {})
    resource = document.get('resource', {})
    if (item['kind'] != 'document' or document.get('representation') != 'scientific-review'
            or document.get('project') != resource.get('day')
            or type(resource.get('version')) is not int or resource['version'] < 1
            or resource.get('picture') is not None or 'outputId' in resource):
        _refuse('INVALID_REPORT', 'scientific preview needs its exact saved document')
    if value is None:
        return None
    value = _legacy_fields(value, ('review', 'frame', 'draw', 'width', 'height', 'contentRect'))
    if not all(item[k] for k in ('open', 'connected', 'visible')):
        _refuse('INVALID_REPORT', 'a hidden scientific window cannot report current paint')
    if type(value['frame']) is not str or re.fullmatch(r'[0-9a-f]{64}', value['frame']) is None:
        _refuse('INVALID_REPORT', 'scientific paint needs its worker frame identity')
    size = [_integer(value[k]) for k in ('width', 'height')]
    if any(not 1 <= n <= 1024 for n in size):
        _refuse('INVALID_REPORT', 'scientific paint dimensions exceed the viewport allowance')
    rect = _rect(value['contentRect'])
    if rect[2] <= 0 or rect[3] <= 0 or any(abs(n) > 1e7 for n in rect):
        _refuse('INVALID_REPORT', 'scientific paint needs a bounded visible canvas rectangle')
    return {k: _id(value[k]) for k in ('review', 'frame', 'draw')} | {
        'width': size[0], 'height': size[1], 'contentRect': rect}


def _legacy_counters(item, before):
    if not before:
        return
    if not (before.get("view") or {}).get("legacy"):
        _refuse("INVALID_REPORT", "native instance cannot become a structural surface")
    if item["lifetimeGeneration"] != before["lifetimeGeneration"]:
        return  # A new observed root lifetime cannot inherit an original Change association.
    if item["rootId"] != before["rootId"] or item["kind"] != before["kind"]:
        _refuse("INVALID_REPORT", "structural root changed without a new lifetime")
    if item["sourceGeneration"] < before["sourceGeneration"] or item["revision"] < before["revision"]:
        _refuse("INVALID_REPORT", "structural observation counters regressed")
    def content(row):
        legacy = row["view"]["legacy"]
        return {**{key: legacy[key] for key in ("shows", "source", "beside", "step", "work") if key in legacy},
                "pictures": [picture["source"] for picture in legacy.get("pictures", [])]}
    if not _same_fact(content(item), content(before)) and item["sourceGeneration"] <= before["sourceGeneration"]:
        _refuse("INVALID_REPORT", "structural content changed without advancing its generation")
    facts = {key: value for key, value in item.items() if key != "revision"}
    prior = {key: value for key, value in before.items() if key != "revision"}
    if not _same_fact(facts, prior) and item["revision"] <= before["revision"]:
        changed = sorted(key for key in facts.keys() | prior.keys()
                         if key not in facts or key not in prior or not _same_fact(facts[key], prior[key]))
        _refuse("INVALID_REPORT", "structural facts changed without advancing their revision: " + ", ".join(changed))


def _item(value, client: ClientRef) -> dict:
    value = _mapping(value)
    if value.get("workspaceId") != client.workspace_id:
        _refuse("CLIENT_MISMATCH", "presentation belongs to another workspace")
    result = {key: _id(value.get(key)) for key in ("id", "rootId", "workspaceId", "kind")}
    if "title" in value and isinstance(value["title"], str):
        result["title"] = value["title"][:120]
    result["lifetimeGeneration"] = _lifetime(value.get("lifetimeGeneration"))
    for key in ("sourceGeneration", "revision"):
        result[key] = _integer(value.get(key))
    for key in ("sourceKey", "sourceToken"):
        if value.get(key) is not None:
            result[key] = _id(value[key])
    for key in ("open", "connected", "visible"):
        result[key] = _boolean(value.get(key))
    if "staged" in value:
        result["staged"] = None if value["staged"] is None else _boolean(value["staged"])
    if "liveSample" in value:
        result["liveSample"] = None if value["liveSample"] is None else _live_sample(value["liveSample"])
    if "heldSample" in value:
        result["heldSample"] = None if value["heldSample"] is None else _live_sample(value["heldSample"])
    if "viewDrawRef" in value:
        result["viewDrawRef"] = None if value["viewDrawRef"] is None else _id(value["viewDrawRef"])
    for key in ("rect", "contentRect", "clipRect"):
        if value.get(key) is not None:
            result[key] = _rect(value[key])
    if "z" in value:
        result["z"] = _number(value["z"])
    if "space" in value:
        result["space"] = _id(value["space"])
    if "mode" in value:
        result["mode"] = _id(value["mode"])
    if "view" in value:
        result["view"] = _json_value(_mapping(value["view"]))
        if "arrangement" in result["view"]:
            arrangement = _legacy_fields(result["view"]["arrangement"], ("project", "tab", "placement", "focused"))
            result["view"]["arrangement"] = {**{key: _id(arrangement[key]) for key in ("project", "tab", "placement")}, "focused": _boolean(arrangement["focused"])}
        if "legacy" in result["view"]:
            _legacy_fields(result["view"], ("legacy",))
            result["view"] = {"legacy": _legacy(result["view"]["legacy"])}
    if value.get("viewTransform") is not None:
        result["viewTransform"] = _transform(value["viewTransform"])
    result["propertyVersions"] = { _id(k): _integer(v) for k, v in _mapping(value.get("propertyVersions", {})).items() }
    result["propertyOwners"] = { _id(k): None if v is None else _id(v) for k, v in _mapping(value.get("propertyOwners", {})).items() }
    controls = _array(value.get("controls", []))
    if len(controls) > 40:
        _refuse("REPORT_TOO_LARGE", "registered control bound exceeded")
    result["controls"] = [_control(control) for control in controls]
    for key, normalizer in (("style", _style), ("payloadBinding", _payload), ("visibility", _visibility), ("unknown", _reasons)):
        if key in value:
            result[key] = None if value[key] is None else normalizer(value[key])
    if value.get("load") is not None:
        load = _mapping(value["load"])
        state = load.get("state")
        if state not in ("empty", "loading", "loaded", "decoded", "drawn", "error", "unknown"):
            _refuse("INVALID_REPORT", "unknown load state")
        result["load"] = {"state": state}
        for key in ("sourceKey", "drawToken", "token", "errorCode"):
            if key in load:
                result["load"][key] = None if load[key] is None else _id(load[key])
        if "sourceGeneration" in load:
            result["load"]["sourceGeneration"] = _integer(load["sourceGeneration"])
        if "naturalSize" in load:
            size = None if load["naturalSize"] is None else _vector(load["naturalSize"], 2)
            if size is not None and any(v < 0 or v != int(v) for v in size):
                _refuse("INVALID_GEOMETRY", "decoded dimensions must be nonnegative pixels")
            result["load"]["naturalSize"] = size
        if "unknown" in load:
            result["load"]["unknown"] = _reasons(load["unknown"])
    if "legacy" in result.get("view", {}):
        _legacy_item(result)
    if 'scientificPaint' in value:
        result['scientificPaint'] = _scientific_paint(value['scientificPaint'], result)
    if 'scientificPanelPaint' in value:
        result['scientificPanelPaint'] = _scientific_paint(value['scientificPanelPaint'], result)
    return result


def _check_live_sample(item: Mapping, sources: Mapping, app_run_id: str) -> None:
    if item.get("liveSample") is not None and item.get("heldSample") is not None:
        _refuse("INVALID_REPORT", "a stopped image cannot also claim a current live sample")
    sample = item.get("liveSample")
    if sample is None:
        sample = item.get("heldSample")
    if sample is None:
        return  # Missing/null remains unknown, even when the desired source is live.
    source_key = item.get("sourceKey")
    source = sources.get(source_key)
    if source is None:
        _refuse("SOURCE_UNRESOLVED", "live sample has no registered presentation source")
    if source["kind"] != "live":
        _refuse("INVALID_SOURCE", "only a live source may carry a current live draw sample")
    if sample["run"] != source["identity"].get("run"):
        _refuse("SOURCE_CHANGED", "drawn live sample belongs to another producing source run")
    if sample["run"] != app_run_id:
        _refuse("RUN_MISMATCH", "drawn live sample belongs to another app run")
    load = item.get("load", {})
    if load.get("state") != "drawn":
        _refuse("INVALID_REPORT", "live sample describes actual drawing, not a scheduled or decoded view")
    if load.get("sourceKey") != source_key or load.get("sourceGeneration") != item["sourceGeneration"]:
        _refuse("SOURCE_CHANGED", "live draw sample lacks the current presentation/load binding")


def _unique(values, key):
    result = {}
    for value in values:
        identity = value[key]
        if identity in result:
            _refuse("DUPLICATE_ID", f"duplicate {key}: {identity}")
        result[identity] = value
    return result


def _target(value) -> dict:
    value = _mapping(value)
    result = {"id": _id(value.get("id")), "type": _id(value.get("type"))}
    for key in ("presentationId", "sourceKey", "controlId", "outlineId", "populationId", "memberId", "basis"):
        if value.get(key) is not None:
            result[key] = _id(value[key])
    for key in ("revision", "memberRevision"):
        if key in value:
            result[key] = _integer(value[key])
    if not any(key in result for key in ("presentationId", "sourceKey", "outlineId", "populationId")):
        _refuse("TARGET_UNRESOLVED", "target has no explicit registered referent")
    return result


def _focus(value) -> dict:
    value = _mapping(value)
    result = {"presentationId": None, "controlId": None, "role": _id(value.get("role", "none")),
              "editable": _boolean(value.get("editable", False))}
    for key in ("presentationId", "controlId"):
        if value.get(key) is not None:
            result[key] = _id(value[key])
    if "ageMs" in value:
        result["ageMs"] = _number(value["ageMs"], nonnegative=True)
    return result


def _pointer(value) -> dict:
    value = _mapping(value)
    result = {key: _id(value.get(key, "none")) for key in ("modality", "state")}
    result["css"] = None if value.get("css") is None else _vector(value["css"], 2)
    for key in ("hitPath", "targetCandidates"):
        result[key] = [_id(v) for v in _array(value.get(key, []))]
    for key in ("pointerId", "buttons"):
        if key in value and value[key] is not None:
            result[key] = _integer(value[key])
    for key in ("capturedBy", "presentationId", "sourceKey"):
        if value.get(key) is not None:
            result[key] = _id(value[key])
    if "sourceGeneration" in value:
        result["sourceGeneration"] = _integer(value["sourceGeneration"])
    if "sourcePoint" in value:
        result["sourcePoint"] = None if value["sourcePoint"] is None else _vector(value["sourcePoint"], 2)
    for key in ("eventMonoMs", "sampledMonoMs", "eventAgeMs", "ageMs"):
        if key in value:
            result[key] = None if value[key] is None else _number(value[key], nonnegative=True)
    if result.get("eventMonoMs") is not None and result.get("sampledMonoMs") is not None:
        if result["eventMonoMs"] > result["sampledMonoMs"]:
            _refuse("INVALID_REPORT", "pointer event is after sample")
        result["eventAgeMs"] = result["sampledMonoMs"] - result["eventMonoMs"]
    if "historical" in value:
        result["historical"] = _boolean(value["historical"])
    return result


def _interaction(value) -> dict:
    value = _mapping(value)
    result = {"pointer": _pointer(value.get("pointer", {})),
              "focus": _focus(value.get("focus", {})),
              "contacts": [_pointer(v) for v in _array(value.get("contacts", []))],
              "selected": [_id(v) for v in _array(value.get("selected", []))],
              "availability": _id(value.get("availability", "unavailable")),
              "unknown": [_id(v) for v in _array(value.get("unknown", []))],
              "omittedContacts": _integer(value.get("omittedContacts", 0))}
    for key, normalizer in (("lastContentPointer", _pointer), ("lastContentFocus", _focus)):
        result[key] = None if value.get(key) is None else normalizer(value[key])
    return result


def _viewport(value) -> dict:
    value = _mapping(value)
    result = {"layoutSize": _vector(value.get("layoutSize"), 2, dimensions=True),
              "visualRect": _rect(value.get("visualRect")),
              "visualScale": _number(value.get("visualScale")),
              "documentScroll": _vector(value.get("documentScroll"), 2),
              "dpr": _number(value.get("dpr")),
              "visible": _boolean(value.get("visible")),
              "documentFocused": _boolean(value.get("documentFocused"))}
    if result["dpr"] <= 0 or result["visualScale"] <= 0:
        _refuse("INVALID_GEOMETRY", "viewport scale must be positive")
    if value.get("activeSurface") is not None:
        result["activeSurface"] = _id(value["activeSurface"])
    if "presentationArea" in value:
        area = value["presentationArea"]
        if area is not None:
            area = _rect(area)
            x, y, width, height = area
            vx, vy, vw, vh = result["visualRect"]
            if (width <= 0 or height <= 0 or x < vx - 1e-6 or y < vy - 1e-6
                    or x + width > vx + vw + 1e-6 or y + height > vy + vh + 1e-6):
                _refuse("INVALID_GEOMETRY", "presentation area must be a positive visible viewport intersection")
        # Absent is unknown; null is an observed empty host/viewport intersection.
        result["presentationArea"] = area
    return result


def normalize_observation(message: dict, *, client: ClientRef, app_run_id: str,
                          received_at: float, previous: Observation | None = None,
                          limits: Limits | None = None) -> Observation:
    limits = limits or Limits()
    _preflight(message, limits)
    message = _mapping(message)
    _match_client(message.get("client"), client)
    if message.get("appRunId") != _id(app_run_id):
        _refuse("RUN_MISMATCH", "observation is from another app run")
    if message.get("schema") != "screen-observation-v2":
        _refuse("INVALID_REPORT", "unknown observation schema")
    received_at = _number(received_at, nonnegative=True)
    seq = _integer(message.get("seq"))
    if previous is not None:
        if previous.client != client:
            _refuse("CLIENT_MISMATCH", "previous report is not this connection's baseline")
        if previous.app_run_id != app_run_id:
            _refuse("RUN_MISMATCH", "previous report belongs to another run")
        if seq <= previous._data["seq"] or received_at < previous.received_at:
            _refuse("REPORT_REPLAYED", "sequence or receive time moved backwards")
    result = dict(schema="screen-observation-v2", client=client.wire(), appRunId=app_run_id,
                  seq=seq, layoutRevision=_integer(message.get("layoutRevision")),
                  capturedMonoMs=_number(message.get("capturedMonoMs"), nonnegative=True),
                  viewport=_viewport(message.get("viewport")))
    all_items = [_item(v, client) for v in _array(message.get("items", []))]
    all_sources = [_source(v) for v in _array(message.get("sources", []))]
    all_targets = [_target(v) for v in _array(message.get("targets", []))]
    items = _unique(all_items, "id")
    prior_items = {item["id"]: item for item in previous.wire()["items"]} if previous is not None else {}
    for item in all_items:
        if "legacy" in item.get("view", {}):
            _legacy_counters(item, prior_items.get(item["id"]))
        elif "legacy" in (prior_items.get(item["id"], {}).get("view") or {}):
            _refuse("INVALID_REPORT", "structural surface cannot acquire native instance authority")
    sources = _unique(all_sources, "key")
    targets = _unique(all_targets, "id")
    controls = _unique([control for item in all_items for control in item["controls"]], "id")
    control_owners = {control["id"]: item["id"] for item in all_items for control in item["controls"]}
    for item in all_items:
        if item.get("sourceKey") is not None and item["sourceKey"] not in sources:
            _refuse("SOURCE_UNRESOLVED", "presentation source is absent")
        load = item.get("load", {})
        if ((load.get("sourceKey") is not None and load["sourceKey"] != item.get("sourceKey")) or
                (load.get("sourceGeneration") is not None and load["sourceGeneration"] != item["sourceGeneration"])):
            _refuse("SOURCE_CHANGED", "draw/load binding differs from presentation")
        _check_live_sample(item, sources, app_run_id)
        for control in item["controls"]:
            if control["targetId"] not in items:
                _refuse("TARGET_UNRESOLVED", "control target is absent")
    for target in all_targets:
        if "presentationId" in target and target["presentationId"] not in items:
            _refuse("TARGET_UNRESOLVED", "target presentation is absent")
        if "sourceKey" in target and target["sourceKey"] not in sources:
            _refuse("SOURCE_UNRESOLVED", "target source is absent")
        if "presentationId" in target and "sourceKey" in target:
            if items[target["presentationId"]].get("sourceKey") != target["sourceKey"]:
                _refuse("SOURCE_CHANGED", "target source differs from its presentation")
        if "controlId" in target and target["controlId"] not in controls:
            _refuse("TARGET_UNRESOLVED", "target control is absent")
    edges = []
    known = set(items) | set(sources) | set(targets) | set(controls)
    for edge in _array(message.get("edges", [])):
        edge = _mapping(edge)
        normalized = {k: _id(edge.get(k)) for k in ("type", "from", "to")}
        if normalized["from"] not in known or normalized["to"] not in known:
            _refuse("TARGET_UNRESOLVED", "edge endpoint is absent")
        edges.append(normalized)
    interaction = _interaction(message.get("interaction", {}))
    references = interaction["selected"] + interaction["pointer"]["targetCandidates"]
    for contact in interaction["contacts"]:
        references.extend(contact["targetCandidates"])
    if any(ref not in targets for ref in references):
        _refuse("TARGET_UNRESOLVED", "interaction references an unregistered target")
    focus = interaction["focus"]
    if focus["presentationId"] is not None:
        pid, cid = focus["presentationId"], focus["controlId"]
        if pid not in items or (cid is not None and cid != pid and control_owners.get(cid) != pid):
            _refuse("TARGET_UNRESOLVED", "content focus is not a registered presentation/control")
    # Null-presentation editor/Send focus is an adapter-supplied global fact, not
    # an actionable window target. Its value/text is excluded by _focus.
    for point in [interaction["pointer"], *interaction["contacts"]]:
        if point is not None:
            if any(ref not in known for ref in point["hitPath"]):
                _refuse("TARGET_UNRESOLVED", "pointer path references an unregistered hit")
            if point.get("presentationId") is not None and point["presentationId"] not in items:
                _refuse("TARGET_UNRESOLVED", "current pointer presentation is absent")
            if point.get("sourceKey") is not None and point["sourceKey"] not in sources:
                _refuse("SOURCE_UNRESOLVED", "pointer source is absent")
            if point.get("capturedBy") is not None and point["capturedBy"] not in known:
                _refuse("TARGET_UNRESOLVED", "pointer capture recipient is unregistered")
    # History outlives a root. Preserve its dated IDs as unavailable facts, never as
    # a new registered actionable target or a reason to reject the entire current report.
    for key in ("lastContentPointer", "lastContentFocus"):
        historic = interaction[key]
        if historic is not None:
            historic["historical"] = True
            represented = historic.get("presentationId") in items
            if key == "lastContentFocus" and historic.get("controlId") is not None:
                cid = historic["controlId"]
                represented = represented and (cid == historic["presentationId"] or
                                               control_owners.get(cid) == historic["presentationId"])
            if historic.get("sourceKey") is not None:
                represented = (represented and historic["sourceKey"] in sources and
                               items[historic["presentationId"]].get("sourceKey") == historic["sourceKey"])
                if represented and "sourceGeneration" in historic:
                    represented = items[historic["presentationId"]]["sourceGeneration"] == historic["sourceGeneration"]
            historic["available"] = represented
            if not represented:
                if key == "lastContentPointer":
                    historic["hitPath"] = []
                    historic["targetCandidates"] = []
                interaction["unknown"].append(f"{key}-not-represented")
    # Required explicit selections/candidates cannot silently disappear under truncation.
    required_targets = set(references)
    current_points = [interaction["pointer"], *interaction["contacts"]]
    for point in current_points:
        required_targets.update(ref for ref in point["hitPath"] if ref in targets)
        if point.get("capturedBy") in targets:
            required_targets.add(point["capturedBy"])
    if len(required_targets) > limits.targets or len(interaction["selected"]) > limits.candidates:
        _refuse("TARGET_NOT_REPRESENTABLE", "required target bound exceeded")
    kept_targets = [t for t in all_targets if t["id"] in required_targets]
    required_items, direct_sources = set(), set()

    def require(ref):
        pending = [ref]
        while pending:
            ref = pending.pop()
            if ref in items:
                if ref in required_items:
                    continue
                required_items.add(ref)
                # Kept controls must not refer to a presentation omitted by the cut.
                pending.extend(control["targetId"] for control in items[ref]["controls"])
            elif ref in controls:
                pending.extend((control_owners[ref], controls[ref]["targetId"]))
            elif ref in targets:
                target = targets[ref]
                pending.extend((target.get("presentationId"), target.get("controlId")))
                if target.get("sourceKey") is not None:
                    direct_sources.add(target["sourceKey"])
            elif ref in sources:
                direct_sources.add(ref)

    def require_target(target):
        require(target.get("presentationId"))
        require(target.get("controlId"))
        if target.get("sourceKey") is not None:
            direct_sources.add(target["sourceKey"])

    omitted = _mapping(message.get("omitted", {}))
    sender_totals = _mapping(message.get("totals", {}))
    totals = {}
    collections = {"items": all_items, "sources": all_sources, "targets": all_targets, "edges": edges}
    for key, original in collections.items():
        totals[key] = len(original) + _integer(omitted.get(key, 0))
        if key in sender_totals and _integer(sender_totals[key]) != totals[key]:
            _refuse("INVALID_REPORT", "total contradicts represented plus omitted")
    declared_complete = _boolean(message.get("complete", False))
    if "foreground" in message:
        scope = _mapping(message["foreground"])
        if set(scope) != {"scope", "hostId", "complete", "roots"} or scope["scope"] != "workspace-windows":
            _refuse("INVALID_REPORT", "unknown foreground scope")
        roots = []
        for raw in _array(scope["roots"]):
            root = _mapping(raw)
            if set(root) != {"rootId", "z"}:
                _refuse("INVALID_REPORT", "invalid foreground root")
            roots.append({"rootId": _id(root["rootId"]), "z": _number(root["z"])})
        if len(roots) > 64 or len({root["rootId"] for root in roots}) != len(roots):
            _refuse("INVALID_REPORT", "ambiguous or excessive foreground roots")
        result["foreground"] = {"scope": scope["scope"], "hostId": _id(scope["hostId"]),
            "complete": _boolean(scope["complete"]), "roots": roots}
    # Legacy projections/causal results are bounded JSON facts, not confirmation here.
    for key in ("applied", "user", "commandResults"):
        values = _array(message.get(key, []))
        if len(values) > 120:
            _refuse("REPORT_TOO_LARGE", "command projection bound exceeded")
        result[key] = _json_value(values)
    # A renderer view reference is an identity, not live-registration authority.
    for row in result["commandResults"]:
        if isinstance(row, dict) and row.get("presentationId") in items and "legacy" in items[row["presentationId"]].get("view", {}):
            _refuse("INVALID_REPORT", "structural surface cannot supply native command results")
        if isinstance(row, dict) and "viewDrawRef" in row:
            row["viewDrawRef"] = None if row["viewDrawRef"] is None else _id(row["viewDrawRef"])
    envelope, interaction_seed = result, interaction
    extra_sources, kept_edges = [], []
    sized_fields = (*collections, "applied", "user", "commandResults")
    def byte_size(value):
        return len(json.dumps(value, allow_nan=False, separators=(",", ":")).encode())
    encoded_sizes = {id(row): byte_size(row) for rows in (*collections.values(),
        *(envelope[key] for key in ("applied", "user", "commandResults"))) for row in rows}

    def projected_size(value):
        return byte_size({**value, **{key: [] for key in sized_fields}}) + sum(
            sum(encoded_sizes[id(row)] for row in value[key]) + max(0, len(value[key]) - 1)
            for key in sized_fields)

    def project(kept_items, kept_sources, kept_targets, kept_edges):
        result = dict(envelope)
        interaction = _json_value(interaction_seed)
        focus = interaction["focus"]
        retained = {i["id"] for i in kept_items} | {s["key"] for s in kept_sources} | {t["id"] for t in kept_targets}
        retained.update(c["id"] for i in kept_items for c in i["controls"])
        result["totals"], result["omitted"] = dict(totals), {}
        for key, kept in (("items", kept_items), ("sources", kept_sources), ("targets", kept_targets), ("edges", kept_edges)):
            result[key] = kept
            result["omitted"][key] = totals[key] - len(kept)
        result["complete"] = declared_complete and not any(result["omitted"].values())
        if "foreground" in envelope:
            result["foreground"] = {**envelope["foreground"],
                "complete": envelope["foreground"]["complete"] and not result["omitted"]["items"]}
        if focus["presentationId"] is not None and (focus["presentationId"] not in retained or
                (focus["controlId"] is not None and focus["controlId"] not in retained)):
            focus.update(presentationId=None, controlId=None, available=False)
            interaction["unknown"].append("omitted-current-focus")
        for point in [interaction["pointer"], interaction["lastContentPointer"], *interaction["contacts"]]:
            if point is not None:
                if len(point["hitPath"]) > limits.candidates or len(point["targetCandidates"]) > limits.candidates:
                    _refuse("TARGET_NOT_REPRESENTABLE", "pointer candidate bound exceeded")
                # A cut path must never look like a full reliable hit.
                unresolved = (any(ref not in retained for ref in point["hitPath"] + point["targetCandidates"]) or
                              any(point.get(key) is not None and point[key] not in retained
                                  for key in ("presentationId", "sourceKey", "capturedBy")))
                if unresolved:
                    point["hitPath"] = []
                    point["targetCandidates"] = []
                    point["sourcePoint"] = None
                    point["available"] = False
                    if not point.get("historical"):
                        point.update(presentationId=None, sourceKey=None, capturedBy=None)
                        point.pop("sourceGeneration", None)
                    interaction["unknown"].append("omitted-pointer-hit")
        contact_cut = max(0, len(interaction["contacts"]) - limits.contacts)
        interaction["contacts"] = interaction["contacts"][:limits.contacts]
        interaction["omittedContacts"] += contact_cut
        if interaction["omittedContacts"] or interaction["unknown"]:
            interaction["availability"] = "partial"
        result["interaction"] = interaction
        return result

    def projection():
        kept_items = [i for i in all_items if i["id"] in required_items]
        source_keys = direct_sources | {i["sourceKey"] for i in kept_items if "sourceKey" in i}
        kept_sources = [s for s in all_sources if s["key"] in source_keys] + extra_sources
        return project(kept_items, kept_sources, kept_targets, kept_edges)

    def fits():
        source_keys = direct_sources | {items[pid]["sourceKey"] for pid in required_items
                                       if "sourceKey" in items[pid]}
        return (len(required_items) <= limits.items and len(source_keys) + len(extra_sources) <= limits.sources
                and projected_size(projection()) <= limits.normalized_bytes)

    for target in kept_targets:
        require_target(target)
    require(focus["presentationId"])
    if focus["presentationId"] is not None:
        require(focus["controlId"])
    for point in current_points:
        require(point.get("presentationId"))
        require(point.get("capturedBy"))
        for ref in point["hitPath"]:
            require(ref)
        if point.get("sourceKey") is not None:
            direct_sources.add(point["sourceKey"])
    for point in (interaction["lastContentPointer"], interaction["lastContentFocus"]):
        if point and point.get("presentationId") in items:
            require(point["presentationId"])
            if point.get("available") and point.get("controlId") is not None:
                require(point["controlId"])
            if point.get("available") and point.get("sourceKey") is not None:
                direct_sources.add(point["sourceKey"])
    if not fits():
        _refuse("TARGET_NOT_REPRESENTABLE", "required presentations/sources cannot fit")
    # Keep optional registered targets only when their complete dependency closure
    # still fits; they must not crowd current focus/hits out with background roots.
    for target in all_targets:
        if target["id"] in required_targets or len(kept_targets) >= limits.targets:
            continue
        prior_items, prior_sources = required_items.copy(), direct_sources.copy()
        require_target(target)
        kept_targets.append(target)
        if not fits():
            required_items, direct_sources = prior_items, prior_sources
            kept_targets.pop()
    # Background roots are optional too: admit their whole control/source closure
    # only while it fits, rather than making a representable current hit fail.
    for item in all_items:
        if item["id"] in required_items:
            continue
        prior_items, prior_sources = required_items.copy(), direct_sources.copy()
        require(item["id"])
        if not fits():
            required_items, direct_sources = prior_items, prior_sources
    current = projection()
    required_sources = {s["key"] for s in current["sources"]}
    for source in all_sources:
        if source["key"] in required_sources:
            continue
        extra_sources.append(source)
        if not fits():
            extra_sources.pop()
    current = projection()
    retained = {i["id"] for i in current["items"]} | {s["key"] for s in current["sources"]} | {t["id"] for t in kept_targets}
    retained.update(c["id"] for i in current["items"] for c in i["controls"])
    for edge in edges:
        if len(kept_edges) >= limits.edges:
            break
        if edge["from"] not in retained or edge["to"] not in retained:
            continue
        kept_edges.append(edge)
        if not fits():
            kept_edges.pop()
    result = projection()
    if len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode()) > limits.normalized_bytes:
        _refuse("REPORT_TOO_LARGE", "normalized observation byte limit")
    return Observation(_freeze(result), client, app_run_id, received_at)


def front(wire: Mapping) -> dict:
    """What is in front in one observation (docs/architecture/on-screen.md): the Project and Tab its Tab strip reports
    (with the Tab's title), the window highest in the stacking among the windows open and laid out on that Tab, and
    whether the studio itself was in view and focused when it reported. A fact the report does not give is None, never
    guessed: no Tab strip, no Tab; no window open, no window; another Project's or Tab's windows never count."""
    from .screen import closable

    items = list(wire.get("items") or [])
    strip = next((i for i in items if i.get("id") == "workspace-tabs"), None)
    workspace = ((strip or {}).get("view") or {}).get("legacy", {}).get("workspace") or {}
    project, tab = workspace.get("project"), workspace.get("tab")

    def legacy(item):
        return (item.get("view") or {}).get("legacy") or {}

    def here(item):
        document = legacy(item).get("document")
        return document is None or (document.get("project") == project and document.get("tab") == tab)

    windows = [i for i in items if i.get("id") != "workspace-tabs" and closable(i) and i.get("open") and i.get("visible")
               and here(i)]
    window = max(windows, key=lambda i: i.get("z") or 0, default=None)
    viewport = wire.get("viewport") or {}
    # The Tab in front's specimen (the live view), as its Tab strip reports it: part of what that Tab holds.
    strip_tab = next((t for t in workspace.get("tabs") or () if t.get("front")), None) or {}
    specimen = strip_tab.get("specimen")
    drawn = overlays(wire) if (specimen or {}).get("state") != "hidden" else []
    return {"project": project, "tab": tab, "tabTitle": workspace.get("tabTitle"),
            "window": window["id"] if window is not None else None,
            "title": (window.get("title") or legacy(window).get("title")) if window is not None else None,
            "specimen": specimen,
            # What the Tab in front holds, counted by kind (workspace-model.ts), the layers drawn on its specimen, and
            # its magnifiers: None when the report does not say.
            "holds": strip_tab.get("holds"), "overlays": drawn or None, "magnifiers": magnifiers(wire) or None,
            "studioVisible": viewport.get("visible"), "studioFocused": viewport.get("documentFocused")}


def overlays(wire: Mapping) -> list[dict]:
    """The layers drawn on the live specimen now, as the report shows them: each layer of cell outlines (`layer:<id>`)
    and of motion tracks (`tracks:<id>`) open and drawn, and the named outlines drawn (`outlines`, with how many)."""
    out = []
    for item in wire.get("items") or ():
        iid, legacy = str(item.get("id") or ""), ((item.get("view") or {}).get("legacy") or {})
        if iid.startswith(("layer:", "tracks:")) and item.get("open") and item.get("visible"):
            kind = "outlines" if iid.startswith("layer:") else "tracks"
            members = legacy.get("members") or {}
            out.append({"id": iid, "kind": kind, "title": legacy.get("title") or item.get("title"),
                        **({"members": members.get("count")} if "count" in members else {})})
        elif iid == "outlines" and item.get("open") and legacy.get("drawn"):
            out.append({"id": iid, "kind": "outlines", "title": "named outlines", "members": len(legacy["drawn"])})
    return out[:8]


def magnifiers(wire: Mapping) -> list[str]:
    """The magnifiers open now: the legacy magnifier, and each native window showing the live view magnified."""
    sources = {s.get("key"): s for s in wire.get("sources") or ()}
    out = []
    for item in wire.get("items") or ():
        iid = str(item.get("id") or "")
        if iid == "magnifier" and item.get("open"):
            out.append(iid)
        elif iid.startswith("w-") and item.get("open") and (sources.get(item.get("sourceKey")) or {}).get("kind") == "live":
            out.append(iid)
    return out[:8]


def tabs_holding(wire: Mapping, kind: str) -> list[dict]:
    """The open Tabs whose items include `kind` (a table, outlines, a video...), as the Tab strip reports what each
    holds: "which Tab has the measurements?" answered from kinds, never from titles. Each as {id, title, front, n}."""
    strip = next((i for i in wire.get("items") or () if i.get("id") == "workspace-tabs"), None)
    workspace = ((strip or {}).get("view") or {}).get("legacy", {}).get("workspace") or {}
    return [{"id": t["id"], "title": t.get("title"), "front": bool(t.get("front")), "n": (t.get("holds") or {})[kind]}
            for t in workspace.get("tabs") or () if (t.get("holds") or {}).get(kind)]


def pin_request(observation: Observation, *, snapshot_id: str, timing: str,
                requested_at: float, max_report_age_ms: float) -> RequestSnapshot:
    if timing not in ("client-send", "server-final-transcript", "server-handoff-arrival", "server-receive-legacy"):
        _refuse("INVALID_REPORT", "unknown request observation timing")
    requested_at = _number(requested_at, nonnegative=True)
    maximum = _number(max_report_age_ms, nonnegative=True)
    if requested_at < observation.received_at:
        _refuse("INVALID_REPORT", "request precedes report receipt")
    age = (requested_at - observation.received_at) * 1000
    # A studio in the background reports only when it changes or is shown again: its last report stands.
    background = observation._data["viewport"].get("visible") is False
    availability = "stale" if age > maximum and not background else observation._data["interaction"]["availability"]
    return RequestSnapshot(observation, _id(snapshot_id), timing, requested_at, age, availability)


def check_target_current(snapshot: RequestSnapshot, explicit_target_id: str,
                         current: Observation, *, properties=()) -> Binding:
    if not isinstance(properties, (tuple, list)) or len(properties) > 8:
        _refuse("INVALID_REPORT", "touched properties must be a bounded list")
    original = snapshot.observation
    if original.client != current.client:
        _refuse("CLIENT_MISMATCH", "request and current observation differ")
    if original.app_run_id != current.app_run_id:
        _refuse("RUN_MISMATCH", "request belongs to another run")
    if current._data["seq"] < original._data["seq"]:
        _refuse("REPORT_REPLAYED", "current report predates the request snapshot")
    target = next((t for t in original._data["targets"] if t["id"] == explicit_target_id), None)
    if target is None:
        _refuse("TARGET_UNRESOLVED", "not a registered request-time target ID")
    pid = target.get("presentationId")
    if pid is None:
        # Nonpresentation target checks belong to the existing Board/evidence authority.
        _refuse("TARGET_UNRESOLVED", "presentation guard cannot authorize this target kind")
    before = next(i for i in original._data["items"] if i["id"] == pid)
    after = next((i for i in current._data["items"] if i["id"] == pid), None)
    if after is None:
        code = "TARGET_GONE" if current._data["complete"] else "OBSERVATION_INCOMPLETE"
        _refuse(code, "original presentation is not represented")
    if (after["lifetimeGeneration"] != before["lifetimeGeneration"] or
            after["rootId"] != before["rootId"] or not after["open"] or not after["connected"]):
        _refuse("TARGET_GONE", "original presentation lifetime ended")
    for key in ("sourceKey", "sourceToken", "sourceGeneration"):
        if before.get(key) != after.get(key):
            _refuse("SOURCE_CHANGED", "original source binding changed")
    source_key = before.get("sourceKey")
    old_source = None
    if source_key is not None:
        old_source = next(s for s in original._data["sources"] if s["key"] == source_key)
        new_source = next((s for s in current._data["sources"] if s["key"] == source_key), None)
        if not _same_source(old_source, new_source):
            _refuse("SOURCE_CHANGED", "original source identity/provenance changed")
    for prop in properties:
        _id(prop)
        if (prop not in before["propertyVersions"] or prop not in after["propertyVersions"] or
                prop not in before["propertyOwners"] or prop not in after["propertyOwners"] or
                before["propertyVersions"][prop] != after["propertyVersions"][prop] or
                before["propertyOwners"].get(prop) != after["propertyOwners"].get(prop)):
            _refuse("OWNERSHIP_STALE", "touched property changed or lacks a guard")
    return Binding(_freeze({**_thaw(target), "snapshotId": snapshot.snapshot_id,
                            "client": original.client.wire(), "appRunId": original.app_run_id,
                            "sourceKey": source_key, "source": _thaw(old_source),
                            "rootId": before["rootId"],
                            "propertyVersions": {prop: before["propertyVersions"][prop] for prop in properties},
                            "propertyOwners": {prop: before["propertyOwners"][prop] for prop in properties},
                            "lifetimeGeneration": before["lifetimeGeneration"],
                            "sourceGeneration": before["sourceGeneration"], "sourceToken": before.get("sourceToken")}))


def workspace_to_source(css_point, item: dict) -> tuple[float, float] | None:
    x, y = _vector(css_point, 2)
    for key in ("contentRect", "clipRect"):
        if item.get(key) is None:
            return None
        left, top, width, height = _rect(item[key])
        if not (left <= x < left + width and top <= y < top + height):
            return None
    if item.get("viewTransform") is None:
        return None
    transform = _transform(item["viewTransform"])
    a, b, c, d, tx, ty = transform["sourceToWorkspace"]
    determinant = a * d - b * c
    if not math.isfinite(determinant) or abs(determinant) <= 1e-12:
        return None
    sx = (d * (x - tx) - c * (y - ty)) / determinant
    sy = (-b * (x - tx) + a * (y - ty)) / determinant
    width, height = transform["originalSize"]
    if not (math.isfinite(sx) and math.isfinite(sy) and 0 <= sx < width and 0 <= sy < height):
        return None
    return (sx, sy)


CAPTURE_UNAVAILABLE_CODES = frozenset((
    "share-required", "unsupported-api", "unsupported-handle", "permission-declined",
    "wrong-surface", "track-unavailable", "session-retired", "layout-changed",
    "unsupported-mapping", "sample-unavailable", "encode-failed", "bounds-exceeded"))


def normalize_capture_unavailable(message: dict, *, client: ClientRef, expected: dict,
                                  limits: Limits | None = None) -> str:
    """A bounded original-acquisition refusal carries no pixels or success authority."""
    limits = limits or Limits()
    _preflight(message, limits)
    message = _mapping(message)
    keys = {"type", "schema", "status", "id", "lookId", "requestId", "appRunId", "client",
            "originSnapshotId", "code"}
    if set(message) != keys or message["type"] != "screen-image" or message["schema"] != "workspace-capture-v1" or message["status"] != "unavailable":
        _refuse("INVALID_REPORT", "unknown or incomplete capture refusal")
    _match_client(message["client"], client, closed=True)
    for key in ("lookId", "requestId", "appRunId", "originSnapshotId"):
        if message[key] != expected.get(key) or not expected.get(key):
            _refuse("CAPTURE_MISMATCH", f"pending capture {key} differs")
        _id(message[key])
    if message["id"] != message["lookId"]:
        _refuse("CAPTURE_MISMATCH", "capture look identifiers disagree")
    code = message["code"]
    if not isinstance(code, str) or code not in CAPTURE_UNAVAILABLE_CODES:
        _refuse("INVALID_REPORT", "unknown capture refusal code")
    return code


def _capture_basis(value, *, capture_time, uncertainty) -> dict:
    value = _mapping(value)
    keys = {"schema", "captureSessionId", "surfaceOrigin", "pageHandle", "structuralReportSeq",
            "viewport", "sampledBeforeMonoMs", "sampledAfterMonoMs", "mediaTimeSeconds",
            "rootPaintInclusion", "rootPaintReason"}
    if set(value) != keys or value["schema"] != "installed-studio-tab-v1":
        _refuse("INVALID_REPORT", "unknown or incomplete installed capture basis")
    if value["rootPaintInclusion"] != "unknown" or value["rootPaintReason"] != "tab-track-has-no-atomic-root-paint-manifest":
        _refuse("INVALID_REPORT", "a tab track cannot certify individual root paint")
    result = {"schema": value["schema"], "rootPaintInclusion": "unknown",
              "rootPaintReason": value["rootPaintReason"]}
    for key in ("captureSessionId", "surfaceOrigin", "pageHandle"):
        result[key] = _id(value[key])
    result["structuralReportSeq"] = _integer(value["structuralReportSeq"])
    viewport = _mapping(value["viewport"])
    if set(viewport) - {"layoutSize", "visualRect", "visualScale", "documentScroll", "dpr",
                        "visible", "documentFocused", "activeSurface", "presentationArea"}:
        _refuse("INVALID_REPORT", "unknown capture viewport facts")
    result["viewport"] = _viewport(viewport)
    for key in ("sampledBeforeMonoMs", "sampledAfterMonoMs"):
        result[key] = _number(value[key], nonnegative=True)
    before, after = result["sampledBeforeMonoMs"], result["sampledAfterMonoMs"]
    if not before <= capture_time <= after or uncertainty < max(capture_time - before, after - capture_time):
        _refuse("INVALID_REPORT", "capture time is outside its declared sampling uncertainty")
    media = value["mediaTimeSeconds"]
    result["mediaTimeSeconds"] = None if media is None else _number(media, nonnegative=True)
    return result


def normalize_capture_reply(message: dict, *, client: ClientRef, expected: dict,
                            limits: Limits | None = None) -> Binding:
    """Validate injected reply metadata; never acquire, decode, persist or certify pixels."""
    limits = limits or Limits()
    message = _mapping(message)
    jpeg = message.get("jpeg")
    if not isinstance(jpeg, bytes) or not jpeg:
        _refuse("INVALID_REPORT", "capture needs separately leased nonempty bytes")
    if len(jpeg) > limits.image_bytes:
        _refuse("IMAGE_TOO_LARGE", "capture byte bound exceeded")
    _preflight(message, limits, omit_key="jpeg")
    _match_client(message.get("client"), client,
                  closed="schema" in message or "status" in message)
    result = {"client": client.wire()}
    if "schema" in message or "status" in message:
        if message.get("schema") != "workspace-capture-v1" or message.get("status") != "captured":
            _refuse("INVALID_REPORT", "unknown capture success envelope")
        result.update(schema="workspace-capture-v1", status="captured")
    for key in ("lookId", "requestId", "appRunId", "originSnapshotId"):
        if message.get(key) != expected.get(key) or not expected.get(key):
            _refuse("CAPTURE_MISMATCH", f"pending capture {key} differs")
        result[key] = _id(message[key])
    result["capturedObservationId"] = _id(message.get("capturedObservationId"))
    before = _integer(message.get("layoutRevisionBefore"))
    after = _integer(message.get("layoutRevisionAfter"))
    if before != after or before != expected.get("layoutRevision"):
        _refuse("LAYOUT_CHANGED", "capture layout differs or changed during acquisition")
    result.update(layoutRevisionBefore=before, layoutRevisionAfter=after)
    for key in ("originalSize", "inputSize"):
        size = _vector(message.get(key), 2, dimensions=True)
        if any(v != int(v) for v in size):
            _refuse("INVALID_GEOMETRY", "capture pixel dimensions must be integral")
        if size[0] * size[1] > limits.image_pixels:
            _refuse("IMAGE_TOO_LARGE", "capture pixel bound exceeded")
        if key == "inputSize" and max(size) > limits.image_edge:
            _refuse("IMAGE_TOO_LARGE", "model input longest edge bound exceeded")
        result[key] = size
    result["workspaceToImage"] = _vector(message.get("workspaceToImage"), 6)
    a, b, c, d, _, _ = result["workspaceToImage"]
    if not math.isfinite(a * d - b * c) or abs(a * d - b * c) <= 1e-12:
        _refuse("INVALID_GEOMETRY", "capture mapping is not invertible")
    for key in ("captureTimeMonoMs", "uncertaintyMs"):
        result[key] = _number(message.get(key), nonnegative=True)
    fidelity = message.get("fidelity")
    method = _id(message.get("captureMethod"))
    if fidelity not in ("actual-tab-capture", "approximate-composition"):
        _refuse("INVALID_REPORT", "unknown capture fidelity")
    proof = message.get("surfaceProof")
    if fidelity == "actual-tab-capture":
        if proof not in ("same-origin-page-handle", "verified-tab-track"):
            _refuse("CAPTURE_WRONG_SURFACE", "trusted adapter supplied no recognized surface proof")
        if method not in ("one-shot-tab-track", "authorized-tab-track"):
            _refuse("CAPTURE_WRONG_SURFACE", "method cannot supply an actual tab capture")
    elif method != "approximate-dom-media-compositor":
        _refuse("INVALID_REPORT", "approximate fidelity and method differ")
    coverage = message.get("coverage")
    if coverage not in ("complete", "partial", "unknown"):
        _refuse("INVALID_REPORT", "unknown capture coverage")
    result.update(fidelity=fidelity, captureMethod=method, surfaceProof=proof, coverage=coverage)
    result["redactedRects"] = [_rect(v) for v in _array(message.get("redactedRects", []))]
    result["missing"] = [_id(v) for v in _array(message.get("missing", []))]
    if len(result["redactedRects"]) > 40 or len(result["missing"]) > 40:
        _refuse("REPORT_TOO_LARGE", "capture coverage metadata bound exceeded")
    if fidelity == "approximate-composition" and (result["missing"] or result["redactedRects"]):
        result["coverage"] = "partial"
    if result.get("schema") == "workspace-capture-v1":
        result["acquisitionBasis"] = _capture_basis(message.get("acquisitionBasis"),
            capture_time=result["captureTimeMonoMs"], uncertainty=result["uncertaintyMs"])
    if len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode()) > limits.normalized_bytes:
        _refuse("REPORT_TOO_LARGE", "capture metadata byte bound exceeded")
    return Binding(_freeze(result))
