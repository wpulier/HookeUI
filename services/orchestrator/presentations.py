"""Bounded independent presentation instances, behind the existing present/apply seam.

This module holds descriptors and desired state, never pixels, resources, model calls,
transport, a renderer or a completion scheduler. The resource reader and request guard
are supplied. Forward commit/retire_group use the existing synchronous apply writer;
native rollback guards its original occurrence separately for conditional compensation.
Screen supplies trusted observations and owns complete postcondition confirmation.
"""
from __future__ import annotations

import json
import math
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Callable, Mapping
from uuid import UUID, uuid4


class PresentationError(ValueError):
    def __init__(self, code: str, reason: str, *, next: str | None = None):
        super().__init__(reason)
        self.code, self.reason, self.next = code, reason, next


def _fail(code, reason, next=None):
    raise PresentationError(code, reason, next=next)


def _text(value, name, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit:
        _fail("invalid_action", f"{name} needs a nonempty bounded string")
    return value


def _number(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        _fail("invalid_action", f"{name} needs a finite {'positive ' if positive else ''}number")
    return float(value)


def _point(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        _fail("invalid_action", f"{name} needs two numbers")
    return tuple(_number(v, name) for v in value)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _json(value, *, limit=65536):
    try:
        encoded = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > limit:
            _fail("invalid_action", "descriptor exceeds the admitted size")
        # Reject excessive nesting before constructing a recursively frozen descriptor.
        def depth(v, n=0):
            if n > 16:
                _fail("invalid_action", "descriptor exceeds the admitted depth")
            if isinstance(v, dict):
                for child in v.values():
                    depth(child, n + 1)
            elif isinstance(v, (list, tuple)):
                for child in v:
                    depth(child, n + 1)
        depth(value)
        return encoded
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PresentationError):
            raise
        _fail("invalid_action", "descriptor must be finite bounded JSON data")


@dataclass(frozen=True)
class ResourceRef:
    day: str
    id: str
    version: int | None
    picture: int | None

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict) or set(("day", "id", "version", "picture")) - value.keys():
            _fail("invalid_action", "resource requires exact day/id/version/picture")
        _text(value["day"], "source day", 32)
        _text(value["id"], "resource id")
        for key in ("version", "picture"):
            if value[key] is not None and (type(value[key]) is not int or value[key] < 0):
                _fail("invalid_action", f"resource {key} must be nonnegative or explicitly unknown")
        return cls(*(value[k] for k in ("day", "id", "version", "picture")))


@dataclass(frozen=True)
class SourceRef:
    """Immutable producer-qualified descriptor. JSON storage prevents nested aliases."""
    _wire: str = field(repr=False)

    @classmethod
    def from_wire(cls, value):
        if not isinstance(value, dict):
            _fail("invalid_action", "source must be a descriptor")
        for key in ("key", "kind", "media", "identity", "provenance", "availability"):
            if key not in value:
                _fail("invalid_action", f"source requires {key}")
        for key in ("key", "kind", "media", "availability"):
            _text(value[key], "source " + key)
        if value["kind"] not in ("live", "saved-image", "video", "lesson", "written", "composite", "web-page"):
            _fail("invalid_action", "source kind is not declared")
        # A web page or video from the web (web_hosts.py): only an allowed host's framed address, never another page.
        if value["kind"] == "web-page" or (value["kind"] == "video" and value.get("provenance", {}).get("basis") == "web"):
            from .web_hosts import framed

            url = value["identity"].get("url") if isinstance(value.get("identity"), dict) else None
            if not isinstance(url, str) or not framed(url, value["kind"]):
                _fail("invalid_action", "a web source must be an allowed host's framed address")
        # P5 media words remain intact; the source contract's richer lineage words
        # are accepted without deriving either classification from a title.
        if value["media"] not in ("capture", "labelled", "reference", "generated", "lesson", "written", "unknown",
                                  "raw", "derived", "annotated", "external-reference"):
            _fail("invalid_action", "source media kind is not declared")
        if value["availability"] not in ("retained", "temporary", "missing", "earlier-run", "unknown"):
            _fail("invalid_action", "source availability is not declared")
        if not isinstance(value["identity"], dict) or not isinstance(value["provenance"], dict):
            _fail("invalid_action", "source identity/provenance must be objects")
        if value["kind"] == "live":
            if value["provenance"].get("basis") == "tracked-object":
                if set(("run", "object", "day", "anchorCapture")) - value["identity"].keys():
                    _fail("invalid_action", "tracked object source needs run/object/day/anchorCapture")
                for key in ("run", "object", "day", "anchorCapture"):
                    _text(value["identity"][key], "live " + key)
            elif value["provenance"].get("basis") == "current-camera":
                # A continuing producer has an incarnation, not an invented
                # historical object anchor. Its broker still validates ownership.
                if set(("run", "resource", "producerGeneration")) - value["identity"].keys():
                    _fail("invalid_action", "current-camera source needs run/resource/producerGeneration")
                for key in ("run", "producerGeneration"):
                    _text(value["identity"][key], "live " + key)
                if "digest" not in value or value["digest"] is not None:
                    _fail("invalid_action", "current-camera source needs an explicit null mutable-content digest")
            else:
                if set(("run", "objectId", "anchorCapture", "epoch")) - value["identity"].keys():
                    _fail("invalid_action", "live source needs run/object/anchor/epoch")
                for key in ("run", "objectId", "anchorCapture"):
                    _text(value["identity"][key], "live " + key)
                if type(value["identity"]["epoch"]) is not int or value["identity"]["epoch"] < 0:
                    _fail("invalid_action", "live epoch must be a nonnegative integer")
        if "resource" in value["identity"]:
            ResourceRef.from_wire(value["identity"]["resource"])
        components = value.get("components", [])
        if not isinstance(components, (list, tuple)) or len(components) > 8:
            _fail("invalid_action", "source supports at most eight recorded components")
        encoded = _json(value)
        for child in components:
            cls.from_wire(child)  # full exact refs, never guessed bitmap regions
        return cls(encoded)

    def to_wire(self):
        return json.loads(self._wire)

    @property
    def key(self):
        return self.to_wire()["key"]

    @property
    def kind(self):
        return self.to_wire()["kind"]

    @property
    def media(self):
        return self.to_wire()["media"]

    @property
    def availability(self):
        return self.to_wire()["availability"]

    @property
    def identity(self):
        return _freeze(self.to_wire()["identity"])

    @property
    def components(self):
        return tuple(SourceRef.from_wire(c) for c in self.to_wire().get("components", []))

    def with_availability(self, availability):
        wire = self.to_wire()
        wire["availability"] = availability
        return SourceRef.from_wire(wire)

    def same_identity(self, other):
        a, b = self.to_wire(), other.to_wire()
        fields = ("key", "kind", "media", "identity", "provenance")
        return (all(a.get(k) == b.get(k) for k in fields)
                and (not a.get("digest") or a.get("digest") == b.get("digest"))
                and len(self.components) == len(other.components)
                and all(left.same_identity(right) for left, right in zip(self.components, other.components)))


@dataclass(frozen=True)
class ResolvedSource:
    """Optional injected resource lease; release belongs to the supplying store."""
    source: SourceRef
    release: Callable[[], None] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class PresentationContext:
    workspace_id: str
    client_id: str
    client_epoch: str
    request_id: str
    owner: str
    connection_epoch: str | None = None

    @property
    def client(self):
        return self.client_id, self.client_epoch, self.connection_epoch


@dataclass(frozen=True)
class PresentationAction:
    op: str
    instance: str | None = None
    source: SourceRef | None = None
    values: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class PresentationView:
    zoom: float = 1.0
    pan: tuple[float, float] = (0.5, 0.5)
    opacity: float = 1.0
    hidden: bool = False
    group: str | None = None


@dataclass(frozen=True)
class GeometryIntent:
    position: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] | None = None
    relation_json: str | None = None


@dataclass(frozen=True)
class PresentationObservation:
    instance_id: str
    command_id: str
    source_key: str
    source_generation: int
    lifetime_generation: str
    revision: int
    phase: str
    visible: bool
    root_id: str
    render_token: str
    rect: tuple[float, float, float, float]
    content_rect: tuple[float, float, float, float]
    natural_size: tuple[int, int]
    property_versions: Mapping[str, int]
    # These optional facts are supplied by Screen's normalized trusted projection.
    connected: bool = True
    report_sequence: int | None = None
    transaction_id: str | None = None
    controls: Mapping | None = None
    absent: bool = False  # actual closed command result after removal, with no geometry

    def __post_init__(self):
        object.__setattr__(self, "property_versions", MappingProxyType(dict(self.property_versions)))
        for name in ("rect", "content_rect", "natural_size"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.controls is not None:
            object.__setattr__(self, "controls", _freeze(json.loads(_json(dict(self.controls)))))


@dataclass(frozen=True)
class PresentationState:
    id: str
    workspace_id: str
    source: SourceRef
    revision: int
    source_generation: int
    lifetime_generation: str
    open: bool
    view: PresentationView
    geometry_intent: GeometryIntent
    owners: Mapping[str, str]
    property_versions: Mapping[str, int]
    observed: PresentationObservation | None
    staged: bool
    title: str

    @property
    def kind(self):
        return {"live": "magnifier", "saved-image": "image", "composite": "comparison"}.get(self.source.kind, self.source.kind)


@dataclass(frozen=True)
class CommittedTarget:
    instance_id: str
    source_key: str
    source_generation: int
    lifetime_generation: str
    revision: int
    property_versions: Mapping[str, int]

    def __post_init__(self):
        object.__setattr__(self, "property_versions", MappingProxyType(dict(self.property_versions)))


@dataclass(frozen=True)
class PresentationEffect:
    command_id: str
    targets: tuple[str, ...]
    phase: str = "committed"
    parent_id: str | None = None
    context: PresentationContext | None = None
    app_run: str | None = None
    expected: tuple[CommittedTarget, ...] = ()
    retirement: "PresentationEffect | None" = None


@dataclass(frozen=True)
class ObservationResult:
    accepted: tuple[str, ...]
    rejected: tuple[str, ...]


@dataclass(frozen=True)
class RollbackResult:
    restored: tuple[str, ...]
    preserved: tuple[str, ...]
    errors: tuple[tuple[str, str], ...] = ()
    closes: tuple[PresentationEffect, ...] = ()


@dataclass(frozen=True)
class _Fence:
    instance_id: str
    lifetime: str
    source_generation: int
    versions: Mapping[str, int]


@dataclass(frozen=True)
class PreparedPresentation:
    """Opaque preparation handle; all mutable work is private to the registry."""
    id: str


@dataclass
class _Work:
    context: PresentationContext
    workspace_generation: int
    op: str
    targets: tuple[str, ...]
    writes: dict[str, dict]
    fences: tuple[_Fence, ...]
    created: dict[str, ResolvedSource] = field(default_factory=dict)
    replacement: ResolvedSource | None = None
    parent_id: str | None = None
    effect: PresentationEffect | None = None
    before: dict[str, dict] = field(default_factory=dict)
    written: dict[str, dict[str, int]] = field(default_factory=dict)
    created_versions: dict[str, dict[str, int]] = field(default_factory=dict)
    retired: bool = False
    compensation_of: str | None = None


@dataclass
class _Instance:
    id: str
    source: SourceRef
    lifetime: str = field(default_factory=lambda: str(uuid4()))
    source_generation: int = 1
    revision: int = 1
    values: dict = field(default_factory=lambda: {
        "open": True, "zoom": 1.0, "pan": (0.5, 0.5),
        "geometry": GeometryIntent(), "front": 0, "opacity": 1.0, "hidden": False, "group": None,
    })
    versions: dict = field(default_factory=lambda: {p: 1 for p in ("open", "source", "zoom", "pan", "geometry", "front", "title", "opacity", "hidden", "group")})
    owners: dict = field(default_factory=dict)
    observed: PresentationObservation | None = None
    staged: bool = False
    title: str = ""
    lease: ResolvedSource | None = None


class Presentations:
    """One workspace-local registry; resolution and apply/Screen remain existing seams."""
    def __init__(self, *, resolve_source, app_run, max_windows=12, max_live=4,
                 check_request=None, max_workspaces=8, max_pending=32, max_receipts=256,
                 resolve_source_for=None, check_compensation=None):
        for value in (max_windows, max_live, max_workspaces, max_pending, max_receipts):
            if type(value) is not int or value <= 0:
                _fail("invalid_action", "admission bounds must be positive integers")
        self.resolve_source, self.app_run = resolve_source, _text(app_run, "app run")
        self.resolve_source_for = resolve_source_for
        self.max_windows, self.max_live = min(max_windows, 12), min(max_live, 4)
        self.max_workspaces, self.max_pending = min(max_workspaces, 8), min(max_pending, 32)
        self.max_receipts = min(max_receipts, 256)
        self.check_request = check_request or (lambda _: None)
        self.check_compensation = check_compensation or self.check_request
        self._clients: dict[str, tuple] = {}
        self._instances: dict[str, OrderedDict[str, _Instance]] = {}
        self._generations: dict[str, int] = {}
        self._pending: dict[str, _Work] = {}
        self._done: OrderedDict[str, _Work] = OrderedDict()
        self._canceled: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._release_errors: list[str] = []
        self._front = 0
        self._observation_batch = 0
        self._observed_batches: dict[str, int] = {}

    def connect(self, ctx):
        for name in ("workspace_id", "client_id", "client_epoch", "request_id", "owner"):
            _text(getattr(ctx, name), name)
        if ctx.connection_epoch is not None:
            _text(ctx.connection_epoch, "connection epoch")
        if ctx.workspace_id not in self._instances:
            if len(self._instances) >= self.max_workspaces:
                _fail("capacity_unavailable", "workspace capacity reached", "close an unused workspace")
            self._instances[ctx.workspace_id] = OrderedDict()
            self._generations[ctx.workspace_id] = 0
        elif self._clients.get(ctx.workspace_id) != ctx.client:
            for token, work in tuple(self._pending.items()):
                if work.context.workspace_id == ctx.workspace_id:
                    self.cancel(PreparedPresentation(token), reason="the originating connection changed",
                                code="client_epoch_changed")
        self._clients[ctx.workspace_id] = ctx.client

    def disconnect(self, ctx):
        """Retire this accepted connection's preparations, preserving owned windows."""
        if self._clients.get(ctx.workspace_id) != ctx.client:
            return
        self._clients.pop(ctx.workspace_id)
        for token, work in tuple(self._pending.items()):
            if work.context.workspace_id == ctx.workspace_id and work.context.client == ctx.client:
                self.cancel(PreparedPresentation(token), reason="the originating connection disconnected",
                            code="client_epoch_changed")

    def workspace_generation(self, ctx):
        """Read the connected origin's reset generation, granting no request authority."""
        self._context(ctx, check_request=False)
        return self._generations[ctx.workspace_id]

    def _context(self, ctx, *, check_request=True):
        if self._clients.get(ctx.workspace_id) != ctx.client:
            _fail("client_epoch_changed", "the originating client connection changed", "ask again in this Studio")
        why = self.check_request(ctx) if check_request else None
        if why:
            _fail("request_canceled", str(why), "ask again")

    def _instance(self, workspace, iid, *, opened=False):
        instance = self._instances.get(workspace, {}).get(iid)
        if instance is None or (opened and not instance.values["open"]):
            _fail("target_missing", "that window is not open in this workspace", "choose an open window")
        return instance

    def get(self, workspace, iid):
        obj = self._instance(workspace, iid)
        return PresentationState(
            obj.id, workspace, obj.source, obj.revision, obj.source_generation, obj.lifetime,
            obj.values["open"], PresentationView(*(obj.values[p] for p in ("zoom", "pan", "opacity", "hidden", "group"))),
            obj.values["geometry"], MappingProxyType(dict(obj.owners)), MappingProxyType(dict(obj.versions)),
            obj.observed, obj.staged, obj.title,
        )

    def list(self, workspace):
        return tuple(self.get(workspace, iid) for iid in self._instances.get(workspace, {}))

    def _release(self, lease):
        if lease and lease.release:
            try:
                lease.release()
            except Exception as exc:
                self._release_errors.append(type(exc).__name__)
                del self._release_errors[:-16]

    @property
    def cleanup_errors(self):
        """Bounded store-cleanup failures for the existing diagnostic inspector."""
        return tuple(self._release_errors)

    def _resolved(self, ref, ctx):
        if not isinstance(ref, SourceRef):
            _fail("invalid_action", "open needs an exact SourceRef")
        resolved = self.resolve_source_for(ref, ctx) if self.resolve_source_for is not None else self.resolve_source(ref)
        lease = resolved if isinstance(resolved, ResolvedSource) else ResolvedSource(resolved)
        if not isinstance(lease.source, SourceRef) or not ref.same_identity(lease.source):
            self._release(lease)
            _fail("source_identity_mismatch", "source resolution returned another producer/version", "choose the exact retained source")
        if lease.source.availability not in ("retained", "temporary"):
            self._release(lease)
            _fail("source_missing", "exact source bytes are unavailable or unknown", "choose a retained source")
        if lease.source.kind == "live" and lease.source.identity["run"] != self.app_run:
            self._release(lease)
            _fail("earlier_run", "this live binding belongs to an earlier run", "open a new live view")
        return lease

    def _fence(self, obj, properties):
        return _Fence(obj.id, obj.lifetime, obj.source_generation,
                      MappingProxyType({p: obj.versions[p] for p in properties}))

    @staticmethod
    def _observed_position(obj):
        """Where the studio last showed the window, while no later geometry change was committed; else None."""
        if (obj.observed and obj.observed.visible and len(obj.observed.rect) == 4
                and obj.observed.property_versions.get('geometry') == obj.versions.get('geometry')):
            return tuple(obj.observed.rect[:2])
        return None

    def _guard(self, work):
        self._context(work.context)
        ws = work.context.workspace_id
        if self._generations[ws] != work.workspace_generation:
            _fail("target_superseded", "workspace reset since admission", "ask again")
        for fence in work.fences:
            obj = self._instances[ws].get(fence.instance_id)
            if (obj is None or not obj.values["open"] or obj.lifetime != fence.lifetime
                    or obj.source_generation != fence.source_generation
                    or any(obj.versions[p] != version for p, version in fence.versions.items())):
                _fail("target_superseded", "the target source or affected property changed", "ask again on that window")

    def _capacity(self, workspace, created, *, replacing=None, exclude=None, release_replaced=False, retiring=()):
        objects = [obj for obj in self._instances[workspace].values() if obj.values["open"] and obj.id not in retiring]
        pending_work = [w for token, w in self._pending.items() if w.context.workspace_id == workspace and token != exclude]
        pending = [lease.source for w in pending_work for lease in w.created.values()]
        requested = [lease.source for lease in created.values()]
        windows = len(objects) + len(pending) + len(requested)
        live = sum(self._active_live(obj.source) for obj in objects) + sum(self._active_live(s) for s in pending + requested)
        for work in pending_work:
            if work.replacement is not None:
                obj = self._instances[workspace].get(work.targets[0])
                if obj is not None and obj.values["open"]:
                    # A preparation reserves increases; its future decrease cannot
                    # spend a lens that is still active. Overlaps stay conservative.
                    live += max(0, int(self._active_live(work.replacement.source)) - int(self._active_live(obj.source)))
        if replacing is not None:
            iid, source = replacing
            obj = self._instance(workspace, iid)
            delta = int(self._active_live(source)) - int(self._active_live(obj.source))
            # Only the guarded synchronous writer may credit its own replacement.
            live += delta if release_replaced else max(0, delta)
        if windows > self.max_windows or live > self.max_live:
            _fail("capacity_unavailable", "window or live lens admission limit reached", "close a window or open fewer sources")

    def _active_live(self, source):
        return source.kind == "live" and source.availability in ("retained", "temporary") and source.identity["run"] == self.app_run

    def admit(self, action, ctx):
        self._context(ctx)
        if sum(w.context.workspace_id == ctx.workspace_id for w in self._pending.values()) >= self.max_pending:
            _fail("capacity_unavailable", "too many pending presentation actions", "wait for pending work")
        if not isinstance(action, PresentationAction):
            _fail("invalid_action", "expected a PresentationAction")
        values = json.loads(_json(dict(action.values), limit=8192))
        op = action.op
        allowed = {"open": {"title", "placement"}, "split": set(), "duplicate": set(), "close": set(),
                   "front": set(), "zoom": {"level", "factor", "fit", "size"}, "pan": {"center"},
                   "move": {"position"}, "resize": {"size"}, "arrange": {"targets", "relation"},
                   "opacity": {"value"}, "hide": set(), "restore": set(), "group": {"targets"}, "ungroup": set()}
        if op not in allowed or set(values) - allowed[op]:
            _fail("invalid_action", "unsupported presentation operation or arguments")
        if op != "open" and action.source is not None:
            _fail("invalid_action", "only open takes a source")
        ws, created, replacement = ctx.workspace_id, {}, None
        writes, fences, parent = {}, [], None
        try:
            if op == "open" and action.instance is None:
                iid = "w-" + str(uuid4())
                created[iid] = self._resolved(action.source, ctx)
                targets = (iid,)
                if "title" in values:
                    _text(values["title"], "title", 120)
                if "placement" in values:
                    placement = values['placement']
                    if not isinstance(placement, dict) or set(placement) != {'position','size','relation'}:
                        _fail('invalid_action', 'initial placement needs position, size and relation')
                    relation = placement['relation']
                    if (not isinstance(relation, dict) or set(relation) != {'kind','reference','gapPx','align','side'}
                            or relation['kind'] != 'beside' or relation['align'] != 'top'
                            or relation['side'] not in ('left','right') or _number(relation['gapPx'], 'gap') < 0):
                        _fail('invalid_action', 'initial placement needs an exact beside relation')
                    anchor = self._instance(ws, relation['reference'], opened=True)
                    position = _point(placement['position'], 'initial position')
                    size = _point(placement['size'], 'initial size')
                    if min(size) <= 0:
                        _fail('invalid_action', 'initial size must be positive')
                    writes[iid] = {'geometry': GeometryIntent(position,size,_json(relation,limit=2048))}
                    fences.append(self._fence(anchor, ('geometry','source','open')))
            elif op in ("split", "duplicate"):
                obj = self._instance(ws, action.instance, opened=True)
                parent = obj.id if op == "split" else None
                refs = obj.source.components if op == "split" else (obj.source,)
                if op == "split" and len(refs) < 2:
                    _fail("components_unavailable", "separate original sources were not retained", "use the composite or choose retained originals")
                for ref in refs:
                    created["w-" + str(uuid4())] = self._resolved(ref, ctx)
                targets = tuple(created)
                fences.append(self._fence(obj, obj.versions if op == "split" else ("source", "zoom", "pan")))
                if op == "duplicate":
                    writes[targets[0]] = {"zoom": obj.values["zoom"], "pan": obj.values["pan"]}
            elif op == "group":
                ids = values.get("targets")
                if not isinstance(ids, list) or not 2 <= len(ids) <= 8 or len(set(ids)) != len(ids):
                    _fail("invalid_action", "group needs two to eight distinct windows")
                targets = tuple(ids)
                group = "group-" + str(uuid4())
                for iid in ids:
                    obj = self._instance(ws, iid, opened=True)
                    writes[iid] = {"group": group}
                    fences.append(self._fence(obj, ("group",)))
            elif op == "arrange":
                ids = values.get("targets")
                relation = values.get("relation")
                if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
                        or not all(isinstance(iid, str) for iid in ids)
                        or len(set(ids)) != len(ids) or not isinstance(relation, dict)):
                    _fail("invalid_action", "arrange needs distinct targets and a relation")
                if relation.get("kind") not in ("beside", "above", "below", "row", "column"):
                    _fail("invalid_action", "unsupported arrangement relation")
                if set(relation) - {"kind", "reference", "gapPx", "align", "side"}:
                    _fail("invalid_action", "arrangement has undeclared arguments")
                if "gapPx" in relation and _number(relation["gapPx"], "gap") < 0:
                    _fail("invalid_action", "arrangement gap must be nonnegative")
                if relation.get("align", "top") not in ("top", "center", "bottom"):
                    _fail("invalid_action", "arrangement alignment is unsupported")
                if relation.get("side", "right") not in ("left", "right"):
                    _fail("invalid_action", "arrangement side must be left or right")
                targets = tuple(ids)
                reference = relation.get("reference")
                if reference is not None and reference not in ids:
                    anchor = self._instance(ws, reference, opened=True)
                    fences.append(self._fence(anchor, ("geometry", "source")))
                encoded = _json(relation, limit=2048)
                for iid in ids:
                    obj = self._instance(ws, iid, opened=True)
                    writes[iid] = {"geometry": replace(obj.values["geometry"], relation_json=encoded)}
                    fences.append(self._fence(obj, ("geometry",)))
            else:
                obj = self._instance(ws, action.instance, opened=True)
                targets = (obj.id,)
                if op == "open":
                    if 'placement' in values:
                        _fail('invalid_action', 'initial placement applies only to a new window')
                    replacement = self._resolved(action.source, ctx)
                    writes[obj.id] = {"source": replacement.source, "zoom": 1.0, "pan": (0.5, 0.5)}
                elif op == "zoom":
                    if len(set(values) - {"size"}) != 1 or "size" in values and values.get("fit") is not True:
                        _fail("invalid_action", "zoom needs one level/factor/fit; size is only allowed with fit")
                    if "fit" in values:
                        if values["fit"] is not True:
                            _fail("invalid_action", "fit must be true")
                        level = 1.0
                    else:
                        level = _number(values.get("level", values.get("factor")), "zoom", positive=True)
                        if "factor" in values:
                            level *= obj.values["zoom"]
                        if not math.isfinite(level) or level > 64:
                            _fail("invalid_action", "zoom exceeds the admitted 64x limit")
                    writes[obj.id] = {"zoom": level}
                    if values.get("fit") is True:
                        writes[obj.id]["pan"] = (0.5, 0.5)
                        if "size" in values:
                            size = _point(values["size"], "size")
                            if any(v <= 0 for v in size):
                                _fail("invalid_action", "size must be positive")
                            position = self._observed_position(obj) or obj.values['geometry'].position
                            writes[obj.id]["geometry"] = replace(obj.values["geometry"], position=position,
                                                                 size=size, relation_json=None)
                elif op == "pan":
                    center = _point(values.get("center"), "pan center")
                    if any(not 0 <= v <= 1 for v in center):
                        _fail("invalid_action", "pan center is normalized source coordinates")
                    writes[obj.id] = {"pan": center}
                elif op in ("move", "ungroup"):
                    members = [o for o in self._instances[ws].values() if o.values["open"] and
                               (o.id == obj.id or obj.values["group"] and o.values["group"] == obj.values["group"])]
                    targets = tuple(o.id for o in members)
                    if op == "move":
                        position = _point(values.get("position"), "position")
                        def actual(o):
                            return o.observed.rect[:2] if o.observed is not None and o.observed.rect else o.values["geometry"].position
                        origin = actual(obj)
                        delta = (position[0] - origin[0], position[1] - origin[1])
                    for member in members:
                        if op == "ungroup":
                            writes[member.id] = {"group": None}
                        else:
                            xy = actual(member)
                            writes[member.id] = {"geometry": replace(member.values["geometry"],
                                position=(xy[0] + delta[0], xy[1] + delta[1]), relation_json=None)}
                        fences.append(self._fence(member, (*writes[member.id], "group")))
                elif op == "opacity":
                    opacity = _number(values.get("value"), "opacity")
                    if not 0 <= opacity <= 1:
                        _fail("invalid_action", "opacity must be between zero and one")
                    writes[obj.id] = {"opacity": opacity}
                elif op in ("hide", "restore"):
                    writes[obj.id] = {"hidden": op == "hide"}
                elif op == "resize":
                    size = _point(values.get("size"), "size")
                    if any(v <= 0 for v in size):
                        _fail("invalid_action", "size must be positive")
                    geometry = obj.values["geometry"]
                    if geometry.position == (0.0, 0.0) and geometry.relation_json is None:
                        # Never placed by a move: the studio placed it itself (away from its object) and resizes
                        # it where it is, so its admitted place is where it was observed, not 0,0 (live 2026-10-11:
                        # every resize of a just-opened window was refused as a geometry mismatch).
                        geometry = replace(geometry, position=self._observed_position(obj) or geometry.position)
                    writes[obj.id] = {"geometry": replace(geometry, size=size)}
                elif op == "front":
                    writes[obj.id] = {"front": None}  # actual desired order allocated at commit
                elif op == "close":
                    writes[obj.id] = {"open": False}
                if op == "open" and "title" in values:
                    _text(values["title"], "title", 120)
                    writes[obj.id]["title"] = values["title"]
                fences.append(self._fence(obj, writes[obj.id]))
            self._capacity(ws, created, replacing=(action.instance, replacement.source) if replacement else None)
            token = "pc-" + str(uuid4())
            work = _Work(ctx, self._generations[ws], op, targets, writes, tuple(fences), created, replacement, parent)
            if op == "open" and "title" in values:
                work.writes.setdefault(targets[0], {})["title"] = values["title"]
            self._pending[token] = work
            return PreparedPresentation(token)
        except Exception:
            for lease in created.values():
                self._release(lease)
            self._release(replacement)
            raise

    def _write(self, obj, values, owner):
        before = {}
        for prop, value in values.items():
            before[prop] = (obj.source if prop == "source" else obj.title if prop == "title" else obj.values[prop],
                            obj.owners.get(prop), obj.versions.get(prop, 0))
            if prop == "source":
                obj.source = value
                obj.source_generation += 1
                obj.observed = None
            elif prop == "title":
                obj.title = value
            else:
                if prop == "front" and value is None:
                    self._front += 1
                    value = self._front
                obj.values[prop] = value
            obj.versions[prop] = obj.versions.get(prop, 0) + 1
            obj.owners[prop] = owner
        obj.revision += 1
        if values.get("open") is False:
            obj.lifetime = str(uuid4())
            obj.observed = None
            self._release(obj.lease)
            obj.lease = None
        elif values.get("open") is True:
            obj.lifetime = str(uuid4())
            obj.source_generation += 1
            obj.observed = None
        return before

    def commit(self, prepared):
        token = prepared.id
        if token in self._done:
            work = self._done[token]
            self._context(work.context)
            return work.effect
        if token in self._canceled:
            code, reason = self._canceled[token]
            _fail(code, reason, "ask again")
        work = self._pending.get(token)
        if work is None:
            _fail("preparation_expired", "preparation is no longer admitted", "ask again")
        try:
            self._guard(work)
            self._capacity(work.context.workspace_id, work.created, exclude=token,
                           replacing=(work.targets[0], work.replacement.source) if work.replacement else None,
                           release_replaced=True)
        except PresentationError as exc:
            self.cancel(prepared, reason=exc.reason, code=exc.code)
            raise
        # No await between guards and writes; caller's apply guard runs too.
        objects = self._instances[work.context.workspace_id]
        for iid, lease in work.created.items():
            obj = _Instance(iid, lease.source, lease=lease, staged=work.op == "split")
            obj.owners = {p: work.context.owner for p in obj.versions}
            objects[iid] = obj
        for iid, values in work.writes.items():
            obj = objects[iid]
            work.before[iid] = self._write(obj, values, work.context.owner)
            work.written[iid] = {p: obj.versions[p] for p in values}
            if "source" in values:
                self._release(obj.lease)
                obj.lease = work.replacement
        work.created_versions = {iid: dict(objects[iid].versions) for iid in work.created}
        # Resource leases have transferred to instances; retained receipts keep
        # descriptors, never closures that might keep released capture bytes alive.
        work.created = {iid: ResolvedSource(lease.source) for iid, lease in work.created.items()}
        work.replacement = None
        expected = tuple(CommittedTarget(iid, objects[iid].source.key, objects[iid].source_generation,
                                         objects[iid].lifetime, objects[iid].revision,
                                         objects[iid].versions if iid in work.created else work.written[iid]) for iid in work.targets)
        work.effect = PresentationEffect(token, work.targets, parent_id=work.parent_id,
                                         context=work.context, app_run=self.app_run, expected=expected)
        self._pending.pop(token)
        self._done[token] = work
        while len(self._done) > self.max_receipts:
            self._done.popitem(last=False)
        self._prune_closed(work.context.workspace_id)
        return work.effect

    def cancel(self, prepared, *, reason, code="request_canceled"):
        work = self._pending.pop(prepared.id, None)
        if work:
            for lease in work.created.values():
                self._release(lease)
            self._release(work.replacement)
            self._canceled[prepared.id] = (code, str(reason)[:300])
            while len(self._canceled) > self.max_receipts:
                self._canceled.popitem(last=False)

    def _prune_closed(self, workspace):
        objects = self._instances[workspace]
        closed = [iid for iid, obj in objects.items() if not obj.values["open"]]
        for iid in closed[:-128]:
            del objects[iid]  # old IDs are never allocated again; old commits cannot reconstruct them
            self._observed_batches.pop(iid, None)

    def observe(self, ctx, observations):
        observations = tuple(observations)
        if len(observations) > 40:
            _fail("capacity_unavailable", "too many presentation observations")
        if not all(isinstance(obs, PresentationObservation) for obs in observations):
            _fail("invalid_action", "observation requires typed renderer facts")
        accepted, rejected = [], []
        self._observation_batch += 1
        for obs in observations:
            work = self._done.get(obs.command_id)
            try:
                if work is not None and work.compensation_of is not None and obs.phase == "closed":
                    self.compensation_guard(work.effect, ctx)
                else:
                    self._context(ctx)
            except PresentationError:
                rejected.append(obs.instance_id)
                continue
            obj = self._instances[ctx.workspace_id].get(obs.instance_id)
            expected = next((e for e in work.effect.expected if e.instance_id == obs.instance_id), None) if work else None
            closed = obs.phase == "closed"
            membership_receipt = work is not None and work.op in ("group", "ungroup")
            intentionally_hidden = (obj is not None and not closed and obs.visible is False
                                    and (obj.values["hidden"] or obj.values["opacity"] == 0
                                         or membership_receipt))
            valid = (obj is not None and work is not None and expected is not None
                     and (not obj.values["open"] if closed else obj.values["open"])
                     and work.context.workspace_id == ctx.workspace_id and work.context.client == ctx.client
                     and obs.instance_id in work.targets and obs.source_key == obj.source.key
                     and obs.source_generation == obj.source_generation and obs.lifetime_generation == obj.lifetime
                     and type(obs.revision) is int and obs.revision == obj.revision
                     and obs.source_generation == expected.source_generation and obs.lifetime_generation == expected.lifetime_generation
                     and all(obs.property_versions.get(p) == v for p, v in expected.property_versions.items())
                     and type(obs.source_generation) is int and type(obs.visible) is bool and type(obs.connected) is bool
                     and obs.phase in ("committed", "loading", "loaded", "drawn", "error", "closed")
                     and all(type(obs.property_versions.get(p)) is int and obs.property_versions[p] == v for p, v in obj.versions.items())
                     and (not obs.visible and not obs.connected if closed else
                          obs.connected and bool(obs.root_id) and (intentionally_hidden or membership_receipt or bool(obs.render_token))))
            if valid:
                if obs.absent:
                    valid = (obs.absent is True and closed and work.compensation_of is not None
                             and obs.rect == obs.content_rect == obs.natural_size == ()
                             and not obs.root_id and not obs.render_token
                             and type(obs.report_sequence) is int and obs.report_sequence > 0
                             and obs.transaction_id == obs.command_id)
                else:
                    valid = obs.absent is False
                    for rect in (obs.rect, obs.content_rect):
                        valid = valid and len(rect) == 4 and all(type(v) in (int, float) and math.isfinite(v) for v in rect)
                        if intentionally_hidden:
                            valid = valid and rect[2] >= 0 and rect[3] >= 0
                        elif not closed:
                            valid = valid and rect[2] > 0 and rect[3] > 0
                if not intentionally_hidden and obs.phase in ("loaded", "drawn") and obj.source.kind not in ("written", "lesson"):
                    valid = valid and len(obs.natural_size) == 2 and all(type(v) is int and v > 0 for v in obs.natural_size)
            if not valid:
                rejected.append(obs.instance_id)
            else:
                # Membership commits can arrive before a visible node's next
                # paint. Neither they nor tucked nodes establish drawn media.
                obj.observed = replace(obs, phase="committed") if intentionally_hidden or membership_receipt else obs
                self._observed_batches[obs.instance_id] = self._observation_batch
                accepted.append(obs.instance_id)
        return ObservationResult(tuple(accepted), tuple(rejected))

    def _effect_work(self, effect, ctx, *, check_request=True):
        self._context(ctx, check_request=check_request)
        work = self._done.get(effect.command_id)
        if (work is None or work.effect.targets != effect.targets or work.effect.parent_id != effect.parent_id
                or work.context.workspace_id != ctx.workspace_id or work.context.client != ctx.client):
            _fail("target_superseded", "effect is not owned by this originating workspace/client")
        return work

    def retire_group(self, effect, ctx):
        work = self._effect_work(effect, ctx)
        if work.op != "split":
            _fail("invalid_action", "only split has a parent to retire")
        if work.retired:
            return work.effect
        self._guard(work)
        children = [self._instance(ctx.workspace_id, iid, opened=True) for iid in effect.targets]
        if any(obj.observed is None or obj.observed.phase != "drawn" or not obj.observed.visible or not obj.observed.connected
               or obj.observed.command_id != effect.command_id or obj.observed.revision != obj.revision
               for obj in children):
            _fail("group_not_drawn", "every split child must be drawn and visible", "wait for the missing view")
        if len({obj.observed.root_id for obj in children}) != len(children):
            _fail("group_not_independent", "split children do not have independent window roots")
        if len({self._observed_batches.get(obj.id) for obj in children}) != 1:
            _fail("group_not_coherent", "split children were not observed in one coherent report")
        parent = self._instance(ctx.workspace_id, work.parent_id, opened=True)
        # Retirement has its own causal close command/fence for Screen to confirm.
        close = self.commit(self.admit(PresentationAction("close", instance=parent.id), ctx))
        close_work = self._done[close.command_id]
        work.before[parent.id] = close_work.before[parent.id]
        work.written[parent.id] = close_work.written[parent.id]
        for obj in children:
            obj.staged = False
        work.retired = True
        work.effect = replace(work.effect, retirement=close)
        return work.effect  # committed retirement; Screen must still confirm actual parent removal

    def _compensation_work(self, effect, ctx):
        """Original active occurrence and origin; only cancellation is exempt."""
        work = self._effect_work(effect, ctx, check_request=False)
        if work.context != ctx or effect != work.effect or effect.app_run != self.app_run:
            _fail("target_superseded", "compensation belongs to another original request/action")
        why = self.check_compensation(ctx)
        if why:
            _fail("request_canceled", str(why))
        if self._generations[ctx.workspace_id] != work.workspace_generation:
            _fail("target_superseded", "workspace reset since the original commit")
        return work

    def compensation_guard(self, effect, ctx):
        """Recheck registered conditional closes after every observation await."""
        work = self._compensation_work(effect, ctx)
        original = self._done.get(work.compensation_of)
        if (work.op != "close" or original is None or original.op != "split" or original.retired
                or original.context != ctx or original.effect.app_run != self.app_run
                or original.workspace_generation != work.workspace_generation
                or any(iid not in original.created_versions for iid in work.targets)):
            _fail("target_superseded", "the original provisional split receipt is unavailable")
        for target in effect.expected:
            obj = self._instance(ctx.workspace_id, target.instance_id)
            if (obj.values["open"] or not obj.staged
                    or obj.source != original.created[target.instance_id].source
                    or obj.source.key != target.source_key
                    or obj.source_generation != target.source_generation
                    or obj.lifetime != target.lifetime_generation or obj.revision != target.revision
                    or obj.versions != target.property_versions
                    or set(obj.owners) != set(obj.versions)
                    or any(owner != ctx.owner for owner in obj.owners.values())):
                _fail("target_superseded", "the compensated source, lifetime or ownership changed")

    def rollback(self, effect, ctx, *, native_child=None):
        # Cancellation stops forward work, not its conditional compensation.
        # Client/workspace ownership still applies; a replacement client cannot undo it.
        work = (self._compensation_work(effect, ctx) if native_child is not None
                else self._effect_work(effect, ctx, check_request=False))
        if native_child is not None and (work.op != "split" or work.retired
                                         or native_child not in work.created_versions):
            _fail("invalid_action", "native compensation closes only an original provisional split child")
        objects = self._instances[ctx.workspace_id]
        restored, preserved, errors, closes = [], [], [], []
        if self._generations[ctx.workspace_id] != work.workspace_generation:
            return RollbackResult((), work.targets, (("workspace", "target_superseded"),))
        # These are the resulting commit fences, not admission's earlier source.
        # A split's parent belongs to its separate causal retirement close receipt.
        committed = {target.instance_id: target
                     for receipt in (work.effect, work.effect.retirement) if receipt is not None
                     for target in receipt.expected}

        def same_committed_target(obj, iid):
            fence = committed.get(iid)
            return (fence is not None and obj.source.key == fence.source_key
                    and obj.source_generation == fence.source_generation
                    and obj.lifetime == fence.lifetime_generation)

        for iid, initial in work.created_versions.items():
            if native_child is not None and iid != native_child:
                continue
            obj = objects.get(iid)
            if obj is None or not obj.values["open"]:
                if native_child is not None:
                    preserved.append(iid)
                continue
            if (not same_committed_target(obj, iid)
                    or (native_child is not None and (obj.source != work.created[iid].source
                        or obj.revision != committed[iid].revision or not obj.staged))):
                preserved.append(iid)
                errors.append((iid, "target_superseded"))
            elif (obj.versions != initial or any(owner != work.context.owner for owner in obj.owners.values())
                  or (native_child is not None and set(obj.owners) != set(initial))):
                preserved.append(iid)
            else:
                if native_child is None:
                    self._write(obj, {"open": False}, "showing:restore")
                else:
                    # Register actual conditional undo in the same bounded work
                    # lifecycle. Never reconstruct a receipt displaced by eviction.
                    overflow = max(0, len(self._done) + 1 - self.max_receipts)
                    if effect.command_id in tuple(self._done)[:overflow]:
                        _fail("capacity_unavailable", "original split receipt cannot survive the close receipt")
                    token = "pc-" + str(uuid4())
                    close = _Work(ctx, work.workspace_generation, "close", (iid,),
                                  {iid: {"open": False}}, (self._fence(obj, obj.versions),),
                                  compensation_of=effect.command_id)
                    close.before[iid] = self._write(obj, {"open": False}, ctx.owner)
                    close.written[iid] = {"open": obj.versions["open"]}
                    target = CommittedTarget(iid, obj.source.key, obj.source_generation,
                                             obj.lifetime, obj.revision, obj.versions)
                    close.effect = PresentationEffect(token, (iid,), context=ctx,
                                                       app_run=self.app_run, expected=(target,))
                    self._done[token] = close
                    while len(self._done) > self.max_receipts:
                        self._done.popitem(last=False)
                    closes.append(close.effect)
                restored.append(iid)
        if native_child is not None:
            self._prune_closed(ctx.workspace_id)
            return RollbackResult(tuple(restored), tuple(preserved), tuple(errors), tuple(closes))
        for iid, before in work.before.items():
            if iid in work.created:
                continue
            obj = objects.get(iid)
            if obj is None:
                preserved.append(iid)
                continue
            if not same_committed_target(obj, iid):
                preserved.append(iid)
                errors.append((iid, "target_superseded"))
                continue
            eligible = {p for p in before if obj.versions.get(p) == work.written[iid][p] and obj.owners.get(p) == work.context.owner}
            undo = {p: before[p][0] for p in eligible}
            lease = None
            if "source" in before:
                # Source/view restoration is source-bound: do not swap back under
                # a user's newer pan/zoom on the replacement image.
                related = {p for p in before if p in ("source", "zoom", "pan", "title")}
                if not related <= eligible:
                    for prop in related:
                        undo.pop(prop, None)
            reopening = not obj.values["open"] and undo.get("open") is True
            if not obj.values["open"] and not reopening:
                preserved.append(iid)
                continue
            if "source" in undo or reopening:
                requested = undo.get("source", obj.source)
                try:
                    lease = self._resolved(requested, ctx)
                    self._capacity(ctx.workspace_id, {iid: lease} if reopening else {},
                                   replacing=None if reopening else (iid, lease.source), release_replaced=True)
                except PresentationError as exc:
                    self._release(lease)
                    errors.append((iid, exc.code))
                    for prop in ("source", "zoom", "pan", "title", "open"):
                        undo.pop(prop, None)
            if undo:
                if "source" in undo or undo.get("open") is True:
                    self._release(obj.lease)
                    obj.lease = lease
                    if "source" in undo:
                        undo["source"] = lease.source
                self._write(obj, undo, "showing:restore")
                for prop in undo:
                    obj.owners[prop] = before[prop][1] or "system:restore"
                restored.append(iid)
            if len(undo) != len(before):
                preserved.append(iid)
        self._prune_closed(ctx.workspace_id)
        return RollbackResult(tuple(dict.fromkeys(restored)), tuple(dict.fromkeys(preserved)), tuple(errors))

    def snapshot(self, workspace):
        def saved_descriptor(ref):
            wire = ref.to_wire()
            wire.pop("locator", None)  # renewable transport is never persisted identity
            if "components" in wire:
                wire["components"] = [saved_descriptor(child) for child in ref.components]
            return wire

        items = []
        for obj in self._instances.get(workspace, {}).values():
            if not obj.values["open"]:
                continue
            source = saved_descriptor(obj.source)
            # A pending/failed confirmation must not resurrect geometry sampled
            # before a newer manual Fit, move or resize was committed.
            current_geometry = (obj.observed is not None and obj.observed.visible
                                and obj.observed.property_versions.get('geometry') == obj.versions.get('geometry'))
            items.append({"id": obj.id, "source": source, "title": obj.title,
                          "view": {"zoom": obj.values["zoom"], "pan": list(obj.values["pan"]),
                                   "opacity": obj.values["opacity"], "hidden": obj.values["hidden"], "group": obj.values["group"]},
                          "geometry": {"position": list(obj.observed.rect[:2]) if current_geometry else list(obj.values["geometry"].position),
                                       "size": list(obj.observed.rect[2:]) if current_geometry else list(obj.values["geometry"].size) if obj.values["geometry"].size else None},
                          "owners": dict(obj.owners)})
        return {"version": 1, "workspaceId": workspace, "items": items}

    def restore_snapshot(self, snapshot, ctx, *, reconnect=False):
        self._context(ctx)
        _json(snapshot)
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1 or snapshot.get("workspaceId") != ctx.workspace_id:
            _fail("invalid_snapshot", "workspace preference has an unsupported identity/version")
        items = snapshot.get("items")
        if not isinstance(items, list) or len(items) > self.max_windows or (self._instances[ctx.workspace_id] and not reconnect):
            _fail("invalid_snapshot", "restore requires an empty workspace and bounded items")
        created = {}
        try:
            for item in items:
                if not isinstance(item, dict):
                    _fail("invalid_snapshot", "saved window must be an object")
                iid = item["id"]
                if not isinstance(iid, str):
                    _fail("invalid_snapshot", "saved instance ID must be a UUID string")
                UUID(iid.removeprefix("w-"))
                if not iid.startswith("w-") or iid in created:
                    _fail("invalid_snapshot", "saved instance IDs must be distinct UUIDs")
                ref = SourceRef.from_wire(item["source"])
                if ref.kind == "live" and ref.identity["run"] != self.app_run:
                    lease = ResolvedSource(ref.with_availability("earlier-run"))
                else:
                    lease = self._resolved(ref, ctx)
                created[iid] = lease
            # Reconnect atomically replaces these instances; it does not open a
            # second copy of every retained window against the capacity limit.
            self._capacity(ctx.workspace_id, created,
                           retiring=tuple(self._instances[ctx.workspace_id]) if reconnect else ())
            restored = OrderedDict()
            for item in items:
                iid, view, geo = item["id"], item["view"], item["geometry"]
                if not isinstance(view, dict) or not isinstance(geo, dict):
                    _fail("invalid_snapshot", "saved view and geometry must be objects")
                zoom = _number(view["zoom"], "saved zoom", positive=True)
                pan = _point(view["pan"], "saved pan")
                if zoom > 64 or any(not 0 <= p <= 1 for p in pan):
                    _fail("invalid_snapshot", "saved view exceeds supported bounds")
                size = _point(geo["size"], "saved size") if geo.get("size") is not None else None
                if size and any(n <= 0 for n in size):
                    _fail("invalid_snapshot", "saved size must be positive")
                title, owners = item.get("title", ""), item.get("owners", {})
                if not isinstance(title, str) or len(title) > 120 or not isinstance(owners, dict):
                    _fail("invalid_snapshot", "saved title/owners exceed their contract")
                obj = _Instance(iid, created[iid].source, lease=created[iid], title=title)
                obj.values.update(zoom=zoom, pan=pan, geometry=GeometryIntent(_point(geo["position"], "saved position"), size))
                opacity = _number(view.get("opacity", 1), "saved opacity")
                if not 0 <= opacity <= 1 or type(view.get("hidden", False)) is not bool:
                    _fail("invalid_snapshot", "saved visibility or opacity is invalid")
                group = view.get("group")
                if group is not None:
                    _text(group, "saved group")
                obj.values.update(opacity=opacity, hidden=view.get("hidden", False), group=group)
                obj.owners = {p: "user" if owners.get(p) == "user" else "system:restore" for p in obj.versions}
                restored[iid] = obj
            old = self._instances[ctx.workspace_id]
            self._instances[ctx.workspace_id] = restored
            for obj in old.values():
                self._release(obj.lease)
        except Exception as exc:
            for lease in created.values():
                self._release(lease)
            if isinstance(exc, (KeyError, TypeError, ValueError, AttributeError)) and not isinstance(exc, PresentationError):
                _fail("invalid_snapshot", "saved window is malformed")
            raise

    def reconnect_effect(self, ctx, iid):
        """Renew delivery ownership after an accepted reconnect, preserving view/source identity."""
        self._context(ctx)
        obj = self._instance(ctx.workspace_id, iid, opened=True)
        self._write(obj, {"source": obj.source}, ctx.owner)
        target = CommittedTarget(obj.id, obj.source.key, obj.source_generation, obj.lifetime,
                                 obj.revision, {"source": obj.versions["source"]})
        return PresentationEffect(str(uuid4()), (iid,), context=ctx, app_run=self.app_run,
                                  expected=(target,))

    def reset(self, workspace):
        """Adapter calls inside apply after day/clear/cancel policy; no peer workspace reset."""
        if workspace not in self._instances:
            return
        self._generations[workspace] += 1
        for token, work in tuple(self._pending.items()):
            if work.context.workspace_id == workspace:
                self.cancel(PreparedPresentation(token), reason="workspace reset")
        for obj in self._instances[workspace].values():
            if obj.values["open"]:
                self._write(obj, {"open": False}, "system:reset")
        self._prune_closed(workspace)

    def reset_all(self):
        """App day/stop cleanup releases every owned window and preparation."""
        for workspace in tuple(self._instances):
            self.reset(workspace)
