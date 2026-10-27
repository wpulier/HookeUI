"""Identity (session knowledge K2): following chosen cells over time, where they are and whether they
changed, and "that's the same one" (or not). Every answer says how it knows; a missing link means can't tell.
What the user reads is in plain words: cells by their numbers, outlines by their names; individuals' ids
(i4) come last, for asking again."""
from __future__ import annotations

import re

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from .common import describe, log_measurement


def add_identity_tools(registry: Registry, services) -> None:
    hub = services.hub

    def store():
        from ...identity import identity_of

        return identity_of(hub)

    def population_of(member: str, ctx: ToolContext, of=None):
        """The population a cell belongs to: the one named, else the one holding it (cell ids are numbered
        through the day, so a bare c12 names one cell), else the one shown. Cells segmented for someone else (a
        researcher, a comparison: `made_for`) stand in only when their population is named or shown, as for "the
        cells" (population.resolve), or when the cell is already one of the things followed or linked today."""
        from ...evidence import evidence_of
        from ...identity import member_node
        from ...population import layer_of, resolve

        if of:
            return resolve(hub, of, ctx)
        holding = next((p for p in reversed(list(evidence_of(hub).items.values())) if member in p.members), None)
        if holding is not None and holding.made_for and layer_of(hub).shown != holding.id \
                and not store().find(member_node(holding.id, member)):
            raise ToolFailed(f"{member} is one of the cells segmented for {holding.made_for} ({holding.id}), which stand in "
                             "only when named", next=f"name {holding.id} to use its cells, or outline the cells on the view now")
        return holding or resolve(hub, None, ctx)

    def node_of(ref, ctx: ToolContext) -> str:
        """An outline (o4, "that", "target n", a name) as this run's outline node, a cell (c12, e3/c12) as a
        member node, or an individual (i4) as one of its nodes."""
        from ...identity import member_node, outline_node
        from .common import resolve_object

        ref = str(ref or "").strip()
        if "/" in ref:
            return ref
        if re.fullmatch(r"i\d+", ref):
            nodes = sorted(store().nodes_of(ref)) if ref in store().made else []
            if not nodes:
                raise ToolFailed(f"no followed thing is {ref}", next="say which cells, by their number")
            return nodes[0]
        if re.fullmatch(r"c\d+", ref):
            population = population_of(ref, ctx)
            if population is None or ref not in population.members:
                raise ToolFailed(f"{ref} is not one of the cells segmented here", next="say which cells, by their number")
            return member_node(population.id, ref)
        return outline_node(resolve_object(hub, ref or "that", ctx).id)

    def plain(node: str) -> str:
        """A node as the user knows it: a cell by its number, an outline by its name."""
        from ...identity import is_member

        if is_member(node):
            return node.split("/", 1)[1]
        obj = store().outline(node)
        return describe(obj) if obj is not None else "an outline from an earlier run of the app"

    def joined(names: list[str]) -> str:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    async def follow_cells(ctx: ToolContext, args: dict) -> str:
        """Keep an eye on chosen cells: each outlined from its own mask, followed, and measured again on new
        views while its own pixels are found where it is (contract 2: following chosen members over time)."""
        from ...identity import (EARLIER_VIEW, FOLLOW_FOR_S, GONE, MAX_FOLLOWED, MEASURE_EVERY_S, RUN, made_on_earlier_run,
                                 member_node, outline_node)
        from ...population import promote

        identity = store()
        members = [str(m) for m in (args.get("members") or []) if m]
        objects = [str(o) for o in (args.get("objects") or []) if o]
        if not members and not objects:
            raise ToolFailed("follow which? cells by their number (c12), or outlines", next="say which ones")
        if len(members) + len(objects) > MAX_FOLLOWED:
            raise ToolFailed(f"at most {MAX_FOLLOWED} can be followed at once", next=f"choose {MAX_FOLLOWED} of them")
        made, skipped = [], []  # (individual, the user's words for it); what was not followed, and why
        groups: dict[str, tuple] = {}
        missing = []
        for m in members:
            population = population_of(m, ctx, args.get("of"))
            if population is None or m not in population.members:
                missing.append(m)
            else:
                groups.setdefault(population.id, (population, []))[1].append(m)
        if missing:
            if not groups and all(population_of(m, ctx, args.get("of")) is None for m in missing):
                raise ToolFailed("no cells have been segmented here yet", next="outline the cells first")
            raise ToolFailed(f"{joined(missing)} {'is' if len(missing) == 1 else 'are'} not among the cells segmented here",
                             next="say which cells, by their number")
        earlier = False
        for population, mids in groups.values():
            if population.view.get("run") != RUN:  # its view's offsets and epoch were another run's (the K2 re-check, SF2)
                earlier = True
                skipped.append(f"{joined(mids)} {'was' if len(mids) == 1 else 'were'} segmented before the app restarted, "
                               f"so where {'it is' if len(mids) == 1 else 'they are'} now is not known: outline the cells "
                               "again on the view now")
                continue
            removed = [m for m in mids if population.members[m].status in GONE]
            skipped += [f"{m} was removed by your correction, so it is not followed" for m in removed]
            live = [m for m in mids if m not in removed]
            # One already followed with its outline on the view is followed again as it is (never outlined twice).
            for m in list(live):
                known = identity.find(member_node(population.id, m))
                if known and any(identity.outline(n) is not None for n in identity.given(known[0])):
                    made.append((known[0], m))
                    live.remove(m)
            if not live:
                continue
            outlines = await promote(hub, population, live, why="followed")
            by_member = {(o.made_by or {}).get("member"): o for o in outlines}
            for m in live:
                obj = by_member.get(m)
                if obj is None:
                    skipped.append(f"{m} could not be outlined, so it is not followed")
                    continue
                made.append((identity.make([member_node(population.id, m), outline_node(obj.id)],
                                           why=str(args.get("why") or "followed"), by="follow_cells"), m))
        for oid in objects:
            node = node_of(oid, ctx)
            obj = identity.outline(node)
            if obj is None:
                raise ToolFailed(f"{oid} is not outlined now", next="click it, then ask again")
            if made_on_earlier_run(obj):
                raise ToolFailed(f"{describe(obj)}: {EARLIER_VIEW}", next="outline it again on the view now")
            made.append((identity.make([node], why=str(args.get("why") or "followed"), by="follow_cells"), describe(obj)))
        if not made:
            raise ToolFailed("nothing could be followed: " + "; ".join(skipped),
                             next="outline the cells again on the view now" if earlier else "say which cells, by their number")
        stopped = []
        for i, _ in made:
            stopped += identity.follow(i)
        mine = {i for i, _ in made}
        stopped = [i for i in dict.fromkeys(stopped) if i not in mine]
        log_measurement(services, ctx, "individuals", individuals=[i for i, _ in made],
                        how="followed: outlined from their own masks, carried by continuity, measured again on new views")
        said = (f"keeping an eye on {joined([w for _, w in made])}, each outlined: each is measured again every "
                f"{MEASURE_EVERY_S:.0f} s while its own pixels are found where it is, for up to {FOLLOW_FOR_S / 60:.0f} minutes "
                "while the app runs; where it goes is known only while the slide's movement is followed")
        if skipped:
            said += "; " + "; ".join(skipped)
        if stopped:
            names = [joined(sorted({plain(n) for n in identity.given(i)})) or i for i in stopped]
            said += f"; no longer followed (at most {MAX_FOLLOWED} at once): {joined(names)}"
        return said + f" (to ask about {'it' if len(made) == 1 else 'them'} later: {', '.join(i for i, _ in made)})"

    async def individuals(ctx: ToolContext, args: dict) -> str:
        """Where followed things are, and whether they changed, from their histories (contract 2)."""
        from ...identity import change_words, how_words, thing_words, where_words

        identity = store()
        which = str(args.get("which") or "").strip()
        if which and not re.fullmatch(r"i\d+", which):
            try:
                which = node_of(which, ctx)
            except ToolFailed:
                raise ToolFailed(f"no followed thing is {which}", next="ask to keep an eye on some cells first") from None
        found = identity.find(which)
        if not found:
            raise ToolFailed("nothing is being followed today" if not identity.individuals() else
                             "that is not one of the things followed today", next="ask to keep an eye on some cells first")
        ask = str(args.get("ask") or "both").lower()
        lines = []
        for i in found[:8]:
            history = identity.history(i)
            line = (f"{thing_words(identity, i)} ({i}): {how_words(identity, i)}; seen {len(history)} "
                    f"time{'s' if len(history) != 1 else ''}")
            gone = [identity.gone(n) for n in identity.given(i)]
            line += "".join(f"; {g}" for g in gone if g)
            if ask in ("where", "both"):
                line += f"; where: {where_words(hub, identity, i)}"
            if ask in ("changed", "both"):
                line += f"; changed: {change_words(identity, i)}"
            lines.append(line)
        log_measurement(services, ctx, "individuals", individuals=found[:8],
                        how="from each individual's observations, compared only on a shared basis")
        return "\n".join(lines)

    async def same_one(ctx: ToolContext, args: dict) -> str:
        identity = store()
        a, b = node_of(args.get("a"), ctx), node_of(args.get("b"), ctx)
        if a == b:
            raise ToolFailed("those are the same outline already", next=None)  # no next: nothing to link
        if args.get("undo"):  # "those aren't the same": a new event naming the link; nothing is deleted
            undone = identity.not_same(a, b, why=ctx.question)
            if not undone:
                together = any(a in identity.nodes_of(i) and b in identity.nodes_of(i) for i in identity.individuals())
                raise ToolFailed(f"I have no link of yours or the linking model's between {plain(a)} and {plain(b)} to undo"
                                 + (" (they are one thing through other links: say which pair is wrong)" if together else ""),
                                 next=None)  # no next: nothing to undo
            return (f"{plain(a)} and {plain(b)} are no longer one thing (you said they aren't the same; the earlier "
                    "link stays in today's record, marked undone)")
        i = identity.same_one(a, b, basis="user")
        return f"{plain(a)} and {plain(b)} are now one thing, because you said so (to ask about it later: {i})"

    registry.add(Tool("follow_cells", "Keep an eye on chosen cells over time (\"keep an eye on the odd ones\").",
                      schema({"members": {"type": "array", "items": {"type": "string"}, "description": "c12..."},
                              "objects": {"type": "array", "items": {"type": "string"}, "description": "o4..."},
                              "of": {"type": "string"}, "why": {"type": "string"}}),
                      follow_cells, inline_ms=2500, family="motion"))
    registry.add(Tool("individuals", "Where followed things are and whether they changed.",
                      schema({"which": {"type": "string", "description": "i4 or empty"},
                              "ask": {"type": "string", "description": "where, changed or both"}}),
                      individuals, kind="query", inline_ms=1500, family="motion", outline_refs=("which",)))
    registry.add(Tool("same_one", "The user says two are the same thing (\"that's the same one\"); undo: they aren't.",
                      schema({"a": {"type": "string"}, "b": {"type": "string"}, "undo": {"type": "boolean"}}, ["a", "b"]),
                      same_one, inline_ms=200, family="motion", outline_refs=("a", "b")))
