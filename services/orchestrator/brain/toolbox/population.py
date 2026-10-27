"""Querying a population (contract 9): facts about the members an operation found, never a
segmentation started on the way (the brain decides whether to run one)."""
from __future__ import annotations

import numpy as np

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import log_measurement

DEFAULT_COLUMNS = ["areaPx", "feretMaxPx", "feretMinPx", "elongation", "circularity", "solidity"]


def add_population_tools(registry: Registry, services) -> None:
    hub = services.hub

    async def query_population(ctx: ToolContext, args: dict) -> str:
        from ...operations import OPERATIONS
        from ...population import (ROWS_MAX, SHOWN_COLOUR, _counts, column_of, drawn_colours, layer_of, resolve, review,
                                    row_words, select, summary)

        population = resolve(hub, args.get("of"), ctx)
        if population is None:
            # Nothing measured yet: say what could produce it, and never start it here.
            from ...population import nothing_here

            raise ToolFailed(nothing_here(hub), next="outline the cells first")
        if not population.columns.get("measure"):
            raise ToolFailed(f"{population.title} ({population.id}) has no measurements yet", next="outline the cells again")
        verified, total = layer_of(hub).verified_count() if layer_of(hub).shown == population.id else (0, len(population.live()))
        placed = (f"shown, {verified} verified in place, {total - verified} unverified" if layer_of(hub).shown == population.id
                  else "not shown")
        columns = [column_of(c, population, hub) for c in (args.get("columns") or [])] or list(DEFAULT_COLUMNS)
        drawn_line = None
        if SHOWN_COLOUR in columns:  # "colour" is the colour each is drawn in now, not a measurement (the review of G3, N3)
            columns = [c for c in columns if c != SHOWN_COLOUR] or list(DEFAULT_COLUMNS)
            try:
                now, not_now, why = drawn_colours(population, layer_of(hub))
                drawn_line = f"drawn now: {_counts(now.values()) or 'none'}" + (f"; {len(not_now)} not drawn ({why})" if not_now else "")
            except ToolFailed as exc:
                drawn_line = f"drawn now: none ({exc})"
        lines = [f"{population.title} ({population.id}; {placed}): {summary(population, columns)}"]
        if drawn_line:
            lines.append(drawn_line)
        if args.get("review"):
            flagged = review(population)
            lines.append(f"worth a second look: {len(flagged)}" + "".join(f"\n  {mid}: {why}" for mid, why in flagged[:ROWS_MAX]))
        chosen_any = any(args.get(k) for k in ("where", "by", "top", "outliers", "ids"))
        if chosen_any:
            notes: list[str] = []
            members, how = select(population, ids=args.get("ids"), where=args.get("where"), by=args.get("by"),
                                  order=str(args.get("order") or "desc"), top=args.get("top"), outliers=args.get("outliers"),
                                  hub=hub, notes=notes)
            shown_cols = [c for c in dict.fromkeys([column_of(args.get("by"), population, hub),
                                                   *(column_of(k, population, hub) for k in (args.get("where") or {})),
                                                   column_of(args.get("outliers"), population, hub), *columns]) if c]
            lines.append(f"{len(members)} member{'s' if len(members) != 1 else ''} ({how}):")
            lines += [f"  {row_words(population, m, shown_cols[:4])}" for m in members[:ROWS_MAX]]
            if len(members) > ROWS_MAX:
                lines.append(f"  ... and {len(members) - ROWS_MAX} more")
            lines += notes  # a colour pick's facts: the other colours drawn, those not drawn now (G3 review, SF1)
        if args.get("against"):  # two captures compared (session knowledge K2)
            from ...identity import compare_populations

            other = resolve(hub, str(args["against"]), ctx)
            if other is None or other is population:
                raise ToolFailed("compare it with which cells? name the other population (e5)", next=None)  # no next: the model names it
            if args.get("link"):  # only when asked: the linking model's links are kept as evidence
                from ...identity import link_populations

                lines.append(await link_populations(hub, population, other))
            lines += compare_populations(hub, population, other)
        log_measurement(services, ctx, "population", population=population, n=len(population.live()),
                        revision=population.revision,  # the numbers are of this revision (the M2.3 re-check)
                        how="queried from the population's measurements (app code on each member's own mask)")
        return "\n".join(lines)

    registry.add(Tool(
        "query_population",
        "Facts about a population of segmented things (outline_cells, automatic masks, find-all matches): the "
        "count, per column the median and middle half, coverage, the members by measured ranges, sorted, the "
        "first few, outliers of a column, or those worth a second look (review). Columns: area, perimeter, "
        "length, width, elongation, roundness, solidity, intensity, score, group (once grouped). It never segments: with no "
        "population it says so. Query this source when asked to check a saved count or measurement, even if "
        "an earlier answer gave a number. For requested largest/smallest members use by with top and order; "
        "the median and middle half are not extrema. If creating or correcting a set first, include this query "
        "after that action in the same plan (of @N for the population made by step N), so the requested "
        "measurements are answered from the resulting revision. Members (c12) can then be highlighted by id, or with highlight's selector.",
        schema({"of": {"type": "string", "description": 'a population id (e3), "@2" for what step 2 of this request made, or empty for the one shown'},
                "where": {"type": "object", "description": 'measured ranges, e.g. {"elongation": [2.5, null], "area": [null, 300]}'},
                "by": {"type": "string", "description": "sort by this column"},
                "order": {"type": "string", "description": '"desc" (default) or "asc"'},
                "top": {"type": "integer", "description": "only the first n"},
                "outliers": {"type": "string", "description": "members far from this population's median of a column"},
                "ids": {"type": "array", "items": {"type": "string"}},
                "review": {"type": "boolean", "description": "list members worth a second look (edge-cut, tiny, maybe merged, low score)"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "against": {"type": "string", "description": "e5: compare with it"},
                "link": {"type": "boolean", "description": "link cells first"}}),
        query_population, kind="query", inline_ms=2000, family="measuring"))


def add_correction_tool(registry: Registry, services) -> None:
    hub = services.hub

    async def correct_cells(ctx: ToolContext, args: dict) -> str:
        """The user says some segmented cells are wrong (M2): a new revision of the population; revision 0
        (the model's own) is kept."""
        import asyncio

        from ...evidence import evidence_of, linked_outline
        from ...population import CorrectionRefused, correct, layer_of, prepare_correction, resolve
        from ..registry import record_made
        from .view import named_since

        store = evidence_of(hub)
        change = str(args.get("change") or "").strip().lower()
        layer = layer_of(hub)

        def cell_of(obj):
            """The population and cell an outline is of: its own record (highlighted, promoted), else a same-object
            link (find-all-like matches, masks pointing); None when it is no segmented cell."""
            made = obj.made_by or {}
            if made.get("member") and store.get(made.get("evidence")) is not None:
                return store.get(made.get("evidence")), made["member"]
            for p in reversed(list(store.items.values())):
                for link in p.links:
                    if link.get("to") == obj.id and link.get("from") in p.members and linked_outline(hub, link) is obj:
                        return p, link["from"]
            return None

        def not_a_cell(obj) -> ToolFailed:
            whose = "the outline you made" if not obj.source.startswith("ai") else "that outline"
            shown = layer.shown is not None
            return ToolFailed(f"{obj.id} ({whose}) is not one of the segmented cells",
                              next=("click the cell itself on the cells' layer, or say its number" if shown else
                                    "show the cells and click the one you mean, or say its number"))

        members, population = [], None
        for ref in [str(m) for m in (args.get("members") or []) if m]:
            obj = hub.scene.objects.get(ref)
            if obj is None:
                members.append(ref)
                continue
            found = cell_of(obj)  # the scene table names outlines (M2.3 review B4)
            if found is None:
                raise not_a_cell(obj)
            population = population or found[0]
            members.append(found[1])
        if not members and change != "undo":
            # What the user pointed at last: the cell selected on the layer, the outline selected, or the outline a
            # click just made, whichever is newest (an older selection never beats a newer click; the re-check).
            # As of when the request was made (ctx.when_asked; the partner's Amendment 1, C2): something pointed at
            # while it was being answered is not what "that" meant. This request's own step comes first.
            scene = hub.scene
            asked = getattr(ctx, "when_asked", None)
            until = asked.at if asked is not None else float("inf")
            pointed = []
            if getattr(ctx, "pointed", None) in scene.objects:
                pointed.append((float("inf"), "outline", scene.objects[ctx.pointed]))
            if asked is not None:
                if asked.cell is not None:
                    pointed.append((asked.cell_at, "cell", asked.cell))
                if asked.selected is not None:
                    pointed.append((asked.selected_at, "outline", scene.objects.get(asked.selected)))  # None: gone since
            else:
                if layer.selected and layer.shown:
                    pointed.append((getattr(layer, "selected_at", 0.0), "cell", (layer.shown, layer.selected)))
                if getattr(scene, "selected", None) in scene.objects:
                    pointed.append((getattr(scene, "selected_at", 0.0), "outline", scene.objects[scene.selected]))
            clicked = [o for o in scene.objects.values() if o.source == "click" and o.created_at <= until]
            if clicked:
                newest = max(clicked, key=lambda o: o.created_at)
                pointed.append((newest.created_at, "outline", newest))
            if pointed:
                _, kind, what = max(pointed, key=lambda t: t[0])
                if what is None:  # what was selected when the user asked has gone since: never another in its place
                    raise ToolFailed("the outline you had selected when you asked is no longer on screen",
                                     next="click it again")
                if kind == "cell":
                    population, members = store.get(what[0]), [what[1]]
                    if population is None:
                        raise ToolFailed("the cells you had shown when you asked are no longer here",
                                         next="show the cells and click the one you mean")
                else:
                    found = cell_of(what)
                    if found is None:
                        raise not_a_cell(what)
                    population, members = found[0], [found[1]]
        if args.get("of") and population is None:  # the outline's own population comes first (the re-check's note)
            population = resolve(hub, args.get("of"), ctx)
        elif population is None and members:  # the population the cells belong to, shown or not (S3)
            population = next((p for p in reversed(list(store.items.values())) if members[0] in p.members), None)
        elif population is None and change == "undo":
            # The latest correction made in this conversation, whichever conversation the cells were made in (S3).
            thread = getattr(getattr(ctx, "asked", None), "thread", None)
            undone = {(p.id, r.get("undoes")) for p in store.items.values() for r in p.revisions if r.get("undoes")}
            open_ = [(r.get("at") or 0, p) for p in store.items.values() for r in p.revisions
                     if (r.get("was") or r.get("added")) and not r.get("undoes") and (p.id, r.get("r")) not in undone
                     and (thread is None or (r.get("thread") or p.thread) == thread)]
            if not open_ and thread is not None:  # never another conversation's correction (re-check S3)
                raise ToolFailed("nothing has been corrected in this conversation to undo",
                                 next=None)  # no next: there is nothing here to undo
            population = max(open_, key=lambda t: t[0])[1] if open_ else None
        if population is None:
            population = resolve(hub, None, ctx)
        if population is None:
            raise ToolFailed("no cells have been segmented here to correct", next="outline the cells first")
        if ("revision" in args and args["revision"] != population.revision
                or "session" in args and args["session"] != population.session):
            raise ToolFailed("the named population revision or session changed", next="review the saved result and try again")
        thread = getattr(getattr(ctx, "asked", None), "thread", None)
        from ...ticket import current
        request, revision = current(), population.revision
        try:
            with store.keeping_pixels(population):  # read back and used in one hold: a save finishing meanwhile keeps them
                gray = await asyncio.to_thread(prepare_correction, hub, population, change)  # off the loop (S5)
                reason = request.superseded("cell") if request is not None else None
                if reason or request is not None and request.closed:
                    raise CorrectionRefused(reason or "the correction request ended", next="review the result and try again")
                if store.get(population.id) is not population or population.revision != revision:
                    raise CorrectionRefused("the population revision or session changed while its masks were read",
                                            next="review the current result and try again")
                words, gone = correct(hub, population, change, members, by="user", why=str(args.get("why") or "")[:80],
                                      gray=gray, broadcast=False, asked=ctx.question[:200], thread=thread)
        except CorrectionRefused as exc:
            raise ToolFailed(str(exc), next=exc.next) from None
        if layer.shown == population.id:
            layer.broadcast(layer.wire())
        record_made(ctx, population.id)  # "@n" names it to the steps after (S2)
        # Outlines the app made of those members (highlighted, promoted) no longer stand; the user's own stay, and so
        # does one the user named or confirmed (S4), which is said.
        linked = {link["to"] for link in population.links if link.get("from") in gone and linked_outline(hub, link) is not None}
        removed, kept = [], []
        for oid in linked:
            obj = hub.scene.objects.get(oid)
            if obj is None or not obj.source.startswith("ai"):
                continue
            if obj.status == "confirmed" or named_since(obj):
                kept.append(obj)
                continue
            hub.scene.remove(oid)
            removed.append(oid)
        log_measurement(services, ctx, "population", population=population, n=len(population.live()),
                        revision=population.revision, execution=(population.made_by or {}).get("execution"),
                        asked=ctx.question[:200], how=f"corrected by the user (revision {population.revision})")
        back = []
        if change == "undo":  # cells back whose outlines a correction took off: said, never drawn again unasked
            undone = population.revisions[-1].get("undoes")
            was = next((r.get("was") or {} for r in population.revisions if r.get("r") == undone), {})
            outlined = {link["from"] for link in population.links if link.get("kind") == "same-object"
                        and link.get("from") in was and linked_outline(hub, link) is None}
            back = sorted(outlined)
        return (words + (f"; {'its outline' if len(removed) == 1 else 'their outlines'} taken off too" if removed else "")
                + "".join(f"; your \"{o.label}\" outline is kept (you named it)" for o in kept)
                + (f"; {', '.join(back)} {'is' if len(back) == 1 else 'are'} not outlined again (ask to highlight "
                   f"{'it' if len(back) == 1 else 'them'})" if back else ""))

    registry.add(Tool(
        "correct_cells",
        "Only when the user says segmented cells are wrong (\"that's not a cell\", \"these two are one\"): remove, merge "
        "or undo the last correction, as a new revision.",
        schema({"change": {"type": "string", "description": "remove, merge or undo"},
                "members": {"type": "array", "items": {"type": "string"}, "description": "c12...; empty: the one clicked"},
                "of": {"type": "string"},
                "revision": {"type": "integer", "description": "exact revision being corrected, when known"},
                "session": {"type": "string", "description": "owning session, when explicitly supplied"},
                "why": {"type": "string"}},
               ["change"]),
        correct_cells, inline_ms=1500, family="measuring"))


GROUP_COLUMNS = ["areaPx", "elongation", "circularity", "solidity", "intensity"]


def add_group_tools(registry: Registry, services) -> None:
    """Groups by look (session knowledge K1): every model adds facts about the same members, as column groups;
    the brain is given the facts and explains; a group is a group, never a type."""
    hub = services.hub

    def _population(ctx, args):
        from ...population import resolve

        population = resolve(hub, args.get("of"), ctx)
        if population is None:
            raise ToolFailed("no cells have been segmented here yet (outline_cells segments every cell, about 1-8 s)",
                             next="outline the cells first")
        return population

    async def group_cells(ctx: ToolContext, args: dict) -> str:
        from ...enrichment import GROUPS_CALIBRATION, K_MAX, K_MIN, current, model_label, run_groups, run_light, stale_words
        from ...operations import Unavailable
        from ..registry import record_made

        population = _population(ctx, args)
        if args.get("group") and args.get("name"):
            return await name_group(ctx, population, str(args["group"]), str(args["name"]))
        k = args.get("groups")
        if k is not None and not (K_MIN <= int(k) <= K_MAX):
            raise ToolFailed(f"between {K_MIN} and {K_MAX} groups", next=None)  # no next: the model's own call; it corrects it
        try:
            gid, result = await run_groups(hub, population, k=int(k) if k else None)
        except Unavailable as exc:
            raise ToolFailed(f"the cells can't be grouped by look here: {exc}", next=exc.next or "ask again later") from None
        record_made(ctx, population.id)  # "@n" names the grouped population to the steps after (review SF5)
        try:
            await run_light(hub, population)  # cheap facts for describe_groups; its failure is not this answer's
        except (ToolFailed, Unavailable):
            pass
        group = current(population, gid)
        if group is None:  # corrected while its colours were measured (the K1 re-check, B1): said, never a raw error
            raise ToolFailed(stale_words(population, population.columns.get(gid) or {}), next="group them again")
        model = group.get("model") or gid.split(":", 1)[1]
        sizes = ", ".join(f"{g} ({n})" for g, n in group["sizes"].items())
        reused = " (reused: these cells were already grouped)" if result.use.reused else ""
        log_measurement(services, ctx, "groups", population=population, k=group["k"], silhouette=group["silhouette"],
                        execution=group.get("execution"), model=model,
                        how=f"k-means over {group['looks']}, k by silhouette ({group['tried']})")
        # The silhouette's score and what the groups bench measured, as data (review SF1): never "a clear split".
        chosen = (f"{group['k']} groups as asked (silhouette {group['silhouette']:.2f})" if group.get("asked") else
                  f"the number of groups was chosen by silhouette ({group['silhouette']:.2f}; tried "
                  f"{', '.join(f'{kk}: {v:.2f}' for kk, v in group['tried'].items())})")
        measured = GROUPS_CALIBRATION.get(model, f"How {model_label(model)}'s groups match kinds of cell is not measured yet.")
        return (f"{population.title} ({population.id}): {len(group['values'])} cells in {group['k']} groups by look "
                f"({model_label(model)}){reused}: {sizes}; {chosen}. {measured} A number of groups can be asked for. A group "
                "is cells that look alike to the model, not a type; describe_groups says how they differ")

    async def name_group(ctx: ToolContext, population, gname: str, name: str) -> str:
        """The user names a group ("those are platelets", finding K1-5): its representatives are kept with the
        population as that name's examples, apart from taught.py's (they are the app's picks, not cells the user
        confirmed, so naming a click's taught line never counts them), and every member gets its nearest named
        group by look, with a label only from the picture model's measured floor. Never written into a label."""
        from ...enrichment import current, floor_of, floor_words, grouping, model_label, run_your_label, stale_words
        from ...operations import Unavailable
        from ...untrusted import inline
        from ..registry import record_made

        gid, groups = grouping(hub, population)
        model = groups.get("model") or gid.split(":", 1)[1]
        key = gname.strip().lower().replace("group ", "g").replace(" ", "")
        if key.isdigit():
            key = f"g{key}"
        reps = groups["representatives"].get(key)
        if not reps:
            raise ToolFailed(f"there is no {gname} in {model_label(model)}'s look groups (they are "
                             f"{', '.join(groups['representatives'])})",
                             next=None)  # no next: the message lists the groups; the model asks again
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ToolFailed("there is no name to keep", next="say what the group is called")
        try:
            ngid, _result, earlier = await run_your_label(hub, population, gid, key, name)
        except Unavailable as exc:
            raise ToolFailed(f"the group can't be named here: {exc}", next=exc.next or "ask again later") from None
        record_made(ctx, population.id)
        column = current(population, ngid)
        if column is None:  # corrected meanwhile (the K1 re-check, B1): said, never a raw error
            raise ToolFailed(stale_words(population, population.columns.get(ngid) or {}, "nearest named groups"),
                             next="group them again, then name the group")
        values = column["values"]
        examples = set(reps)
        others = [m.id for m in population.live() if m.id not in examples]
        near = [m for m in others if (values.get(m) or {}).get("yourLabel") == name]
        log_measurement(services, ctx, "groups", population=population, execution=column.get("execution"), model=model,
                        named=key, floor=floor_of(model), how="each cell's nearest named group by look (1-NN cosine)")
        said = inline(name, 60)
        head = (f"\"{said}\" is kept for the {len(reps)} cells nearest the centre of {key} ({model_label(model)}): the app's "
                "picks, not cells you confirmed, and not your taught examples")
        # What became of the names given before, said every time (the K1 re-check, SF3): none goes unsaid.
        quoted = lambda names: ", ".join(f"\"{inline(n, 60)}\"" for n in names)  # noqa: E731
        kept = ", ".join(f"\"{inline(e['name'], 60)}\"" + (f" ({e['left']} of its {e['of']} example cells left)"
                                                          if e["left"] < e["of"] else "") for e in earlier["kept"])
        moved = "; ".join(f"your earlier \"{inline(e['name'], 60)}\" keeps {e['left']} of its {e['of']} example cells, and "
                          f"{e['moved']} {'is' if e['moved'] == 1 else 'are'} now \"{said}\"" for e in earlier["moved"])
        whole = [e["name"] for e in earlier["replaced"] if e["how"] == "all"]
        again = [e["name"] for e in earlier["replaced"] if e["how"] == "same group"]
        before = "".join([
            f"; your earlier names kept: {kept}" if earlier["kept"] else "",
            f"; {moved}" if moved else "",
            f"; it replaces your earlier {quoted(whole)} (every one of its example cells is in this group)" if whole else "",
            f"; it replaces your earlier {quoted(again)} (this group, named again)" if again else "",
            f"; your earlier {quoted(earlier['dropped'])} dropped: none of its example cells is left after your corrections"
            if earlier["dropped"] else ""])
        if floor_of(model) is None:
            return f"{head}{before}; each other cell has its similarity to them beside it; {floor_words(model)}"
        return (f"{head}{before}; {len(near)} of the {len(others)} other cells are closest to the group you named at or above "
                f"{floor_of(model):.2f} ({floor_words(model)}): a \"nearest group you named\" column beside each cell, "
                "never their names")

    async def describe_groups(ctx: ToolContext, args: dict) -> str:
        from ...enrichment import NAMES_BASIS, grouping, model_label, run_names, stale_words
        from ...operations import Unavailable
        from ...population import PLAIN_COLUMNS, UNITS, value
        from ..registry import record_made

        population = _population(ctx, args)
        gid, groups = grouping(hub, population)  # the one grouping every reader uses (review SF2); refused when stale (B3)
        model = groups.get("model") or gid.split(":", 1)[1]
        names = None
        if args.get("names"):
            try:
                ngid, _ = await run_names(hub, population, gid)
                names = population.columns[ngid]
                record_made(ctx, population.id)  # it wrote (or reused) a names group on the population
            except (ToolFailed, Unavailable) as exc:
                names = {"error": str(exc)}
        if population.stale(groups):  # corrected while the names were ranked (the K1 re-check, B1)
            raise ToolFailed(stale_words(population, groups), next="group them again")
        members = {g: [population.members[m] for m, v in groups["values"].items()
                       if v["group"] == g and m in population.members and population.members[m].status not in ("deleted", "merged")]
                   for g in groups["sizes"]}
        lines = [f"{population.title} ({population.id}): {groups['k']} groups by look ({model_label(model)}; silhouette "
                 f"{groups['silhouette']:.2f}); groups are cells that look alike to the model, never types"]
        medians: dict[str, dict[str, float]] = {}
        for g, ms in members.items():
            parts = []
            for column in GROUP_COLUMNS + ["meanRed", "meanGreen", "meanBlue"]:
                vals = [v for v in (value(population, m, column) for m in ms) if isinstance(v, (int, float))]
                if vals:
                    med = float(np.median(vals))
                    medians.setdefault(column, {})[g] = med
                    parts.append(f"{PLAIN_COLUMNS.get(column, column)} {med:.3g}{(' ' + UNITS[column]) if UNITS.get(column) else ''}")
            line = f"{g}: {len(ms)} cells; medians: {', '.join(parts)}; e.g. {', '.join(groups['representatives'].get(g, [])[:3])}"
            if names and names.get("byGroup", {}).get(g):
                line += "; MedSigLIP's names (data): " + ", ".join(f"{n['name']} {n['share']:.2f}" for n in names["byGroup"][g])
            lines.append(line)
        spread = sorted(((max(v.values()) / max(min(v.values()), 1e-9), c) for c, v in medians.items() if len(v) > 1
                         and min(v.values()) > 0), reverse=True)
        if spread:
            lines.append("most different between groups (ratio of the largest to the smallest median): " +
                         ", ".join(f"{PLAIN_COLUMNS.get(c, c)} {r:.1f}x" for r, c in spread[:3]))
        if names and names.get("calibration"):
            lines.append(f"MedSigLIP's names are data, not names: {names['calibration']}. {NAMES_BASIS}")
        elif names and names.get("error"):
            lines.append(f"no MedSigLIP names: {names['error']}")
        log_measurement(services, ctx, "groups", population=population, k=groups["k"], execution=groups.get("execution"),
                        model=model, how="per-group medians of each member's own measurements")
        return "\n".join(lines)

    registry.add(Tool(
        "group_cells",
        "Group segmented cells by look (\"group these by look\"); group+name: the user names one (\"those are platelets\").",
        schema({"of": {"type": "string"}, "groups": {"type": "integer", "description": "how many, if asked"},
                "group": {"type": "string", "description": "g2"}, "name": {"type": "string"}}),
        group_cells, inline_ms=4000, family="measuring"))
    registry.add(Tool(
        "describe_groups",
        "How the look groups differ (sizes, measured medians, examples); names: MedSigLIP's names as data.",
        schema({"of": {"type": "string"}, "names": {"type": "boolean"}}),
        describe_groups, kind="query", inline_ms=3000, family="measuring"))
