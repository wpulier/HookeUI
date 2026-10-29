"""The measurements table on screen, as the Assistant opens it (docs/architecture/on-screen.md, "One surface per thing").

A person's Open in Measurements (a Data row's, a masks window's Measurements) opens a table in a window on the Tab they
are on (`openStudyTable`, workspace-study-content.ts). `show_measurements` opens that same window: the studio is sent a
`table-window` message for the workspace opening and the Tab it names (apps/studio/src/agent-table.ts), opens it with
that code, and answers with the window's placement (`table-window-result`). It is done only once the studio's screen
report shows that window in that Project and Tab, with that exact table and revision, its rows drawn, shown and in
front (no window over it) (ADR 0006: done means the screen shows it; a `#measurements` page and its `page-opened`
answer were taken as done while the Tab held no table, voice log of 2026-10-28).

With a Tab named, it is one action: the Tab is found by its id or title, or made, and put in front, through the tabs
broker the person's Tab gestures use (workspace_tabs.py); then the window is opened there and fitted with its own Expand.

Saved, computed, displayed, in front and accepted are separate facts (`facts`). The table is saved in Data before
anything is shown and stays there whatever the studio does, so an answer that it is not shown says where it is saved.
A studio in the background is waited for as present's changes are (the time out of view does not count, at most
BACKGROUND_WAIT_S), and then said to be placed and not shown yet, never failed. A studio without the Project workspace
(no Tab strip in its report: an older studio, a test's fixture) is shown the Measurements page as before (tables.py).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

from .brain.registry import ToolFailed
from .brain.toolbox.present import BACKGROUND_WAIT_S  # noqa: F401 - patched here by tests; present's own wait
from .data_reader import ASK_EVERY_S, ReaderStudio, _current
from .untrusted import inline

TABLE_WAIT_S = 8.0  # the window shown with its rows drawn, counting only time the studio is in view (data_reader's)
ANSWER_WAIT_S = 4.0  # the studio's answer to `table-window` (it answers at once, in the background too)
AREA_FITTED = 0.6  # a window covering this much of the Tab's presentation area is said fitted to it


@dataclass
class Shown:
    """What the studio's last report showed of the table window: it, its Tab's title, whether the studio was in view,
    and the window over it."""
    item: dict | None
    tab_title: str
    in_view: bool | None
    over: dict | None  # an open window over it, by the report


class TableWindows:
    """The `table-window` messages waiting for their studio's answer, each answered once by the studio sent it."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple] = {}

    async def ask(self, studio: ReaderStudio, message: dict, wait: float) -> dict | None:
        future = asyncio.get_running_loop().create_future()
        self.pending[message["id"]] = (studio.client, studio.pin, future)
        try:
            studio.client.offer(json.dumps(message, ensure_ascii=False))
            return await asyncio.wait_for(future, wait)
        except TimeoutError:
            return None
        finally:
            self.pending.pop(message["id"], None)

    def reply(self, client, message: dict) -> bool:
        found = self.pending.get(message.get("id"))
        if found is None or found[0] is not client or found[2].done() or message.get("workspacePin") != found[1]:
            return False
        result = message.get("result")
        if not isinstance(result, dict) or len(json.dumps(result, allow_nan=False).encode()) > 4096:
            return False
        found[2].set_result(result)
        return True


def table_windows(hub) -> TableWindows:
    if getattr(hub, "table_windows", None) is None:
        hub.table_windows = TableWindows()
    return hub.table_windows


def _strip(report: dict | None) -> dict:
    item = next((i for i in (report or {}).get("items") or [] if i.get("id") == "workspace-tabs"), None)
    return (item or {}).get("workspace") or {}


def _covers(a, b) -> bool:
    """Whether rectangle `a` covers at least half of `b` (a panel docked at an edge over a corner of it does not)."""
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 4) or b[2] * b[3] <= 0:
        return True  # no geometry to tell: a window higher in the stacking counts as over it
    width = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return width * height >= 0.5 * b[2] * b[3]


def over(item: dict, items: list[dict]) -> dict | None:
    """The open, shown window highest over `item` in the report (higher in the stacking, covering at least half of it),
    else None: "in front" is that nothing is over it, as the report shows, not that it was sent to the front."""
    from .screen import closable, laid_out

    above = [i for i in items if i is not item and closable(i) and i.get("open", True)
             and (i.get("visible") or laid_out(i)) and (i.get("z") or 0) > (item.get("z") or 0)
             and _covers(i.get("rect"), item.get("rect"))]
    return max(above, key=lambda i: i.get("z") or 0, default=None)


def window_in(report: dict | None, placement: str | None, ref: dict, project: str, tab: str) -> dict | None:
    """The table window of the report showing `ref` (day, id, version) in that Project and Tab, open; by its placement
    when the studio named one."""
    for item in (report or {}).get("items") or []:
        doc = item.get("document") or {}
        resource = doc.get("resource") or {}
        if (str(item.get("id") or "").startswith("document:") and item.get("open", True)
                and (placement is None or item["id"] == f"document:{placement}")
                and doc.get("representation") == "table" and doc.get("project") == project and doc.get("tab") == tab
                and resource.get("day") == ref["day"] and resource.get("id") == ref["id"]
                and (resource.get("version") or 0) == ref["version"]):
            return item
    return None


def fitted(item: dict, report: dict) -> bool:
    area = ((report or {}).get("viewport") or {}).get("presentationArea")
    rect = item.get("rect")
    if not (isinstance(area, list) and isinstance(rect, list) and len(area) == len(rect) == 4) or area[2] * area[3] <= 0:
        return False
    width = max(0.0, min(rect[0] + rect[2], area[0] + area[2]) - max(rect[0], area[0]))
    height = max(0.0, min(rect[1] + rect[3], area[1] + area[3]) - max(rect[1], area[1]))
    return width * height >= AREA_FITTED * area[2] * area[3]


def facts(target: str, revision: int, rows: int, *, displayed: str, front: str) -> str:
    """The separate facts every answer carries (on-screen.md): saved, computed, displayed, in front, accepted."""
    return (f"Facts: saved: in Data as {target} (revision {revision}); computed: {rows} rows; displayed: {displayed}; "
            f"in front: {front}; accepted: not asked.")


async def _tab(services, ctx, asked: str) -> tuple[str, str, bool]:
    """The Tab named (its exact id, else its title, case and spaces aside), made when none has that title, and put in
    front: through the tabs broker, the person's own Tab gestures (workspace_tabs.py). Its id, title and whether made."""
    listed = await services.workspace_tabs(ctx, {"action": "list"})
    tabs = [t for t in listed.get("tabs") or [] if (t.get("kind") or "work") == "work"]
    key = " ".join(asked.split()).casefold()
    found = ([t for t in tabs if t.get("id") == asked]
             or [t for t in tabs if " ".join(str(t.get("title") or "").split()).casefold() == key])
    if len(found) > 1:
        raise ToolFailed(f"several Tabs are called {inline(asked, 80)!r} in this Project",
                         next="ask which one, by the Tabs listed (tabs list)")
    made = not found
    if made:
        reply = await services.workspace_tabs(ctx, {"action": "create", "title": asked})
        before = {t.get("id") for t in tabs}
        new = [t for t in reply.get("tabs") or [] if t.get("id") not in before and t.get("title") == asked[:80]]
        if len(new) != 1:
            raise ToolFailed(f"the Tab {inline(asked, 80)!r} was not confirmed made", next="list the Tabs, then ask again")
        tab = new[0]
    else:
        tab = found[0]
    if not tab.get("selected"):
        reply = await services.workspace_tabs(ctx, {"action": "switch", "tab": tab["id"]})
        if not any(t.get("id") == tab["id"] and t.get("selected") for t in reply.get("tabs") or []):
            raise ToolFailed(f"the Tab {inline(str(tab.get('title')), 80)!r} did not come to the front",
                             next="list the Tabs, then ask again")
    return tab["id"], str(tab.get("title") or asked), made


async def show(services, ctx, studio: ReaderStudio, *, target: str, revision: int, title: str, rows: int, what: str,
               tab: str | None, guard) -> str:
    """Opens the measurements table of `target` at `revision` in its window on the Tab named (made when needed, fitted)
    or the Tab in front, and returns what the report showed. Refuses (saying where the table is saved) when the report
    never shows it there."""
    from .brain.registry import CURRENT_RUN

    run = CURRENT_RUN.get()
    project = studio.project
    ref = {"day": project, "id": target, "version": revision}
    saved = f"it is saved in Data as {target}, where its row's Open in Measurements opens it"
    tab_title, made = None, False
    if tab:
        tab_id, tab_title, made = await _tab(services, ctx, tab)
    else:
        strip = _strip(_current(studio, guard))
        tab_id, tab_title = strip.get("tab") or studio.tab, strip.get("tabTitle")
    guard()
    where = f'Tab "{inline(tab_title, 80)}"' if tab_title else "the Tab in front"

    def refused(why: str):
        raise ToolFailed(f"{what} is not shown in {where}: {why}; {saved}. "
                         + facts(target, revision, rows, displayed="no", front="no"),
                         next="ask again to open it there")

    message = {"type": "table-window", "id": uuid.uuid4().hex, "session": project, "tab": tab_id,
               "workspacePin": studio.pin, "item": {"resource": ref, "title": title}, "fit": bool(tab)}
    answer = await table_windows(services.hub).ask(studio, message, ANSWER_WAIT_S)
    guard()
    if answer is None:
        refused("the studio did not answer")
    if not answer.get("ok"):
        refused(f"the studio said {inline(str(answer.get('error') or 'it could not open it'), 160)!r}")
    placement = str(answer.get("placement") or "") or None

    loop = asyncio.get_running_loop()
    tick = asked_at = loop.time()
    in_view_s = background_s = 0.0  # time waited with the studio in view, and out of it
    away = False  # the studio is in the background now (its progress said once)
    last = Shown(None, tab_title or "", None, None)
    while True:
        report = _current(studio, guard)
        strip = _strip(report)
        item = window_in(report, placement, ref, project, tab_id)
        visible = report.get("visible")
        last = Shown(item, strip.get("tabTitle") or tab_title or "", visible,
                     over(item, report.get("items") or []) if item is not None else None)
        state = ((item or {}).get("document") or {}).get("state")
        if item is not None and state == "failed":
            refused("its rows could not be read there")
        if (item is not None and state == "rendered" and item.get("visible") and visible is not False
                and strip.get("tab") == tab_id and last.over is None):
            break
        now = loop.time()
        if visible is False:  # the studio in the background paints nothing: waited for, its time out of view not counted
            if not away and run is not None:
                run.report_progress("waiting for the studio to be in view: the table is saved in Data, and its window "
                                    "is confirmed once the studio is in view again")
            away = True
            background_s += now - tick
            if background_s >= BACKGROUND_WAIT_S:
                break
        else:
            if away and run is not None:
                run.report_progress(None)
            away = False
            in_view_s += now - tick
            if in_view_s >= TABLE_WAIT_S:
                break
        tick = now
        if now - asked_at >= ASK_EVERY_S:
            studio.client.offer(json.dumps({"type": "screen-ask"}))
            asked_at = now
        await asyncio.sleep(0.05)
    if run is not None:
        run.report_progress(None)

    item = last.item
    where = f'Tab "{inline(last.tab_title, 80)}"' if last.tab_title else "the Tab in front"
    new = " (a new Tab)" if made else ""
    from .screen import laid_out

    if item is not None and last.in_view is False and laid_out(item) and _strip(report).get("tab") == tab_id:
        # Observed: the studio was not in view when it reported (another app's window over it, or the app hidden), and
        # the window laid out in the Tab in front.
        return (f"placed {what} in its table window in {where}{new}; not shown yet: the studio was not in view when it "
                f"last reported, and the window is laid out there for when the app is in front again; {saved}. "
                + facts(target, revision, rows, displayed="not yet (the studio is not in view)", front="unknown"))
    if _strip(report).get("tab") != tab_id:
        refused("another Tab came to the front before it was shown")
    if item is None:
        refused("the studio has not shown its window there" + (" (the Tab is open without it)" if tab else ""))
    state = (item.get("document") or {}).get("state")
    if last.over is not None:
        title_over = inline(str(last.over.get("title") or "another window"), 80)
        refused(f"its window is open there but behind {title_over} (present front {item['id']} brings it forward)")
    if state != "rendered" or not item.get("visible"):
        refused("its window is open there but its rows were still loading when the studio last reported"
                if state != "rendered" else "its window is open there but not visible")
    is_fitted = fitted(item, report)
    fit = (", fitted to the Tab" if is_fitted else ", not fitted to the Tab (its size was kept)") if tab else ""
    return (f"the studio shows {what} in a table window in {where}{new}, in front{fit} ({item['id']}); sortable, "
            "with the median and middle half, and Download CSV. "
            + facts(target, revision, rows, displayed=f"yes, {where}", front="yes"))
