"""The brain's tools. Each is defined once and works from chat and from voice.

`build(services)` is the one way to make the tool list: every family registers in a fixed
order (the order the model sees them), and a tool is there only when what it acts on exists
(no researchers, no researcher tools). The server, the tests and the benches all use it, so
the tool list the model sees is the same everywhere (ADR 0003). `catalogue()` is the full
list for describing it (the voice's prompt, the benches), with nothing behind it.

Tools refer to objects by id ("o12"), by "target N" (the Nth thing this answer outlined), by
label ("stoma"), or not at all (then: this answer's first target, else the selected object,
else the newest outline); researchers the way people do (research/desk.py).
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

from ..registry import Registry
from .common import describe, in_view, resolve_object, visible_object
from .control import log_research_control
from .display import add_display_tools
from .finding import add_finding_tools
from .magnifier import add_magnifier_tools
from .memory import add_memory_tools
from .motion import add_motion_tools
from .narration import add_narration_tools
from .research import add_research_tools, recent_request
from .scene import add_dissect_tool, add_lead_tool, add_scene_tools
from .seeing import add_seeing_tools
from .population import add_correction_tool, add_group_tools, add_population_tools
from .threads import add_capability_tool, add_thread_tools
from .choosing import add_choosing_tools
from .time import add_time_tools
from .view import add_view_tools
from .where import add_where_tools
from .identity import add_identity_tools
from .outcomes import add_outcome_tools
from .catalog import add_catalog_tools
from .kinds import add_kind_tools
from .present import add_present_tools
from .showing import add_showing_tools
from .resources import add_resource_tools
from .scale import add_scale_tools
from .library import add_library_tools
from .volumes import add_volume_tools
from .tables import add_table_tools
from .scientific import add_scientific_tools
from .datatable_tools import add_datatable_tools
from .tabs import add_tab_tools
from .web import add_web_tools
from .workflows import add_workflow_tools


@dataclass
class Services:
    """What the tools act on. Anything left None has no tools."""

    hub: Any
    agent: Any = None
    magnifier: Any = None  # magnifier.Magnifier
    dissector: Any = None  # dissect.Dissector
    research: Any = None  # research.ResearchManager
    desk: Any = None  # research.desk.Desk: which researcher a request means
    presenter: Any = None  # presenter.Presenter: a researcher's work beside the view
    day: Any = None  # day.SessionStore: conversations and preferences
    memory: Any = None  # day.memory.Memory: recall
    subjects: Any = None  # day.subject.SubjectTracker: the specimen the user names
    sight: Any = None  # sight.Sight: the running look (highlight seen things)
    narrator: Any = None  # brain.narrator.Narrator: suggestions at a pause
    board: Any = None  # board.Board: what is on screen now, and what "that" means on it
    prepared_sources: Any = None  # exact native delivery leases, installed after the one Index
    presentations: Any = None  # workspace-local UUID windows; one owner shared with App
    workspace_context: Callable | None = None  # accepted socket -> original action PresentationContext
    segment_all: Callable | None = None  # every match of an exemplar (default tools.segment_all: SAM 3.1 on fal)
    find_reference: Callable | None = None  # (subject, n[, search_terms=...]) -> reference.Found; omitted terms keep the legacy call
    find_video: Callable | None = None  # async (subject) -> video_search.FoundVideo (None: the web search, checked)
    scale: Any = None  # scale.Scale: the objective in use, so outlines have sizes
    outline_seen: Callable | None = None  # (ref, ctx) -> outline id, for a thing the look saw
    publish_settings: Callable | None = None  # tell every studio the settings changed
    # (changes, by) -> problems: settings changed from now on, as the studio's menus change them (commands.change_settings:
    # kept until changed, what a change needs started, published). None: the settings alone (tests, benches).
    change_settings: Callable | None = None
    history_log: Any = None  # Path of the interaction log (seen_before reads today's views from it)
    capabilities: Callable | None = None  # () -> the capability wire (capabilities.py): the agent reads what the user sees
    # () -> {"capabilities": rows}: the same rows, fresh, without the Models page (App.capability_status): awareness
    capability_status: Callable | None = None
    clip: Any = None  # clip.ClipBuffer: the last minute of the view (what_happened)
    watcher: Any = None  # watcher.Watcher: watching for something to happen (watch)
    sweep: Any = None  # sweep.SweepBuffer: the frames of the last turn of the focus knob (enhance how="stack")
    slidemap: Any = None  # slidemap.SlideMap: the slide map panel (where_is needs only the scene)
    snapshots: Any = None  # Path: where snapshots are saved (paths.Paths.snapshots; None: from the environment)
    bookmarks: Any = None  # bookmarks.Bookmarks: places on the slide the user asked to keep (M3)
    registrations: Any = None  # registrations.Registrations: volumes the user added and the registrations run on them
    taught: Any = None  # taught.TaughtLabels: the labels the user teaches (name_outline keeps examples)
    seen: Any = None  # seen.SeenToday: the outlines named today (seen_before lists the most alike)
    log: Callable | None = None  # the interaction log: every action on researchers' work is one record
    keep: Callable | None = None  # (path, kind) a file a tool saved (a snapshot): kept in a dataset being collected
    downloads: Any = None  # downloads.Downloads: the models folder's queue (get_model, on the user's yes)
    installs: Any = None  # envs.Installs: the environments the app makes (get_model, on the user's yes)
    open_page: Callable | None = None  # async (client, "#models/<id>") -> {"visible"} once the studio shows it, or None
    workspace_tabs: Callable | None = None  # original-context named Tab command, acknowledged by its Studio
    table_services: Callable | None = None  # real DataTables/derivatives resolved after App construction, never placeholders
    # async (asset id) -> the `source` command's reply: one of the user's assets on stage, the session's (library tools)
    put_on_stage: Callable | None = None
    observe: Callable | None = None  # async (model id) -> asks the outside world again about it (observed.py)
    turn_on: Callable | None = None  # (job id) -> one press of Turn on, carried through (capability_shelf.TurnOn.start)
    kinds: Callable | None = None  # () -> the new kinds of job on here (kinds.available): run_capability's choices


def build(services: Services) -> Registry:
    settings = getattr(services.hub, "settings", None)
    registry = Registry(menu=lambda: getattr(settings, "menu", "flat"))
    registry.log = getattr(getattr(services, "agent", None), "log", None)  # where unexpected failures are written
    from ... import activity

    registry.models = lambda run: activity.models_of(services.hub, run)  # what worked on each step, for Activity
    add_scene_tools(registry, services)
    add_thread_tools(registry, services)
    add_capability_tool(registry, services)
    add_catalog_tools(registry, services)
    add_kind_tools(registry, services)
    add_choosing_tools(registry, services)
    add_lead_tool(registry, services)
    add_magnifier_tools(registry, services)
    add_population_tools(registry, services)
    add_correction_tool(registry, services)
    add_group_tools(registry, services)
    add_dissect_tool(registry, services)
    add_research_tools(registry, services)
    add_narration_tools(registry, services)
    add_view_tools(registry, services)
    add_finding_tools(registry, services)
    add_scale_tools(registry, services)
    add_motion_tools(registry, services)
    add_where_tools(registry, services)
    add_identity_tools(registry, services)
    add_outcome_tools(registry, services)
    add_time_tools(registry, services)
    add_memory_tools(registry, services)
    add_resource_tools(registry, services)
    add_seeing_tools(registry, services)
    add_display_tools(registry, services)
    add_present_tools(registry, services)
    add_showing_tools(registry, services)
    add_library_tools(registry, services)
    add_volume_tools(registry, services)
    add_table_tools(registry, services)
    add_tab_tools(registry, services)
    add_web_tools(registry, services)
    add_scientific_tools(registry, services)
    add_workflow_tools(registry, services)
    add_datatable_tools(registry, services)
    if services.log is not None:
        log_research_control(registry, services.log)
    return registry


class _StandIn:
    """Stands in for every service when the tool list is only described: it has every
    attribute (so "what it acts on exists" holds for every tool) and does nothing."""

    def __getattr__(self, name: str):
        return self

    def __call__(self, *args, **kwargs):
        return self


def catalogue() -> Registry:
    """Every tool, for describing the tool list (the voice's prompt, benches); none can run."""
    stand_in = _StandIn()
    return build(Services(**{f.name: stand_in for f in fields(Services) if f.name not in ("log",)}))


__all__ = ["Services", "build", "catalogue", "describe", "in_view", "recent_request", "resolve_object", "visible_object"]


def refresh(registry: Registry, services: Services) -> list[str]:
    """The choices the tools offer, made again from the catalog (a model turned on or taken away while the app runs: an
    entry with its own adapter proven, one of yours turned on): each tool's arguments are built again (their lists of
    ids: names.choosable_ids; `use` says them in its description too), and only those that changed are replaced. Their handlers, and what they keep (an offer's
    id), stay the ones in use. Returns the tools whose choices changed (live 2026-10-13: Dark spots, installed while the
    app ran, was Ready on its card and "isn't listed among the selectable segmentation tools" to the agent)."""
    kept = getattr(services, "offer_download", None)
    try:
        fresh = build(services)
    finally:
        if kept is not None:
            services.offer_download = kept  # the live tools' offers, never the fresh build's
    changed = []
    for gone in ("run_capability", "describe_capability"):  # the last capability taken away
        if gone in registry.tools and gone not in fresh.tools:
            registry.tools.pop(gone)
            changed.append(gone)
    for name, tool in fresh.tools.items():
        live = registry.tools.get(name)
        if live is None and name in ("run_capability", "describe_capability"):  # the first capability turned on while it runs
            registry.add(tool)
            changed.append(name)
        elif live is not None and (live.params != tool.params or live.description != tool.description):
            live.params, live.description = tool.params, tool.description  # `use` says its choices as "id = name" too
            changed.append(name)
    return changed
