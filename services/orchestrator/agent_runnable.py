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
    "measure_saved_objects": "measures its saved objects",
    "compare_retained_methods": "compares methods on its saved data",
    "register_volume": "registers its own volume",
    "run_capability": "runs a catalog capability on its stage",
    "dissect": "dissects a thing on its stage",
    "enhance": "makes a clearer picture of its stage",
    "reference_photo": "finds a reference photo for its subject",
    "read_result": "reads a result",
    "read_analysis": "reads an accepted analysis",
    "resources": "reads its Project's index",
    "outcomes": "reads how models have done",
    "describe_capability": "reads a capability's declaration",
}

# Not runnable, with why. Every other tool of the Assistant is declared here.
NOT_RUNNABLE: dict[str, str] = {
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
    "get_model": "downloads models",
    "turn_on": "downloads and installs",
    "use": "chooses a model for one request; the door runs the researcher's choices",
    "set_default": "changes a setting",
    "set_lead": "changes a preference",
    "set_level": "changes a preference",
    "set_objective": "changes a setting",
    "set_research_cap": "changes the research spending cap",
    "set_style": "changes how outlines are drawn",
}


def runnable(tool_name: str) -> bool:
    return tool_name in RUNNABLE
