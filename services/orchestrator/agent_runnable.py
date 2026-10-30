"""Which of the Assistant's tools an outside agent may run through the door (ADR 0026), declared in one place, as
produces.py declares what each makes. Kept here, not on each Tool(...): one table to read.

The default is no. A tool is declared runnable only when, run in a Project the agent made, it makes scientific data
there or reads it, and it doesn't change settings, navigate between Projects, change what a window presents, start
researchers, touch conversations or the researcher's other Projects, or download. Where a tool runs (this Mac or a
cloud service) is checked apart, at run time, from its models (door/listing.py): through the door, only local work runs.

`tests/test_agent_door.py` fails on a tool declared in neither table, so a new tool is never offered to agents until
someone decides.
"""
from __future__ import annotations

# Runnable: makes scientific data in the agent's own Project, or reads it.
RUNNABLE: dict[str, str] = {
    "outline_cells": "outlines cells on its stage",
    "correct_cells": "revises outlines it made",
    "find": "finds and outlines things on its stage",
    "highlight": "outlines a thing on its stage",
    "count": "counts what is outlined",
    "measure": "measures what is outlined",
    "contrast": "measures contrast on its stage",
    "focus_check": "measures focus on its stage",
    "compare": "compares outlines it made",
    "compare_models": "compares models on its stage",
    "group_cells": "groups its outlined cells",
    "describe_groups": "describes its groups",
    "query_population": "reads members of its populations",
    "same_one": "matches things across its views",
    "follow_cells": "follows its cells over time",
    "track_motion": "tracks motion on its stage",
    "where_is": "says where something is in its view",
    "what_happened": "says what changed in its view",
    "way_to_bookmark": "says how to reach a bookmark of its own",
    "snapshot": "keeps a snapshot of its stage",
    "bookmark": "bookmarks a place in its view",
    "calibrate_scale": "calibrates its own picture's scale",
    "make_table": "makes a table from its results",
    "make_graph": "makes a graph from its tables",
    "analyze": "analyses its tables",
    "register_volume": "registers its own volume",
    "run_capability": "runs a catalog capability on its stage",
    "dissect": "dissects a thing on its stage",
    "enhance": "makes a clearer picture of its stage",
    "reference_photo": "finds a reference photo for its subject",
    "read_analysis": "reads an accepted analysis",
    "resources": "reads its Project's index",
    "describe_capability": "reads a capability's declaration",
}

# Not runnable, with why. Every other tool of the Assistant is declared here.
NOT_RUNNABLE: dict[str, str] = {
    "find_workflows": "use Project-scoped workflow discovery through the door",
    "workflow": "use Project-scoped workflow admission through a Study",
    "add_asset": "puts the researcher's library assets on stage; agents bring files with open",
    "assets": "reads the researcher's whole library",
    "annotate_resource": "writes notes on any retained evidence, the researcher's too",
    "edit_table": "edits dataset tables, which span Projects",
    "retain_population_labels": "changes saved labels",
    "name_outline": "teaches labels the researcher's Hooke keeps",
    "propose_correction": "proposes a change for the researcher to accept",
    "cancel_researcher": "stops a researcher",
    "start_researcher": "starts researchers, which spend the research allowance",
    "researcher_followup": "asks a researcher for more",
    "researcher_note": "writes to a researcher",
    "revise_work": "revises a researcher's work",
    "research_status": "reads the researchers",
    "summarize_research": "reads the researchers' work",
    "schedule_acquisition": "schedules camera work",
    "watch": "keeps watching the live view",
    "stop_watching": "stops a watch",
    "watching": "reads the watches",
    "individuals": "reads things followed over time across views",
    "inspect_camera": "reads the live camera",
    "look_at_screen": "reads the researcher's screen",
    "present": "changes what a window presents",
    "show_me": "presents a finding in a window",
    "show_map": "opens a page in a window",
    "show_measurements": "opens a page in a window",
    "show_result": "opens a result in a window",
    "open_models": "opens a page in a window",
    "show_in_magnifier": "changes the magnifier",
    "magnifier_picture": "changes the magnifier",
    "magnifier_source": "changes the magnifier",
    "zoom": "changes the magnifier",
    "hold": "changes the magnifier",
    "close_magnifier": "changes the magnifier",
    "close_result": "closes a result in a window",
    "select": "changes the selection on screen",
    "clear": "takes outlines off the screen",
    "tabs": "changes Tab arrangements",
    "new_project": "starts a Project (agents get theirs from open)",
    "new_session": "starts a Project (agents get theirs from open)",
    "switch_session": "opens another of the researcher's Projects",
    "new_thread": "starts a conversation",
    "switch_thread": "goes to another conversation",
    "recall": "reads the researcher's conversations",
    "seen_before": "reads what was named in the researcher's day",
    "name_specimen": "writes a fact into the conversation",
    "mark_step": "records a lesson's quiz outcome",
    "result_step": "goes through a lesson",
    "suggest_next": "suggests the researcher's next step",
    "retrieve_page": "fetches web pages",
    "capabilities": "the door's own listing says this",
    "models": "the Models page; setting models up is the researcher's choice",
    "models_in_use": "the app's own settings in words; the door's listing says what each capability runs",
    "get_model": "downloads models",
    "turn_on": "downloads and installs",
    "use": "chooses a model for one request; the door runs the researcher's choices",
    "set_default": "changes a setting",
    "set_lead": "changes a preference",
    "set_level": "changes a preference",
    "set_objective": "changes a setting",
    "set_research_cap": "changes the research spending cap",
    "set_style": "changes how outlines are drawn",
    "read_result": "can read researchers' work, which belongs to their Projects",
    "outcomes": "reads how models have done across all the researcher's work",
    "measure_saved_objects": "works on saved Project documents, which may be the researcher's",
    "compare_retained_methods": "works on saved Project documents, which may be the researcher's",
}


# Catalog capabilities (catalog:<key>, run by run_capability): only entries of the reviewed catalog are runnable for
# agents. A community channel's entry or the researcher's own (a manifest with a namespace) is not, whatever it does.
def catalog_runnable(manifest) -> bool:
    return manifest is not None and not getattr(manifest, "namespace", "")


# Arguments that would point an agent's run outside its own Project: a session other than its own, a conversation, a
# researcher.
OWN_SESSION_ARGUMENTS = ("session", "sid")
NEVER_ARGUMENTS = ("thread", "researcher")


def runnable(tool_name: str) -> bool:
    return tool_name in RUNNABLE

# Door-native resources use the same deliberate, default-deny declaration boundary.
# These are not Assistant stage tools: each has its own explicit Project contract.
DOOR_RESOURCES = {
    'project.create': 'creates a local-only retained Project without moving the stage',
    'project.list': 'lists only Projects this connection may read',
    'project.export.prepare': 'reads the byte estimate and unverified dependency scope of an authorized Project folder',
    'project.export': 'contributor explicitly exports an immutable folder snapshot to a new host-selected location; no authority or completeness is inferred',
    'project.storage': 'reads location and collection facts for an authorized Project without moving files or granting access',
    'project.dependencies': 'inspects declared historical evidence dependencies in an authorized Project; never reads external Projects or claims complete collection',
    'project.read': 'reads the exact authorized Project and retained corpus index',
    'project.state': 'reads the authorized Project as every reader sees it: data with exact references, researchers, Studies, the studio',
    'material.organize': 'organizes an exact artifact in an authorized Project; preserves its bytes, producing provenance and historical revisions',
    'project.import': 'retains one explicitly supplied local file in an authorized Project',
    'artifact.read': 'reads an exact authorized retained revision, with bounded source text',
    'study-question.read': 'reads retained questions and exact Study context within an authorized Project',
    'study-question.write': 'retains questions or an attributed answer from the selected Project contributor; never launches an agent',
    'study.read': 'reads questions, results, failures and original authors in an authorized Project',
    'study.write': 'creates or continues Studies and publishes attributed findings in an authorized Project',
    'workflow.read': 'discovers reusable workflow definitions and capability contracts',
    'workflow.write': 'authors a new reusable definition or admits work in an authorized Project',
}


# Owner storage actions accept local paths and cannot be admitted by connected
# agents. Importing historical authorship never establishes present-day authority.
OWNER_STORAGE_ACTIONS = {
    'project-open-folder': 'owner selects a local Project folder; no inherited agent authority',
    'project-copy-prepare': 'owner reviews a completed export before restoring a working copy',
    'project-copy-restore': 'owner restores an exported identity without registration or inherited access',
    'project-copy-status': 'owner observes the restoration operation in its Project',
    'project-copy-stop': 'owner requests stopping a restoration, preserving incomplete files',
    'project-copy-reveal': 'owner reveals only its verified restoration destination',
}


def door_resource(name: str) -> bool:
    return name in DOOR_RESOURCES
