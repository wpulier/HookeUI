"""The Data reader, as the Assistant opens things in it (ticket 66, D6; docs/architecture/on-screen.md).

A person opens anything in Data on the right of the Tab they are on, in one reader (apps/studio/src/data-reader.ts).
`present` open of a saved item does the same for the Assistant: the studio is sent a `reader` message and opens the
item with the code a Data row's click runs (`openInReader`). It is done only once that studio's screen report shows the
reader with that item: its `document:reader-side` surface (a reader Tab's `document:reader-tab`, a researcher's Tab's
`document:reader-research`), open and laid out,
showing that exact day, id and version, its content drawn (ADR 0006: done means the screen shows it). Closing is the
same in reverse: done once no reader shows it.

Only a studio with the reader is sent one. The studio's report says so by the Project's Tab strip (`workspace-tabs`,
the surface the tabs tool pins, workspace_tabs.py): the Hooke workspace, where the reader is. A studio without it (an
older one, a test's fixture) is shown things as before (screen.WITHOUT_READER).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .brain.registry import ToolFailed
from .screen import REPORT_NEXT
from .workspace_observation import ObservationError

READER_SURFACES = ("document:reader-side", "document:reader-tab", "document:reader-research")  # data-reader.ts roots, as the report names them
READER_WAIT_S = 8.0  # the item shown and its content drawn (a write-up reads its research session first)
ASK_EVERY_S = 0.5  # a studio in the background reports only when asked (main.ts screen-ask)
_PIN_FIELDS = ("id", "rootId", "kind", "lifetimeGeneration", "sourceGeneration")


@dataclass
class ReaderStudio:
    """The studio an item opens in: its socket, the pin of its Tab strip (the workspace opening it was asked in), the
    Project and the Tab it was on, and how its current report is read."""
    client: Any
    pin: dict
    project: str
    tab: str
    read: Callable[[], dict | None]


def _strip(observation, session: str) -> dict | None:
    item = next((row for row in observation.wire()["items"] if row["id"] == "workspace-tabs"), None)
    workspace = ((item or {}).get("view") or {}).get("legacy", {}).get("workspace") or {}
    if item is None or not item.get("open") or not item.get("connected") or workspace.get("project") != session:
        return None
    return item


def reader_studio(screen, ctx, session: str) -> ReaderStudio | None:
    """The studio a request opens things in, when it has the Data reader: the requesting studio, by its request's pin
    (the Tab strip as it was at Send), else, for a tool run no studio sent (the sim's `tool`), the studio whose report
    counts, as present's other changes address it. None for a studio without the reader."""
    from .screen import Screen

    if not isinstance(screen, Screen):
        return None
    if ctx is not None and (getattr(ctx, "client", None) is not None or getattr(ctx, "request_snapshot", None) is not None):
        client, snapshot = ctx.client, ctx.request_snapshot
        if client is None or snapshot is None:
            return None
        observation = snapshot.observation
        admission = getattr(ctx, "admission", None)
        request_id = admission.request_id if admission is not None else ctx.request_id

        def read():
            return screen.request_report(snapshot, client, request_id=request_id)
    else:
        client = screen.current_accepted()
        observation = screen.observation(client) if client is not None else None
        if observation is None:
            return None

        def read():
            return screen.completion_report(client)
    item = _strip(observation, session)
    if item is None:
        return None
    workspace = item["view"]["legacy"]["workspace"]
    return ReaderStudio(client, {"client": observation.client.wire(), **{key: item[key] for key in _PIN_FIELDS}},
                        workspace["project"], workspace["tab"], read)


def ref_of(entry) -> dict:
    """An index entry's exact identity, as the reader reports what it shows (document.resource)."""
    return {"day": entry.day, "id": entry.id, "version": entry.version}


def shown(report: dict | None, ref: dict) -> dict | None:
    """The reader surface showing `ref` in this report (open and laid out: shown, or shown in a studio in the
    background, which paints nothing), else None."""
    from .screen import laid_out

    for item in (report or {}).get("items") or []:
        if item.get("id") not in READER_SURFACES or not item.get("open") or not (item.get("visible") or laid_out(item)):
            continue
        # Not named `resource`: test_capture_guard reads `<…source>.get(` as a frame lookup.
        doc = (item.get("document") or {}).get("resource") or {}
        if (item.get("shows") == ref["id"] and doc.get("id") == ref["id"] and doc.get("day") == ref["day"]
                and doc.get("version") == ref["version"]):
            return item
    return None


def _current(studio: ReaderStudio, guard: Callable[[], None]) -> dict:
    """The studio's report now, after the caller's own checks; refused once its Project's workspace was opened again
    (another Project, a reload): what was asked belongs to the opening it was asked in."""
    guard()
    try:
        report = studio.read()
    except ObservationError as exc:
        raise ToolFailed(str(exc), next=REPORT_NEXT) from None
    if report is None:
        raise ToolFailed("the studio has not reported what it shows", next=REPORT_NEXT)
    strip = next((item for item in report.get("items") or [] if item.get("id") == "workspace-tabs"), None)
    if strip is None or any(strip.get(key) != studio.pin[key] for key in _PIN_FIELDS if key != "client"):
        raise ToolFailed("the Project's workspace was opened again while it was being shown",
                         next="ask again in the Project open now")
    return report


async def _until(studio: ReaderStudio, done: Callable[[dict], Any], guard: Callable[[], None], wait: float):
    """Polls the studio's report until `done(report)` is truthy (its value is returned) or `wait` runs out (None),
    asking a studio in the background to report."""
    loop = asyncio.get_running_loop()
    deadline, asked = loop.time() + wait, loop.time()
    while True:
        report = _current(studio, guard)
        value = done(report)
        if value or loop.time() >= deadline:
            return value or None, report
        if loop.time() - asked >= ASK_EVERY_S:
            studio.client.offer(json.dumps({"type": "screen-ask"}))
            asked = loop.time()
        await asyncio.sleep(0.05)


def _message(studio: ReaderStudio, verb: str, ref: dict, *, title: str | None = None, entry: dict | None = None) -> str:
    """The studio's `reader` message (protocol.ts): the item by its exact identity, for the workspace opening it was
    asked in (the studio checks the pin, as it does for the tabs tool)."""
    item = {"resource": ref, **({"title": title} if title is not None else {})}
    return json.dumps({"type": "reader", "do": verb, "id": uuid.uuid4().hex, "session": studio.project,
                       "workspacePin": studio.pin, "item": item, **({"entry": entry} if entry is not None else {})},
                      ensure_ascii=False, default=str)


async def open_in_reader(studio: ReaderStudio, entry, guard: Callable[[], None]) -> tuple[str, dict, bool | None]:
    """Opens `entry` in the reader on the Tab the studio is on and waits for its report to show it there, its content
    drawn. Returns how it stands ('rendered', or 'loading' when its content had not drawn in time), the reader's item
    and whether the studio was in view; refuses when the studio never showed it there or its content could not be
    drawn."""
    ref = ref_of(entry)
    # The entry as Data lists it (its wire), so a studio whose Data has not caught up yet shows it at once.
    studio.client.offer(_message(studio, "open", ref, title=entry.title, entry=entry.wire()))

    def drawn(report):
        item = shown(report, ref)
        return item if item is not None and (item.get("document") or {}).get("state") in ("rendered", "failed") else None

    item, report = await _until(studio, drawn, guard, READER_WAIT_S)
    item = item or shown(report, ref)
    if item is None:
        raise ToolFailed(f"{entry.title} is not open in the reader: the studio has not shown it there",
                         next="look at the studio, or ask again")
    state = (item.get("document") or {}).get("state")
    if state == "failed":
        raise ToolFailed(f"{entry.title} is in the reader, but its content could not be drawn there",
                         next="press Try again in the reader, or ask to read it instead")
    return ("rendered" if state == "rendered" else "loading"), item, report.get("visible")


async def close_in_reader(studio: ReaderStudio, item: dict, guard: Callable[[], None]) -> None:
    """Closes the reader showing `item` (a reader surface of the studio's report) and waits until no reader shows it.
    A reader Tab is its Tab: it closes with the Tab."""
    if item.get("id") == "document:reader-tab":
        raise ToolFailed("a reader Tab is the Tab itself: it closes with its Tab, and stays in Data",
                         next="close that Tab (tabs close)")
    if item.get("id") == "document:reader-research":
        raise ToolFailed("a researcher's reader is part of its Tab: it shows what that researcher made, and stays in Data",
                         next="show something else there, or go to another Tab")
    resource = dict((item.get("document") or {}).get("resource") or {})
    ref = {"day": resource.get("day"), "id": resource.get("id"), "version": resource.get("version")}
    studio.client.offer(_message(studio, "close", ref))
    gone, _report = await _until(studio, lambda report: shown(report, ref) is None, guard, READER_WAIT_S / 2)
    if not gone:
        raise ToolFailed("the reader is not closed: the studio still shows it", next="close it with its ×")


def reader_showing(report: dict | None, entry_id: str) -> dict | None:
    """The reader surface of a report showing the index entry `entry_id` (any version), else None."""
    from .screen import laid_out

    for item in (report or {}).get("items") or []:
        if (item.get("id") in READER_SURFACES and item.get("open") and (item.get("visible") or laid_out(item))
                and item.get("shows") == entry_id):
            return item
    return None


def words(entry, state: str, visible: bool | None) -> str:
    """What opening it did, as the screen report showed it. Its title is the app's own words (a researcher's work is
    titled "Researcher 2's table 1"; what the researcher called it stays on the wire, research_entries.py)."""
    from .untrusted import inline

    said = f"Opened {inline(entry.title, 120)} ({entry.id}, {entry.day}) in the reader on the user's current Tab"
    if state == "loading":
        said += "; its content was still loading when the studio last reported"
    if visible is False:
        said += "; the studio is in the background, so it shows there once it is in view"
    return said + "; its saved record was read, not made again"


def work_entry(s, selected: dict) -> str | None:
    """The Data entry of a researcher's work as show_result selects it (presenter.items; research_entries.py): its report
    for its written result and its current presentation (a form of the report), its lesson, video or picture by the
    first version of its line at its current version. None for one Data does not list as itself (a reference photo, an
    older version)."""
    from . import versions
    from .research_entries import KIND_OF_OUTPUT, _form_output

    kind, current = selected.get("kind"), selected.get("current", True)
    outputs = [o for o in getattr(s, "outputs", None) or [] if isinstance(o, dict)]
    output = next((o for o in outputs if selected.get("id") and o.get("id") == selected["id"]), None)
    if kind == "summary" or current and output is not None and _form_output(output):
        return s.rid
    if kind in KIND_OF_OUTPUT and current and output is not None:
        return f"{s.rid}:{(versions.line(outputs, output['id']) or [output])[0]['id']}"
    return None
