"""What every producer makes, declared in one place (Amendment 3, point 1, of
docs/design/2026-10-06-captures-operations-evidence.md): each tool, studio command and background
producer says which kind of entry of the session's index (resources.py) it makes, or that what it makes
is temporary and why, or that it makes nothing (it changes the view, a setting or the conversation, or
reads what exists). `tests/test_resources.py` fails on a producer without a declaration, so a new tool
that makes data cannot merge without saying where its data goes.

A declaration is one of:
- an entry kind (KINDS), or several separated by commas ("named-outline, population");
- "temporary: <why>";
- "none: <what it does instead>".

Kept here, not on each Tool(...): one table to read, and the toolbox files stay as they are.
"""
from __future__ import annotations

# The kinds of entry in the index, with the plain name a summary uses for them (Amendment 4, item 1).
KINDS = {
    "population": "Cell outlines and matches",
    "member": "Members of a saved population",  # resolved by ID within its parent, not listed twice
    "result": "Measurements and pictures the app made",
    "research": "Researchers' work",
    "snapshot": "Snapshots",
    "named-outline": "Things named today",
    "taught-label": "Labels you taught",
    "calibration": "Scale",
    "dataset": "Datasets",
    "recording": "Screen recordings",
    "bookmark": "Bookmarks",
    "clip": "Footage held for researchers",
    "tracks": "Motion tracks",
    "individual": "Things followed over time",
    "outcome": "How each model has done here",
    "fact": "Facts settled today",
    "annotation": "Your notes on retained evidence",
    "analysis": "Accepted statistical analyses of saved tables",
    "graph": "Accepted graphs of saved tables",
    "dataset-table": "Dataset tables: measured tables pooled, grouped and edited, each version with its rows",
    "asset": "This session's sources: your recordings and pictures",
    "volume": "Volumes you added: whole-brain stacks with their axes, voxel sizes and orientation",
    "job": "Registrations of a volume to an atlas",
    "project": "Saved scientific projects and their revisions",
    # A researcher's work besides its report (the `research` entry), read from its research session (resources.py
    # `_research_work`; ticket 66, S4): ids `<rid>:<part>`.
    "lesson": "A researcher's lessons",
    "video": "A researcher's videos",
    "figure": "Pictures a researcher made",
    "finding-table": "Tables in a researcher's findings",
    "finding-chart": "Charts in a researcher's findings",
    "source": "Pages and web pictures a researcher cited",
}

# What each kind is, in Data's four sections (ticket 66, S1; the lab notebook's order): "acquired" (Pictures and
# recordings), "analysis" (Analyses), "writeup" (Write-ups), "source" (Sources), "other" (folded last), or None for a
# kind Data does not list. A result is decided by its media (RESULT_ROLES): a reference photo from the web is a source,
# every other result an analysis. `tests/test_resource_roles.py` fails on a kind with no role.
ROLES = {
    "asset": "acquired", "volume": "acquired", "recording": "acquired", "snapshot": "acquired", "clip": "acquired",
    "dataset": "acquired",
    "population": "analysis", "named-outline": "analysis", "individual": "analysis", "tracks": "analysis",
    "result": "analysis", "dataset-table": "analysis", "analysis": "analysis", "graph": "analysis",
    "finding-table": "analysis", "finding-chart": "analysis",
    "research": "writeup", "lesson": "writeup", "video": "writeup", "figure": "writeup", "annotation": "writeup",
    "source": "source",
    # None of the four fits these; a project stays here until its producer is read (an imported document is acquired,
    # a made one an analysis).
    "bookmark": "other", "calibration": "other", "taught-label": "other", "project": "other",
    "member": None, "outcome": None, "fact": None, "job": None,  # not listed in Data (as before)
}
RESULT_ROLES = {"reference": "source"}  # a result's media -> its role (media.REFERENCE: a photo from the web)

# Kinds the brain's `resources` tool and a researcher's listing leave out unless asked for by kind (or one read by
# its id): a researcher's sources, tables, charts and other work are many entries, and context earns its place
# (nothing new reaches a prompt by default). The studio's Data lists them.
ASKED_FOR_ONLY = frozenset({"lesson", "video", "figure", "finding-table", "finding-chart", "source"})


def role_of(kind: str, media: str | None = None) -> str | None:
    """Which of Data's sections an entry of `kind` (a result: with `media`) is in, or None when Data does not list it."""
    if kind == "result" and media in RESULT_ROLES:
        return RESULT_ROLES[media]
    return ROLES.get(kind)

# Products (CONTEXT.md "Product"): the analyses the session made as data to keep and reuse, the only entries Made in
# this session lists. The rest of the index (named outlines, outcomes, facts, taught labels, the scale, notes) stays
# readable by the brain and researchers. The user's decision of 2026-10-11: Made today is the data we made, not every
# action. Of 2026-10-12 (Q11, one home per thing; ADR 0022): the user's own recordings and pictures of the specimen
# are the session's sources, not products, so a Record dataset (its camera video), a snapshot and a clearer picture
# are listed in Sources and stay in the index. A screen recording shows the app, not the specimen, and a reference
# photo is the web's, not the user's: both stay products.
PRODUCT_KINDS = frozenset({"population", "tracks", "research", "recording", "bookmark", "clip", "individual", "job",
                           "dataset-table", "analysis", "graph"})
# A result entry is a product only when its producer made a picture to keep that is not one of the user's sources (a
# reference photo); counts, measurements, comparisons and where-is answers are answers. A clearer picture (enhance)
# and a snapshot's own "result" entry are sources (library.py), not products.
PRODUCT_RESULTS = frozenset({"reference_photo"})


# What a researcher makes: its report (`research`) and the rest of its work, read from its research session (S4).
RESEARCH_WORK = "research, lesson, video, figure, finding-table, finding-chart, source"


def is_product(kind: str, made_by: dict | None = None) -> bool:
    """Whether an index entry is a product, decided by its kind and, for a result, the tool that made it."""
    if kind == "result":
        return (made_by or {}).get("tool") in PRODUCT_RESULTS
    return kind in PRODUCT_KINDS


TOOLS = {
    "make_table": "dataset-table",
    "edit_table": "dataset-table",
    "analyze": "analysis",
    "make_graph": "graph",
    "read_analysis": "none: reads exact accepted numerical records without recomputing",
    "tabs": "none: changes saved Tab arrangements and placements, never their Data",
    # Scene
    "select": "none: selects an outline on screen",
    "clear": "none: takes outlines off the screen",
    "name_outline": "taught-label",
    "outline_cells": "population",
    "highlight": "named-outline",
    "find": "named-outline, population",  # by="cells" or like=: a population too
    "set_style": "none: changes how outlines are drawn",
    "present": "none: changes what is on screen (a presentation's state, not data)",
    "look_at_screen": "temporary: a picture of the screen for one look, a performance-log picture only",
    # Conversation and settings
    "name_specimen": "none: a fact of the conversation, in the day's journal",
    "new_thread": "none: starts a conversation",
    "switch_thread": "none: goes back to a conversation",
    "new_session": "none: starts a session (its journal is the session itself, listed from the journals)",
    "switch_session": "none: opens an earlier session",
    "show_measurements": "none: opens the measurements table of what was already measured (tables.py reads it)",
    "assets": "none: reads the library (All assets)",
    "add_asset": "none: puts an asset on stage; the session's journal keeps it as one of its sources",
    "register_volume": "job",  # a registration of a volume to the Allen mouse atlas (registrations.py)
    "recall": "none: reads today's conversations",
    "capabilities": "none: reads what the app can do",
    "models": "none: reads the Models page's rows (catalog_view.py)",
    "get_model": "none: starts a download into the models folder, said in Activity (downloads.py)",
    "turn_on": "none: starts the downloads and installs a job's option needs, each its own (capability_shelf.py)",
    "run_capability": "result",
    "describe_capability": "none: reads a capability's declaration (kinds.described)",
    "open_models": "none: opens the Models page in the studio",
    "use": "none: a choice for one request, on its ticket",
    "set_default": "none: changes a setting",
    "set_lead": "none: changes a preference",
    "set_level": "none: changes a preference",
    "suggest_next": "none: suggests what to do next",
    "resources": "none: reads this index",
    "retrieve_page": "none: retrieves a web page and source link for the answer, without creating a researcher",
    "inspect_camera": "none: acquires original held camera pixels for the original answer; no index entry, saved artifact or visual effect",
    "annotate_resource": "annotation",
    # The magnifier
    "zoom": "none: changes the magnifier",
    "magnifier_picture": "none: enlarges an existing displayed picture",
    "magnifier_source": "none: selects an existing magnifier source",
    "hold": "none: holds the magnifier's view",
    "compare": "result",
    "contrast": "result",
    "compare_models": "result",
    "focus_check": "result",
    "show_in_magnifier": "none: shows a picture that exists",
    "reference_photo": "result",  # a reference photo from the web, kept with its source and licence (media.REFERENCE)
    "close_magnifier": "none: closes the magnifier",
    "enhance": "result",
    "snapshot": "snapshot, result",  # its file, and with native windows the result a window opens by its id
    # Measuring
    "count": "result",
    "query_population": "individual",  # it reads a population; with against and link, the linking model's links join individuals
    "correct_cells": "population",  # a new revision of one
    "group_cells": "population",  # column groups on one (looks, groups, light; your labels when a group is named)
    "describe_groups": "population",  # a names column group on one (MedSigLIP's, when names are asked for), by an execution
    "follow_cells": "individual, named-outline",  # each followed thing, outlined from its own mask
    "individuals": "none: reads where followed things are and whether they changed",
    "same_one": "individual",  # the user's link joins two into one (undo: a new event naming it)
    "outcomes": "none: reads what happened (executions, corrections, votes)",
    "measure": "result",
    "retain_population_labels": "project",  # retained corrections become accepted native inputs
    "compare_retained_methods": "project",  # accepted method evidence and rationale; no producer rerun
    "measure_saved_objects": "project",  # accepted native labels become a typed table in the same Project
    "set_objective": "calibration",
    "calibrate_scale": "calibration",
    "dissect": "result",
    # Motion, time, place, memory
    "track_motion": "tracks",  # what it measured (or a followed outline's trail, `of`), kept as evidence (M3.2)
    "what_happened": "result",
    "watch": "result",  # when it fires (app.py), not when it starts
    "stop_watching": "none: stops a watch",
    "watching": "none: reads the watches",
    "where_is": "result",
    "show_map": "none: opens the slide map",
    "bookmark": "bookmark",
    "way_to_bookmark": "result",  # a measurement of where it is from the view now, as where_is (review note)
    "seen_before": "none: reads what was named today",
    # Researchers and their results
    "schedule_acquisition": "clip",
    "start_researcher": RESEARCH_WORK,
    "researcher_followup": RESEARCH_WORK,  # a new findings revision, and what it cites
    "revise_work": RESEARCH_WORK,  # a new version of a lesson, a video, a picture or the presentation
    "research_status": "none: reads the researchers",
    "summarize_research": "none: reads the researchers' work",
    "cancel_researcher": "none: stops a researcher",
    "researcher_note": "clip",  # a note to a working researcher; with hold_seconds, the footage it asked for (held.py)
    "set_research_cap": "none: changes a preference",
    "propose_correction": "temporary: a proposal until the user accepts it; accepting relabels the outline",
    "show_result": "none: opens a result",
    "read_result": "none: reads a result",
    "result_step": "none: goes through a lesson",
    "mark_step": "none: a quiz outcome, in the day's journal",
    "close_result": "none: closes a result",
    "show_me": "none: starts or stops showing a finding (its beats are present's)",
}

# Tools whose result entry is made later, not from their run (a watch makes one when it fires).
LATER = {"watch"}

COMMANDS = {
    "workspace-tabs-result": "none: acknowledges the original Studio Tab command",
    "onboarding": "population, asset",
    "onboarding-models": "none: keeps the answer to the small-models offer (onboarding/models.json); downloads are model-download's",
    "ask": "none: runs the brain, whose tools declare their own",
    "tool": "none: runs one tool, which declares its own",
    "gpu-test": "none: holds a GPU worker as a model load does, or unloads a model (the sim harness)",
    "click": "named-outline",
    "cells": "population",
    "population-correct": "population",  # the shared correct_cells tool creates a retained revision
    "accept-correction": "named-outline",
    "dismiss-correction": "none: dismisses a proposal",
    "research-followup": RESEARCH_WORK,
    "research-note": "none: appends a steering note to the existing researcher inbox",
    "research-evidence": "clip",  # explicit response to a retained question; existing researcher_note owns the hold
    "research-presentation": "research",
    "research-open-capture": "none: opens the original retained frame without acquisition or analysis",
    "research-cancel": "none: stops a researcher",
    "dataset": "dataset",
    "dataset-rename": "dataset",
    "dataset-trash": "none: moves a dataset aside (the user's)",
    "dataset-reveal": "none: shows a dataset in the Finder",
    "sessions-reveal": "none: shows the folder every Project is saved in, in the Finder",
    "rig": "calibration",
    "scale": "calibration",
    "arena": "temporary: a blind trial's outlines, until its vote",
    "vote": "none: a vote, kept with the judgments (logs/kept)",
    "validate": "none: a verdict, kept with the judgments (logs/kept)",
    "capability-check": "none: runs each model on this Mac once and keeps its check in checks.jsonl (model_checks.py), "
                        "shown on the Capabilities page beside the verdicts",
    "model-download": "none: puts a model's checked files in the models folder (~/MicroscopeMind/catalog, downloads.py), "
                      "said in Activity",
    "model-reveal": "none: shows a model's folder in the Finder",
    "model-add": "none: writes your model's entry in ~/MicroscopeMind/catalog/mine/models (mine.py), said in Activity",
    "model-activate": "none: checks your model in a process of its own and turns it on if it passes (mine.py)",
    "model-deactivate": "none: turns your model off (mine.py)",
    "observe-again": "none: asks the outside world again (a licence's access, whether a key works: observed.py)",
    "observe-confirm": "none: keeps the user's word that they accepted a licence (observed.py)",
    "page-opened": "none: the studio says it shows a page the agent opened (open_models)",
    "model-download-cancel": "none: stops a download; what it fetched stays in its staging folder for next time",
    "model-install": "none: makes a model's own Python in the catalog's envs folder (envs.py); said in Activity",
    "model-prepare": "none: prepares candidate dependencies for private review, without operation activation or qualification",
    "model-install-cancel": "none: stops an install; the half-made environment is never used",
    "model-uninstall": "none: takes a model's environments away; its card says Install again",
    "fiji-add-site": "none: turns on a Fiji extension's update site in Fiji's next generation (fiji_generations.py)",
    "fiji-rollback": "none: makes the previous Fiji generation current; the newer one is kept",
    "volume-add": "volume",  # a whole-brain stack you added, kept with what its file states (registrations.py)
    "volume-describe": "none: what you say of a volume (voxel sizes, orientation, which stack), kept on it as yours",
    "job-start": "job",  # a registration of a volume to the Allen mouse atlas, its outputs kept by their hashes
    "job-cancel": "none: stops a registration; what it wrote is kept, marked, never a result",
    "job-again": "job",  # the same registration run again, a new job
    "catalog-update": "none: fetches the reviewed catalog's newest commit into the catalog's channels folder (channels.py)",
    "catalog-news-seen": "none: marks new capabilities as seen in this Mac's catalog news (catalog_news.py)",
    "catalog-news-get": "none: fetches the peeked commit of the reviewed catalog in full (channels.py), installs nothing",
    "capability-turn-on": "none: starts the downloads and installs a job's option needs, each its own (capability_shelf.py)",
    "capability-select": "none: explicitly selects a currently proven model for the originating session",
    "capability-turn-on-cancel": "none: stops carrying a Turn on through (capability_shelf.py)",
    "model-turn-on": "none: starts the download and install one entry needs, each its own (capability_shelf.py)",
    "model-publish-prepare": "none: prepares a review package under the catalog's contributions folder (publish.py)",
    "model-build": "research",
    "model-own-compare": "temporary: private retained comparison of the exact chosen input",
    "model-own-use": "none: accepts the exact compared installed entry as a choice",
    "model-draft-use": "none: moves a draft that ran into your own models (drafts.py)",
    "model-draft-community": "none: prepares a local community question; nothing is sent",
    "made-with-acquire": "none: imports a result's pinned entry; code still needs explicit trust and setup",
    "capability-remove": "none: removes a catalog capability from this Mac",
    "capability-try": "none: selects the offered capability's explicit Studio try action",
    "capability-try-voice": "none: selects the offered voice capability's explicit try action",
    "model-publish-submit": "none: sends a reviewed package as a pull request on the user's sign-off (publish.py)",
    "github-signin": "none: GitHub's device flow; its token is kept in the Keychain (github_signin.py)",
    "model-publish-status": "none: reads a sent capability's pull request state (publish_api.py)",
    "capability-run": "none: Try it on a new kind of job's card runs the run_capability tool (its result is the tool's)",
    "usage-cap": "none: sets the optional monthly cap on cloud models, kept in the usage ledger (api_usage)",
    "model-report": "none: prepares a report to read and copy; nothing is sent (report.py)",
    "model-version": "none: chooses which proven version of an entry runs, kept beside its environments (env_runner.choose)",
    "maintenance-policy": "none: records the entry's maintenance policy in the catalog's maintenance folder (maintenance/policy.py)",
    "maintenance-check": "none: looks at the entry's source; an update found is a catalog fact, not a session resource (maintenance/discover.py)",
    "maintenance-apply": "none: makes a proven generation active for new work, a decision beside its environments (maintenance/promote.py)",
    "maintenance-undo": "none: makes the generation before active again, a decision beside its environments (maintenance/promote.py)",
    "maintenance-stop": "none: ends the update attempt in progress; the active generation is untouched (maintenance/worker.py)",
    "maintenance-accept": "none: records your acceptance of what an update changes, a decision beside its environments (maintenance/review.py)",
    "maintenance-merge": "none: writes your version merged onto its base into your own entry folder, the previous files kept (maintenance/adapt.py)",
    "catalog-news-dismiss": "none: never offers that capability again as new (catalog_news.py)",
    "channel-add": "none: fetches a community channel's catalog at a pinned commit into the channels folder",
    "channel-trust": "none: allows (or no longer allows) a community channel's code; kept in channels.json",
    "channel-remove": "none: stops reading a channel; its fetched commits go, what was installed stays",
    "channel-update": "none: pins a community channel at its newest commit and fetches it into the channels folder",
    "scores": "none: reads the votes",
    "settings": "none: changes settings",
    "clear": "none: takes outlines off the screen",
    "remove": "none: takes an outline off the screen",
    "restore": "none: brings back an outline the user just removed, as it was",
    "select": "none: selects an outline",
    "focus": "none: notes what the user lingers on",
    "flag": "temporary: a moment flagged in the interaction log",
    "card-open": "temporary: logged, to measure whether descriptions are read",
    "reveal": "temporary: a description's reference pictures, cached for the day",
    "pin-note": "none: pins a note under a label",
    "magnifier": "none: opens or closes the magnifier",
    "presenter": "none: opens or closes a result",
    "showing": "none: stops a showing",
    "slidemap": "none: opens or closes the slide map",
    "thread": "none: starts or switches a conversation",
    "session": "none: starts, opens or names a session (its journal), or keeps working in it",
    "view": "none: the studio covers the live view to look back at a session, or shows it again (no looks meanwhile)",
    # A session's sources (library.py, sources.py; ADR 0022): kept in the session's journal and the library's file.
    "source": "asset",
    "source-remove": "none: takes an asset out of the session (its journal); the library keeps it",
    "source-restore": "none: puts a removed asset back in the session (its journal)",
    "asset-meta": "none: the user's fields of an asset, kept in the library (library.jsonl)",
    "action": "none: runs one of the agent's tools by the user's hand; that tool's own declaration says what it keeps",
    "action-cancel": "none: stops one running action of the user's (its run says it was stopped; nothing after the Stop is kept)",
    "asset-trash": "none: moves an asset to the trash (library.jsonl; a file the library holds to its trash folder)",
    "asset-restore": "none: takes an asset out of the trash (library.jsonl)",
    "debug": "none: shows debugging overlays",
    "timing": "temporary: the studio's timings, in the interaction log",
    "execution-recording": "none: switches the execution log",
    "operation-retry": "none: retries one failed registered tool; its producer retains the result",
    "screen": "none: the studio's report of what it shows",
    "workspace-hello": "none: accepts this Studio socket's workspace origin",
    "workspace-layout": "none: saves session-owned presentation arrangements and retained references in its journal",
    "session-note": "annotation",
    "workspace-profile": "none: supplies an opaque namespace for browser presentation preferences",
    "workspace-study-profile": "none: legacy review-profile alias for the browser preference namespace",
    "present": "none: the user's own change of what is on screen (the same verbs)",
    "screen-image": "temporary: a picture of the screen for one look_at_screen",
}

# Producers that run on their own (module: declaration).
BACKGROUND = {
    "sight": "temporary: the running look, until a request uses it",
    "masks": "temporary: automatic masks, until a request uses them (then a population)",
    "objtrack": "temporary: the follower's steps move outlines (a trail asked for is kept by track_motion)",
    "slidemap": "temporary: the slide map, rebuilt from the view and shown on request",
    "dust": "temporary: dust spots, measured again every 3 s",
    "clip": "temporary: the last minute of frames",
    "sweep": "temporary: the last focus sweep, for 2 minutes",
    "quality": "temporary: each frame's flags",
    "coach": "temporary: advice on the view while it lasts",
    "outline_check": "temporary: a check of an outline, in the interaction log",
    "enrich": "temporary: descriptions under labels, cached for the day",
    "seen": "named-outline",
    "watcher": "result",
    "datasets": "dataset",
    "recordings": "recording",
    "research": RESEARCH_WORK + ", population, result, tracks",  # researchers' work, and their hands' runs (research/hands.py), never on screen
    "operations": "outcome",  # each execution, logged (outcomes.py reads them with corrections and votes; K3)
    "day/memory": "fact",  # facts the user settled today, each with its source (day/memory.py)
    # What happened (activity.py): lines of the conversation in the day's journal (the "sessions" store), never an
    # index entry: Activity is an account of what was done, not data made to keep (CONTEXT.md "Product").
    "activity": "none: says what happened in Activity, filed as lines of the day's journal",
    "looplag": "temporary: a stall of the event loop with its stack, in the interaction log (loop-lag)",
    "loads": "none: models loaded before anyone waits for them, said in Activity while loading (running, then done) "
             "and measured in models/loads.json; unloaded to make room, said",
    "machine": "temporary: the computer's readings when one crosses the contract's lines, in the interaction log",
    "model_checks": "none: each model's last check (works, broken and why...), one line a check in checks.jsonl, a "
                    "performance log made again by Check now or make capability-check; its operations' runs are logged "
                    "with origin \"check\", never an outcome",
}


# Every store a save path of kind "asset" or "dataset" holds (paths.KINDS), and how the index reads it: an
# entry kind, or "none: why" (tests/test_resources.py checks every such path has one).
STORES = {
    "sessions": "none: the day's journal itself (conversations, notes), read by recall and the digest, and the user's "
                "preferences beside it (day/prefs.py)",
    "research": RESEARCH_WORK,  # each session's folder: its session.json, outputs and sources (resources._research_work)
    "snapshots": "snapshot",
    "evidence": "population, tracks",
    "results": "result",
    "taught": "taught-label",
    "seen": "named-outline",
    "bookmarks": "bookmark",
    "identity": "individual",
    "recordings": "recording",
    "datasets": "dataset",
    "library": "asset",  # the ids and the user's edits of their recordings and pictures (library.py)
    "jobs": "volume, job",  # volumes the user added and the registrations run on them (registrations.py)
    "models": "none: a fine-tuned model is made by `make finetune-cells` outside the app, never by a session; each version "
              "is option CELLS cellpose-sam-tuned, named in the capabilities list and in the outcomes; loads.json beside "
              "them is how long each model took to load on this Mac and how much memory it took (loads.py), read by "
              "the memory guard and to say how long a load usually takes",
}


def kinds_of(declaration: str | None) -> list[str]:
    """The entry kinds a declaration names ([] for "none: ..." and "temporary: ...")."""
    parts = [p.strip() for p in str(declaration or "").split(",")]
    return parts if parts and all(p in KINDS for p in parts) else []


def kind_of(declaration: str | None) -> str | None:
    """The (first) entry kind a declaration names, or None."""
    kinds = kinds_of(declaration)
    return kinds[0] if kinds else None


def problems_of(declaration: str | None, who: str) -> list[str]:
    if not declaration:
        return [f"{who} declares nothing it produces (resources.produces)"]
    if kinds_of(declaration):
        return []
    head, _, why = declaration.partition(":")
    if head in ("none", "temporary") and why.strip():
        return []
    return [f"{who}: {declaration!r} is no entry kind, and not 'none: ...' or 'temporary: ...' with a reason"]
