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
import re
import uuid
from dataclasses import dataclass

from .brain.registry import ToolFailed
from .brain.toolbox.present import BACKGROUND_WAIT_S  # noqa: F401 - patched here by tests; present's own wait
from .data_reader import ASK_EVERY_S, ReaderStudio, _current
from .untrusted import inline

TABLE_WAIT_S = 8.0  # the window shown with its rows drawn, counting only time the studio is in view (data_reader's)
ANSWER_WAIT_S = 4.0  # the studio's answer to `table-window` (it answers at once, in the background too)
AREA_FITTED = 0.6  # a window covering this much of the Tab's presentation area is said fitted to it
ARRANGES = 3  # beside: the Tab's Arrange, sent once its window is there and again while the report shows it overlapping
REARRANGE_S = 1.0  # between two Arranges of one placing: the studio's report of the last one comes first


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
        shows = doc.get("resource") or {}  # what the window shows (not named *source: the capture guard)
        if (str(item.get("id") or "").startswith("document:") and item.get("open", True)
                and (placement is None or item["id"] == f"document:{placement}")
                and doc.get("representation") == "table" and doc.get("project") == project and doc.get("tab") == tab
                and shows.get("day") == ref["day"] and shows.get("id") == ref["id"]
                and (shows.get("version") or 0) == ref["version"]):
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


async def _tab(services, ctx, asked: str, *, new: bool = False) -> tuple[str, str, bool]:
    """The Tab named, put in front through the tabs broker, the person's own Tab gestures (workspace_tabs.py): its
    exact id; else the Tab this request made when asked for that title (a create, then a place in it); else the Tab with
    that title, case and spaces aside, and of several so called the one in front, else the newest; else made. `new`: a
    new Tab was asked for, so one is made unless this request made it already, named so it is told apart from a Tab
    already called that ("Reference 2"). Its id, its title and whether this request made it. Never the user's to choose
    between Tabs Hooke can tell apart (will-workspace, 30 Oct: "several Tabs are called 'Reference'; ask which one")."""
    from .workspace_tabs import folded

    listed = await services.workspace_tabs(ctx, {"action": "list"})
    tabs = [t for t in listed.get("tabs") or [] if (t.get("kind") or "work") == "work"]
    broker = getattr(services.hub, "workspace_tabs", None)
    mine = set(broker.made_for(ctx, asked)) if hasattr(broker, "made_for") else set()
    by_id = [t for t in tabs if t.get("id") == asked]
    made_here = [t for t in tabs if t.get("id") in mine]
    named = [t for t in tabs if folded(t.get("title")) == folded(asked)]
    if by_id:
        tab, made = by_id[0], False
    elif made_here:
        tab, made = made_here[-1], True
    elif named and not new:
        tab, made = next((t for t in named if t.get("selected")), named[-1]), False
    else:
        reply = await services.workspace_tabs(ctx, {"action": "create", "title": asked})
        before = {t.get("id") for t in tabs}
        fresh = [t for t in reply.get("tabs") or [] if t.get("id") not in before and (t.get("kind") or "work") == "work"]
        if len(fresh) != 1:
            raise ToolFailed(f"the Tab {inline(asked, 80)!r} was not confirmed made; the studio lists "
                             f"{len(reply.get('tabs') or [])} Tabs", next=None)  # no next: what the studio lists is said
        tab, made = fresh[0], True
    for attempt in range(2):  # a Tab that did not come forward is switched to once more, by Hooke
        if tab.get("selected"):
            break
        reply = await services.workspace_tabs(ctx, {"action": "switch", "tab": tab["id"]})
        tab = next((t for t in reply.get("tabs") or [] if t.get("id") == tab["id"]), tab)
    if not tab.get("selected"):
        raise ToolFailed(f"the Tab {inline(str(tab.get('title')), 80)!r} did not come to the front when switched to "
                         "twice", next=None)  # no next: Hooke switched twice; what the studio shows is said
    return tab["id"], str(tab.get("title") or asked), made


async def show(services, ctx, studio: ReaderStudio, *, target: str, revision: int, title: str, rows: int, what: str,
               tab: str | None, guard, beside: bool = False, moved: str | None = None, fit: bool | None = None) -> str:
    """Opens the measurements table of `target` at `revision` in its window on the Tab named (made when needed, fitted)
    or the Tab in front, and returns what the report showed. Refuses (saying where the table is saved) when the report
    never shows it there. `beside`: the Tab is then arranged, its windows and the live view side by side (placement.py),
    and it is done only once the report shows them so. `moved`: the table's placement, already moved to that Tab (tabs
    move), so no window is opened."""
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
        # No next: what the screen shows and where the table is saved are said; Hooke already did what it can (an
        # Arrange again, a report asked for), so nothing is the user's to repeat (spec 4.8, horizontal 3).
        raise ToolFailed(f"{what} is not shown in {where}: {why}; {saved}. "
                         + facts(target, revision, rows, displayed="no", front="no"), next=None)

    if moved is None:
        message = {"type": "table-window", "id": uuid.uuid4().hex, "session": project, "tab": tab_id,
                   "workspacePin": studio.pin, "item": {"resource": ref, "title": title},
                   "fit": bool(tab) and not beside if fit is None else fit}
        answer = await table_windows(services.hub).ask(studio, message, ANSWER_WAIT_S)
        guard()
        if answer is None:
            refused("the studio did not answer")
        if not answer.get("ok"):
            refused(f"the studio said {inline(str(answer.get('error') or 'it could not open it'), 160)!r}")
        placement = str(answer.get("placement") or "") or None
    else:
        placement = moved
    from .placement import beside_shown

    loop = asyncio.get_running_loop()
    tick = asked_at = loop.time()
    # beside: the Tab's own Arrange, sent once the report shows its window there (one that lands after an Arrange sits
    # on top of the tiling: will-workspace, 30 Oct, "its windows still overlap"), and again, a few times at most, while
    # the report shows them overlapping. Done when the report shows them side by side.
    arranges, arranged_at = 0, None
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
        side_by_side = not beside or (item is not None and arranges > 0 and beside_shown(report, tab_id, [item["id"]])[0])
        if (item is not None and state == "rendered" and item.get("visible") and visible is not False
                and strip.get("tab") == tab_id and last.over is None and side_by_side):
            break
        now = loop.time()
        if (beside and not side_by_side and item is not None and strip.get("tab") == tab_id
                and arranges < ARRANGES and (arranged_at is None or now - arranged_at >= REARRANGE_S)):
            await _arrange(services, ctx, tab_id)
            guard()
            arranges, arranged_at = arranges + 1, loop.time()
            studio.client.offer(json.dumps({"type": "screen-ask"}))
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
    if beside:
        ok, why = beside_shown(report, tab_id, [item["id"]])
        if not ok:
            refused(f"its window is open there but not side by side yet: {why}")
        specimen = ((next((t for t in _strip(report).get("tabs") or () if t.get("id") == tab_id), None) or {})
                    .get("specimen") or {}).get("state")
        with_what = "beside the live view" if specimen == "float" else "beside what is open there"
        how = ", moved there from the Tab it was in" if moved else ""
        return (f"the studio shows {what} in a table window in {where}{new}{how}, {with_what}, tiled side by side "
                f"({item['id']}); sortable, with the median and middle half, and Download CSV. "
                + facts(target, revision, rows, displayed=f"yes, {where}", front="yes"))
    is_fitted = fitted(item, report)
    fit = (", fitted to the Tab" if is_fitted else ", not fitted to the Tab (its size was kept)") if tab else ""
    return (f"the studio shows {what} in a table window in {where}{new}, in front{fit} ({item['id']}); sortable, "
            "with the median and middle half, and Download CSV. "
            + facts(target, revision, rows, displayed=f"yes, {where}", front="yes"))


# --- placing the table (placement.py's vocabulary) ---------------------------------------------------------------------
NEW_TAB_TITLE = "Cell results"  # a population's results Tab, as Will's own "Cell outlines" Tab: its outlines and its table


async def _tabs(services, ctx, args: dict) -> dict:
    """One Tab action through the broker the person's Tab gestures use (workspace_tabs.py)."""
    return await services.workspace_tabs(ctx, args)


async def _arrange(services, ctx, tab_id: str) -> None:
    """The Tab's windows and the live view side by side (beside): the Tab's own Arrange, through the broker; the caller
    waits for the report to show it."""
    broker = getattr(services.hub, "workspace_tabs", None)
    if broker is not None and hasattr(broker, "run"):
        await broker.run(ctx, {"action": "arrange", "tab": tab_id}, verify=False)
    else:
        await _tabs(services, ctx, {"action": "arrange", "tab": tab_id})


async def _open_on(services, ctx, studio, *, ref: dict, title: str, tab_id: str, guard) -> str | None:
    """The table window opened on the Tab in front (`tab_id`), unfitted; its placement as the studio answers."""
    message = {"type": "table-window", "id": uuid.uuid4().hex, "session": studio.project, "tab": tab_id,
               "workspacePin": studio.pin, "item": {"resource": ref, "title": title}, "fit": False}
    answer = await table_windows(services.hub).ask(studio, message, ANSWER_WAIT_S)
    guard()
    if answer is None or not answer.get("ok"):
        why = "the studio did not answer" if answer is None else inline(str(answer.get("error") or "it could not open it"), 160)
        raise ToolFailed(f"the table was not opened: {why}", next=None)  # no next: why is said
    return str(answer.get("placement") or "") or None


async def _wait_for(studio, guard, ok, *, ready=None, arrange=None) -> tuple[dict | None, str]:
    """The studio's report until `ok(report)` says it shows what was asked (asking for reports meanwhile), at most
    TABLE_WAIT_S of the studio's time in view; the last report and what `ok` said is missing. With `arrange`: the Tab's
    Arrange, sent once `ready(report)` says its windows are there, and again (ARRANGES at most, REARRANGE_S apart) while
    `ok` says they are not side by side yet."""
    loop = asyncio.get_running_loop()
    deadline, asked_at, why, report = loop.time() + TABLE_WAIT_S, loop.time(), "", None
    arranges, arranged_at = 0, None
    while True:
        report = _current(studio, guard)
        shown, why = ok(report)
        if shown and (arrange is None or arranges):
            return report, ""
        if loop.time() >= deadline:
            return report, why
        if (arrange is not None and (ready is None or ready(report)) and arranges < ARRANGES
                and (arranged_at is None or loop.time() - arranged_at >= REARRANGE_S)):
            await arrange()
            guard()
            arranges, arranged_at = arranges + 1, loop.time()
            studio.client.offer(json.dumps({"type": "screen-ask"}))
        if loop.time() - asked_at >= ASK_EVERY_S:
            studio.client.offer(json.dumps({"type": "screen-ask"}))
            asked_at = loop.time()
        await asyncio.sleep(0.05)


def _tab_title(report: dict | None, tab_id: str) -> str:
    from .placement import tab_of

    return str((tab_of(report, tab_id) or {}).get("title") or tab_id)


async def place(services, ctx, studio: ReaderStudio, *, target: str, revision: int, title: str, rows: int, what: str,
                tab: str | None, guard, placement: str, instead: bool = False) -> str:
    """The measurements table of `target` placed as asked (placement.py): `beside` the live view and what else is on
    the Tab, tiled (the Tab named or the Tab in front; `instead` moves it there from the Tab it is open in), in a
    `new-tab` with its cells' outlines on their capture beside it (a population's), `only` it on its Tab, or `close`d.
    `window` (and `replace`, which first closes the window in front) is `show`. Each done only once the studio's report
    shows it; otherwise said with what the report shows and where the table is saved."""
    from .placement import beside_shown, identities, only_shown, windows_on

    project = studio.project
    ref = {"day": project, "id": target, "version": revision}
    saved = f"it is saved in Data as {target}, where its row's Open in Measurements opens it"

    def table_on(report, tab_id):
        return window_in(report, None, ref, project, tab_id)

    def refused(why: str, where: str):
        raise ToolFailed(f"{what} is not shown {where}: {why}; {saved}. "
                         + facts(target, revision, rows, displayed="no", front="no"),
                         next=None)  # no next: what is shown and where it is saved are said; nothing to repeat

    if placement == "overlay":
        raise ToolFailed("a table is not drawn on the live view; its cells' outlines are",
                         next=f"present show {target} draws its outlines on the specimen")
    if placement == "close":
        strip = _strip(_current(studio, guard))
        tab_id = strip.get("tab") or studio.tab
        report = _current(studio, guard)
        open_here = [i for i in windows_on(report, tab_id) if ref["id"] in identities(i)
                     and ((i.get("document") or {}).get("representation") == "table")]
        if not open_here:
            return f"{what} is not open in the Tab in front; nothing was closed; {saved}."
        from .brain.toolbox.present import act

        for item in open_here:
            await act(services, {"do": "close", "what": item["id"]}, ctx=ctx)
        return f"closed {what} in Tab \"{inline(_tab_title(report, tab_id), 80)}\"; {saved}."
    if placement in ("window", "replace"):
        if placement == "replace":
            report = _current(studio, guard)
            from .workspace_observation import front

            before = front(report).get("window")
            if before and before.startswith("document:"):
                from .brain.toolbox.present import act

                await act(services, {"do": "close", "what": before}, ctx=ctx)
        return await show(services, ctx, studio, target=target, revision=revision, title=title, rows=rows, what=what,
                          tab=tab, guard=guard)

    if placement == "new-tab":
        # A new Tab of its own, called as asked or "Cell results", and "Cell results 2" when one is called that already
        # (workspace_tabs.fresh_title): a new Tab is asked for, so one is made.
        tab_id, tab_title, made = await _tab(services, ctx, tab or NEW_TAB_TITLE, new=True)
        guard()
        outlines = bool(re.fullmatch(r"e\d+", target))
        if outlines:  # its outlines on their capture first (the masks window), then its table beside them
            entry = await asyncio.to_thread(services.hub.resources.get, target, project)
            guard()
            if entry is None:
                refused("its outlines are not in Data", f'in Tab "{inline(tab_title, 80)}"')
            await _tabs(services, ctx, {"action": "add", "tab": tab_id, "resource": {"id": target, "version": entry.version}})
            guard()
        await _open_on(services, ctx, studio, ref=ref, title=title, tab_id=tab_id, guard=guard)

        def there(report):
            here = windows_on(report, tab_id)
            if _strip(report).get("tab") != tab_id:
                return False, "another Tab came to the front"
            table = table_on(report, tab_id)
            if table is None or (table.get("document") or {}).get("state") != "rendered":
                return False, "its table is not drawn there yet"
            if outlines and not any((i.get("document") or {}).get("representation") == "masks"
                                    and target in identities(i) for i in here):
                return False, "its outlines are not shown there"
            return True, ""

        def shown(report):  # each window there clear of the others (its outlines, its table, the specimen)
            ok, why = there(report)
            for window in windows_on(report, tab_id) if ok else ():
                ok, why = beside_shown(report, tab_id, [window["id"]])
                if not ok:
                    break
            return ok, why

        # Arranged once its windows are there, and again while they overlap (show(), beside).
        report, why = await _wait_for(studio, guard, shown, ready=lambda r: there(r)[0],
                                      arrange=lambda: _arrange(services, ctx, tab_id))
        if why:
            refused(why, f'in Tab "{inline(tab_title, 80)}"')
        new = "a new Tab" if made else "the Tab"
        holds = "the cells' outlines on their capture, with its table beside them" if outlines else "its table"
        return (f'{new} "{inline(tab_title, 80)}" in front holds {holds}: {what}; '
                + facts(target, revision, rows, displayed=f'yes, Tab "{inline(tab_title, 80)}"', front="yes"))

    # beside and only: on the Tab named (made when none has that title) or the Tab in front. Where else the table is
    # open is read from the Tabs: with `instead` (the model's, from the user's words: "instead", "move it") one is moved
    # here, the person's own "Move this view instead"; otherwise it opens here too, and the answer says where else it
    # still is (will-display, 29 Oct: "put the table beside the live view instead" opened a second table, the voice said
    # it was moved, and "which Tab has the measurements?" then found both).
    moved, also = None, []
    if tab:
        tab_id, _title, _made = await _tab(services, ctx, tab)
    else:
        tab_id = _strip(_current(studio, guard)).get("tab") or studio.tab
    listed = await _tabs(services, ctx, {"action": "list"})
    guard()
    copies = [(other, i) for other in listed.get("tabs") or () if other.get("id") != tab_id
              and (other.get("kind") or "work") == "work" for i in other.get("items") or ()
              if i.get("kind") == "table" and (i.get("resource") or {}).get("id") == target and i.get("placement")]
    here = any(t.get("id") == tab_id and any(i.get("kind") == "table" and (i.get("resource") or {}).get("id") == target
                                             for i in t.get("items") or ()) for t in listed.get("tabs") or ())
    if instead and copies and not here:
        other, item = copies.pop(0)
        await _tabs(services, ctx, {"action": "move", "tab": tab_id, "from": other["id"], "placement": item["placement"]})
        moved = item["placement"]
        guard()
    also = list(dict.fromkeys(str(other.get("title") or "") for other, _ in copies))
    names = ", ".join('"' + inline(t, 60) + '"' for t in also[:3])
    hint = ("it was already open here, so nothing moved; closing it there keeps it only here" if instead and here
            else "instead moves it here from there, rather than opening it here too")
    still = f" It is also still open in Tab {names} ({hint})." if also else ""
    if placement == "beside":
        return await show(services, ctx, studio, target=target, revision=revision, title=title, rows=rows, what=what,
                          tab=tab, guard=guard, beside=True, moved=moved) + still
    # only: shown first (as a window), then everything else on its Tab closed and the specimen put away
    said = await show(services, ctx, studio, target=target, revision=revision, title=title, rows=rows, what=what,
                      tab=tab, guard=guard, moved=moved, fit=True)
    report = _current(studio, guard)
    tab_id = _strip(report).get("tab") or studio.tab
    table = table_on(report, tab_id)
    if table is None:
        return said + still
    await _tabs(services, ctx, {"action": "only", "tab": tab_id, "keep": [table["id"]]})
    home = tab_id == "main"
    report, why = await _wait_for(studio, guard, lambda r: only_shown(r, tab_id, [table["id"]], home=home))
    where = f'in Tab "{inline(_tab_title(report, tab_id), 80)}"'
    if why:
        refused(why, where)
    rest = "the live view floats small in its corner" if home else "the live view is hidden there"
    return (f"{what} is now the only window {where}; {rest}. "
            + facts(target, revision, rows, displayed=f"yes, {where}", front="yes") + still)
