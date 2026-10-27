"""Acting on what the app already sees and on what is on screen, without a new look:
highlight a seen thing from its box, close the magnifier, show, read, step through or close a
researcher's work."""
from __future__ import annotations

from ... import quiz
from ...population import ROUNDNESS
from ...presenter import session_steps as presenter_steps
from ...untrusted import block
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema
from ...research.policy import ResearchRefused
from .common import RESEARCHER, describe
from .research import RESEARCH_REFERENCE, bound_researcher, research_request_current

def add_view_tools(registry: Registry, services) -> None:
    """Acting on what the app already sees and on what is on screen, without a new look:
    highlight a seen thing from its box, close the magnifier, show or close a result."""
    hub, magnifier, manager, desk = services.hub, services.magnifier, services.research, services.desk
    sight, presenter, outline_seen = services.sight, services.presenter, services.outline_seen
    if sight is not None and outline_seen is not None:
        add_highlight(registry, hub, sight, outline_seen, magnifier, getattr(services, "log", None))
    if magnifier is not None:
        add_close_magnifier(registry, magnifier, services)
    if manager is not None and desk is not None and presenter is not None:
        add_result_tools(registry, manager, desk, presenter, hub, services.day, services)


def outlined_words(made: list) -> str:
    """What outlining did, never claiming an outline is shown when it is not (ADR 0006): one whose
    place on the live view could not be verified is kept, not drawn, until its own pixels are found.
    Outlines not shown for the same reason are said once (acceptance flow 9: five said it five times)."""
    shown = [o for o in made if getattr(o, "shown", True)]
    hidden = [o for o in made if not getattr(o, "shown", True)]
    names = [describe(o) for o in shown]
    unnamed = names.count("that outline")  # several unnamed outlines are counted, never "that outline" again and again
    names = [n for n in names if n != "that outline"] + (
        [f"{unnamed} outlines not named yet"] if unnamed > 1 else ["that outline"] if unnamed else [])
    parts = ["outlined " + ", ".join(names)] if shown else []
    by_why: dict[str, list] = {}
    for o in hidden:
        by_why.setdefault(o.not_shown_why(), []).append(o)
    for why, objs in by_why.items():
        if len(objs) == 1:
            parts.append(f"{describe(objs[0])} was outlined but is not shown yet ({why}); "
                         "it appears once its own pixels are found on the live view")
        else:
            parts.append(f"{len(objs)} more were outlined but are not shown yet ({why}); "
                         "they appear once their own pixels are found on the live view")
    return "; ".join(parts)


SELECTORS = ("of", "members", "top", "by", "where", "outliers", "rest")  # rest alone: the shown cells' layer
READ_PAGE = 1800  # characters of a researcher's work per read_result page (inside what one answer carries whole)


def _nth(n: int) -> str:
    return f"{n}th" if 10 <= n % 100 <= 20 else f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def basis_words(args: dict) -> str:
    """The measured basis in the user's words ("largest by area"), or ""."""
    from ...population import PLAIN_COLUMNS, column_of

    column = column_of(args.get("by"))
    if not column:
        return ""
    asc = str(args.get("order") or "desc").lower() == "asc"
    return f"{'smallest' if asc else 'largest'} by {PLAIN_COLUMNS.get(column, column)}"


def first_guess_of(hub, ref, ctx: ToolContext):
    """The first guess `checks` names: only this request's own target ("target 1", or an id it outlined),
    never "that" or the selection (M2.1 review S3); None when it is not outlined."""
    from ...board import TARGET

    text = str(ref or "").strip()
    m = TARGET.fullmatch(text)
    if m:
        listed = ctx.targets or ctx.objects  # the look's own numbers when it pointed: a failed one never shifts the rest
        i = int(m.group(1)) - 1
        oid = listed[i] if 0 <= i < len(listed) else None
    else:
        oid = ctx.aliases.get(text, text) if text in ctx.objects or ctx.aliases.get(text) in ctx.objects else None
    return hub.scene.objects.get(oid) if oid else None


def made_by_this_request(obj) -> bool:
    """An outline this request's look made (not one it reused from an earlier answer, not the user's):
    only such a guess may be taken off by the measurement (M2.1 review B2)."""
    import time as _time

    from ...ticket import current

    request = current()
    if request is None or obj is None:
        return False
    started = _time.time() - (_time.monotonic() - request.created)
    return (obj.created_at >= started - 0.002 and str(obj.source).startswith("ai") and obj.trial is None
            and not str(obj.source).startswith("ai:measured"))  # a measured outline is never anyone's guess


def named_since(obj) -> bool:
    """Whether anyone but the look named the outline after the look did: a rename it does not record as the
    look's, or a label its naming record does not explain (the "?" of a first guess aside)."""
    naming = obj.naming or {}
    renames = naming.get("renames") or []
    if any(r.get("by") != "look" for r in renames):
        return True
    expected = renames[-1].get("to") if renames else naming.get("label")
    return bool(naming) and (obj.label or "").rstrip("?").strip() != (expected or "").rstrip("?").strip()


def note_guess(hub, guess, what: str) -> None:
    """The guess's note once measuring ends: what the measurement said, never "being measured" after it."""
    if guess is None or guess.id not in hub.scene.objects or not (guess.note or "").startswith("a first guess by eye, being measured"):
        return
    rest = guess.note[len("a first guess by eye, being measured"):]
    hub.scene.update(guess.id, note=f"a first guess by eye; {what}{rest}")


def guess_verdict(population, chosen: list, guess, args: dict, pinned: str = "") -> tuple[str, bool, dict]:
    """What the measurement says of the look's first guess (M2): it holds, or it was not it (and where it
    ranks), or the two cannot be compared (and why); whether the measured outline may take the guess's
    place (not while the measurement itself is in doubt: review flags); and the facts, for the log. In the
    user's words: ids go in the facts, not the sentence. Never silent, never assumed right."""
    from ...population import PLAIN_COLUMNS, column_of, member_at, review, select

    if guess is None:
        return "I couldn't check my first guess: it isn't outlined, so there was nothing to compare", False, {
            "verdict": "no-guess"}
    if pinned:
        return f"I couldn't check my first guess: {pinned}; both are kept", False, {
            "verdict": "not-comparable", "guess": guess.id, "why": pinned}
    same, why, iou = member_at(population, guess)
    facts = {"guess": guess.id, "iou": round(iou, 3)}
    if same is None:
        return f"Measured: my first guess is kept beside the measured one ({why})", False, {
            **facts, "verdict": "not-comparable", "why": why}
    facts["member"] = same.id
    rank, total = None, None
    column = column_of(args.get("by"))
    if column:
        ordered, _ = select(population, by=args.get("by"), order=str(args.get("order") or "desc"))
        rank = next((i for i, m in enumerate(ordered, start=1) if m.id == same.id), None)
        total = len(ordered)
    facts.update(rank=rank, of=total)
    flagged = dict(review(population))
    doubts = [flagged[m] for m in dict.fromkeys([same.id, *(m.id for m in chosen[:1])]) if m in flagged]
    doubt = f" (the measurement may be off: {'; '.join(doubts)}; both outlines are kept)" if doubts else ""
    facts["doubts"] = doubts
    where = ""
    if rank is not None:
        where = f"the {basis_words(args)}" if rank == 1 else f"the {_nth(rank)} of {total} by {PLAIN_COLUMNS.get(column, column)}"
    if same.id in {m.id for m in chosen}:
        facts["verdict"] = "holds"
        return "Measured: my first guess holds" + (f" (it is {where})" if where else "") + doubt, not doubts, facts
    facts["verdict"] = "not-it"
    return ("Measured: my first guess was not it" + (f" (it is {where})" if where else "") +
            ("; the measured one is outlined now" if not doubts else doubt)), not doubts, facts


async def redraw_with_choice(hub, obj, ctx: ToolContext, keep: bool = False):
    """An outline already on screen, highlighted by a request that chose a model for outlining (`use`) that did not
    draw it: drawn again with the choice where it is now (ticket.reusable; the user's Q17, 2026-10-11: "outline it
    with SAM 2.1" on an existing outline drew nothing new). The app's earlier outline is replaced unless `keep` (the
    user says also, or compare); the user's own stays beside it. None when it may stand as it is."""
    from ... import tools as app_tools
    from ...agent import _box_in
    from ...ticket import reusable
    from ...tools import ToolError

    if obj is None or obj.kind != "highlight" or obj.trial is not None or reusable("outline", obj.made_by):
        return None
    info = hub.latest_info
    if info is None or obj.anchor_epoch != info.epoch or obj.track != "tracking" or not getattr(obj, "shown", True):
        raise ToolError(f"the {obj.label or 'outline'} can't be drawn again with the model chosen: its place on the view "
                        "now is not checked", next="hold the slide still for a second, then ask again")
    x0, y0, x1, y1 = box = _box_in(obj, info)
    again = await app_tools.outline_at(hub, info.id, (x0 + x1) / 2, (y0 + y1) / 2, box=box,
                                       source=obj.source if str(obj.source).startswith("ai") else "ai:redraw",
                                       label=obj.label, status=obj.status, confidence=obj.confidence,
                                       naming=dict(obj.naming) if obj.naming else None)
    ctx.aliases[obj.id] = again.id  # "that" and the old id mean the new outline in the steps after this one
    # The app's earlier outline is replaced; never the user's own, nor one this request made (its earlier step drew
    # it with another model: "outline it with SAM 2.1, then with micro-sam" keeps both).
    if not keep and str(obj.source).startswith("ai") and obj.id in hub.scene.objects and obj.id not in ctx.objects:
        from ...ticket import current

        request = current()  # gated, as replace_earlier: only while this request's ticket stands (ADR 0010)
        if request is None or not request.superseded("outline:app"):
            hub.scene.remove(obj.id)
    return again


def add_highlight(registry: Registry, hub, sight, outline_seen, magnifier=None, log=None) -> None:
    def replace_earlier(made: list, ctx: ToolContext, args: dict) -> None:
        if not (args.get("keep")):
            # A new highlight replaces the app's earlier ones ("highlight the cortex" after the
            # xylem means the cortex now), unless the user says "also"; their own outlines stay.
            # Requests still running from before this one may no longer add theirs (ticket.py).
            from ...ticket import current

            request = current()
            if request is None or not request.superseded("outline:app"):
                hub.scene.generations.bump("outline:app", why="a newer request replaced the app's outlines", by=request)
                keep = {o.id for o in made} | set(ctx.objects)
                for o in list(hub.scene.objects.values()):
                    if o.kind == "highlight" and o.trial is None and o.source.startswith("ai") and o.id not in keep:
                        hub.scene.remove(o.id)
        for o in made:
            if o.id not in ctx.objects:
                ctx.objects.append(o.id)
        from ..registry import CURRENT_RUN, record_made
        from .common import record_outline

        if CURRENT_RUN.get() is not None:
            for o in made:
                if o.trial is not None:
                    continue  # unvoted arena arms retain the existing receipt privacy boundary
                record_outline(ctx, o)
                record_made(ctx, o.id)

    async def members(ctx: ToolContext, args: dict) -> str:
        if not args.get("checks"):
            return await _members(ctx, args)
        try:
            return await _members(ctx, args)
        except ToolFailed as exc:  # never a guess left unchecked without saying so (M2.1 review S1)
            note_guess(hub, first_guess_of(hub, args["checks"], ctx), f"not checked: {exc}")
            raise ToolFailed(f"I couldn't check my first guess: {exc}",
                             next="ask me to outline every cell again, then ask again") from None

    async def _members(ctx: ToolContext, args: dict) -> str:
        """Members of a population (contract 9): chosen by what was measured, outlined from their own
        masks; those verified in place on the live view are drawn there, the rest are shown on the
        view they were made on (the magnifier), never at an unchecked place (contract 3)."""
        import time as _time

        from ...population import MAX_HIGHLIGHT, captured_picture, past_bound_first, promote, resolve, select

        # The first guess this measurement checks (M2: "which is the biggest cell?" is answered by eye
        # at once, then measured): this request's own target, resolved now, before anything is outlined
        # or replaced; whether this request's look made it, and its revision now (a name given while
        # the cells are measured makes it the user's).
        guess = first_guess_of(hub, args["checks"], ctx) if args.get("checks") else None
        made_here = made_by_this_request(guess)
        if guess is not None and made_here and guess.status != "confirmed" and not (guess.note or "").startswith("a first guess"):
            # Marked a guess on every path (the fast pass and hands too: M2.1 re-check SF3), as the look's are.
            from ..loop import mark_first_guesses

            await mark_first_guesses(hub, [1], ToolContext(ctx.channel, ctx.turn_id, ctx.frame_id, targets=[guess.id]))
        guess_revision = guess.revision if guess is not None else None
        unchecked = "; my first guess was not checked (no cells were chosen to compare it with)" if args.get("checks") else ""
        population = resolve(hub, args.get("of"), ctx)
        if population is None:
            from ...population import nothing_here

            raise ToolFailed(nothing_here(hub), next="outline the cells first")
        if args.get("rest") and not any(args.get(k) for k in ("members", "by", "top", "where", "outliers")):
            raise ToolFailed("which cells to keep? fading or hiding the rest needs a choice of some",
                             next="say which: the largest five, the most elongated, or click them")
        if not any(args.get(k) for k in ("members", "by", "top", "where", "outliers")):
            # The whole population, no selection ("pull up the cells from earlier"): its layer, not up to 15 new
            # outlines (acceptance flow 5 on footage: fifteen "Outlined" labels and "125 more fit").
            from types import SimpleNamespace

            from .present import act

            return await act(SimpleNamespace(hub=hub, magnifier=magnifier, presenter=None, slidemap=None),
                             {"what": population.id, "do": "show"}, ctx=ctx) + unchecked
        if args.get("rest"):
            # "Keep the five largest and fade the rest" is the cells' layer narrowed to a selection, not new
            # outlines (acceptance flow 9 on footage: `rest` was dropped and "fading the rest" never happened).
            from types import SimpleNamespace

            from .present import act

            keys = ("members", "by", "order", "top", "where", "outliers", "rest")
            return await act(SimpleNamespace(hub=hub, magnifier=magnifier, presenter=None, slidemap=None),
                             {"what": population.id, "do": "emphasise", **{k: args[k] for k in keys if args.get(k) is not None}},
                             ctx=ctx) + unchecked
        notes: list[str] = []  # a colour pick's facts: the other colours drawn, those not drawn now (G3 review, SF1)
        chosen, how = select(population, ids=args.get("members"), where=args.get("where"), by=args.get("by"),
                             order=str(args.get("order") or "desc"), top=args.get("top"), outliers=args.get("outliers"),
                             hub=hub, notes=notes)
        if not chosen:  # with the measured values nearest the range asked for, so the next try can be judged
            raise ToolFailed(f"none of {population.title} fits that ({how or 'nothing chosen'})" + "".join(f"; {n}" for n in notes),
                             next="ask for the largest or the most elongated ones instead")
        more = len(chosen) - MAX_HIGHLIGHT
        if more > 0 and not args.get("by"):  # the ones outlined are those that fit the range best, said
            chosen, first = past_bound_first(population, chosen, args.get("where"), hub)
            how = ", ".join(h for h in (how, first) if h)
        label = str(args.get("label") or "")[:40]
        verdict, replaces, facts = "", False, {}
        if args.get("checks"):
            # Checked against every chosen cell, before any are cut to MAX_HIGHLIGHT. Only cells measured
            # for this request (its "@n") or after the guess count: older ones may be another view (review S2).
            pinned = ""
            if guess is not None and not str(args.get("of") or "").startswith("@") and population.made_at < guess.created_at:
                when = _time.strftime("%H:%M:%S", _time.localtime(population.view.get("at") or population.made_at))
                pinned = f"these cells were measured at {when}, before it, so the view may not be the same"
            verdict, replaces, facts = guess_verdict(population, chosen, guess, args, pinned)
        chosen = chosen[:MAX_HIGHLIGHT]
        holds = facts.get("verdict") == "holds"
        # The guess's name goes only to the cell it is: on "holds" the measured outline carries its name, how
        # sure and how it was named; otherwise the measured basis names it ("largest by area"; review B1).
        guess_name = (guess.label or "").rstrip("?").strip().lower() if guess is not None else ""
        if args.get("checks") and label and guess_name and label.rstrip("?").strip().lower() == guess_name and not holds:
            label = ""  # the guess's name is never written onto another cell, even when the model passes it (re-check SF5)
        basis_label = False
        guess_label = ""  # the guess's own name, given only to its own cell's outline (re-check 2, SF1)
        if args.get("checks") and not label:
            if holds and guess.label and made_here:
                guess_label = guess.label.rstrip("?").strip()[:40]
            label = basis_words(args) if int(args.get("top") or 0) == 1 else ""
            basis_label = bool(label) and label == basis_words(args)
        # Only a guess this request's look made, still as it made it, is taken off (review B2): one it reused,
        # the user's, or one named or confirmed while the cells were measured stays, and that is said.
        user_named = guess is not None and (guess.status == "confirmed" or guess.revision != guess_revision
                                            or named_since(guess))
        removable = guess is not None and made_here and not user_named
        member = facts.get("member")
        # A guess that holds and is the user's, or older than this request, is that cell's outline already: the
        # cell is not outlined twice, and no name or "Confirmed" is ever copied from it (re-check B-new-1).
        skip = [member] if holds and not removable and member else []
        made = await promote(hub, population, [m.id for m in chosen if m.id not in skip], why=how, label=label)
        if skip:
            made = [guess, *made]
        measured = next((o for o in made if (o.made_by or {}).get("member") == member), None)
        for o in made:  # "largest by area" says how it was chosen: not a name, so never kept as one (seen.py)
            if basis_label and o is not guess and (o.naming or {}).get("by") == "request":
                hub.scene.update(o.id, naming={**o.naming, "basisLabel": True})
        # Never "Confirmed" from the app: a guess the user confirmed while the cells were outlined is theirs (pinned).
        if holds and removable and measured is not None and measured is not guess and guess.status != "confirmed":
            hub.scene.update(measured.id, label=guess_label or measured.label, status=guess.status,
                             confidence=guess.confidence, maybe=list(guess.maybe),
                             **({"naming": dict(guess.naming)} if guess.naming else {}))
        replace_earlier(made, ctx, args)
        kept_why = ""
        if replaces and guess.id in hub.scene.objects and guess.id not in {o.id for o in made}:
            # Named or confirmed while the cells were outlined? Asked again now, with nothing awaited before the
            # removal (re-check SF1).
            user_named = guess.status == "confirmed" or guess.revision != guess_revision or named_since(guess)
            removable = made_here and not user_named
            if not removable:
                kept_why = ("you named it" if user_named else "it was outlined before this request")
            elif holds and not getattr(measured, "shown", False):
                kept_why = "the measured outline is not shown yet"
            else:
                hub.scene.remove(guess.id)  # the measured outline takes the guess's place (or corrects it)
                facts["removed"] = guess.id
        if kept_why:
            verdict += f"; my first guess's outline is kept as it was ({kept_why})"
        if guess is not None and guess.id in hub.scene.objects:  # no longer "being measured" (re-check SF4)
            note_guess(hub, guess, {"holds": "measured: it holds", "not-it": "measured: it was not it"}.get(
                facts.get("verdict"), f"not checked: {facts.get('why') or 'they could not be compared'}"))
        if member and guess is not None:
            from ...evidence import evidence_of

            evidence_of(hub).link(population, "same-object", member, guess.id,
                                  f"overlap with the first guess (box IoU {facts.get('iou', 0):.2f})", confidence=facts.get("iou", 0.0))
        if args.get("checks"):
            from ..intent import log_override
            from ..registry import attach

            facts.update(execution=(population.made_by or {}).get("execution"), population=population.id, kept=kept_why or None)
            attach(values={"firstGuess": facts})
            log_override(log, "first-guess", **facts)  # code decides what stays on screen: measured, tunable (GUESS_IOU)
        words = f"{population.title} ({population.id}), {how}: " + outlined_words(made).replace("was outlined but is not shown yet", "is not where the live view can show it")
        words += "".join(f"; {n}" for n in notes)
        if verdict:
            words = f"{verdict}; {words}"
        hidden = [o for o in made if not getattr(o, "shown", True)]
        if hidden and magnifier is not None:
            ids = [(o.made_by or {}).get("member") for o in hidden]
            jpeg = captured_picture(hub, population, [m for m in ids if m])
            if jpeg is not None:
                when = _time.strftime("%H:%M:%S", _time.localtime(population.view.get("at") or population.made_at))
                from ...screen import picture_shown

                try:
                    await picture_shown(magnifier, jpeg, f"On the view at {when} (not the live view): {', '.join(m for m in ids if m)}")
                    words += f"; shown on the view they were made on ({when}) in the magnifier"
                except ToolFailed as exc:  # the magnifier was closed since: the outlines stand, the picture is not shown
                    words += f"; their view at {when} was {exc.said()}"
        if more > 0:
            words += f"; {more} more fit, not outlined (at most {MAX_HIGHLIGHT})"
        from types import SimpleNamespace

        from .present import reshow_outlines

        return words + await reshow_outlines(SimpleNamespace(hub=hub), ctx)  # never "outlined" while hidden

    async def highlight(ctx: ToolContext, args: dict) -> str:
        things_given = bool(args.get("things") or args.get("thing"))
        if any(args.get(k) for k in SELECTORS if k != "rest") or (args.get("rest") and not things_given):
            return await members(ctx, args)
        requested = args.get("things")
        if requested is None or requested == "" or requested == []:
            requested = args.get("thing", [])
        if isinstance(requested, str):
            requested = [requested]
        if not isinstance(requested, list) or any(not isinstance(ref, str) for ref in requested):
            raise ToolFailed("highlight targets must be a target reference or a list of target references", next=None)  # no next: the planner must correct malformed arguments
        wanted = [ref for ref in requested if ref.strip()]
        if not wanted:
            raise ToolFailed("highlight what? seen ids (things), or members of a population (of, top, by, members...)",
                             next=None)  # no next: the model's own call; it corrects it
        from ..actions import SEEN_ID
        from .common import visible_object
        from ...tools import ToolError

        made, why, nexts, redrawn = [], [], [], []
        for ref in wanted[:15]:
            try:
                if SEEN_ID.fullmatch(ref.strip()):
                    oid = await outline_seen(ref, ctx)
                else:
                    # Request targets, aliases and named outlines share the same
                    # resolver as zoom/select. They are not older Sight targets.
                    oid = visible_object(hub, ref, ctx).id
                    again = await redraw_with_choice(hub, hub.scene.objects.get(oid), ctx, keep=bool(args.get("keep")))
                    if again is not None:
                        redrawn.append(again)
                        oid = again.id
            except (ToolError, ToolFailed) as exc:
                why.append(str(exc))
                nexts.append(exc.next)
                continue
            if oid and oid in hub.scene.objects and all(o.id != oid for o in made):
                # A name the user gave or confirmed is never written over by the app (invariant 14).
                if args.get("label") and hub.scene.objects[oid].status != "confirmed":
                    hub.scene.update(oid, label=str(args["label"])[:40])
                made.append(hub.scene.objects[oid])
        if not made:  # a highlight that misses makes the app look again (Brain), so the reason's own next step, if any
            raise ToolFailed(why[0] if why else "I could not outline those",
                             next=nexts[0] if nexts else None)  # no next: the app looks again on its own
        replace_earlier(made, ctx, args)
        from types import SimpleNamespace

        from .present import reshow_outlines

        # Fading the rest is for the cells' layer; outlined things are highlighted and that is said (re-check SF3).
        rest = "; nothing else was faded (keeping some cells and fading the rest is for the cells' layer)" if args.get("rest") else ""
        if args.get("checks"):  # a guess is checked only against measured cells (M2.1 review S1)
            rest += "; my first guess was not checked (only measured cells can check it)"
        if why:
            rest += "; some requested outlines could not be shown: " + "; ".join(why)
        if redrawn:  # which model drew which (a choice per step): said, from what drew it
            from ...names import said_impl

            rest += "; " + "; ".join(f"the {o.label or 'outline'} drawn again with "
                                     f"{said_impl((o.made_by or {}).get('implementation')) or 'the model chosen'}, the model "
                                     "chosen for this request" for o in redrawn)
        return outlined_words(made) + await reshow_outlines(SimpleNamespace(hub=hub), ctx) + rest  # never "outlined" while hidden

    registry.add(Tool("highlight", "Outline things you have already seen in the view (ids s1, s2... from your running "
                      "look) without looking again; fastest way to point at them. Or members of a population "
                      "(what outline_cells or find-all made): by id (members), or chosen by what was measured (by, "
                      "order, top, where, outliers), from the one shown or another (of: e3, or \"@2\" for what step 2 "
                      "of this request made); \"the five largest cells\" is one call. It replaces your earlier "
                      "highlights unless keep is true (the user says also / too / as well).",
                      schema({"things": {"type": "array", "items": {"type": "string"}, "description": "seen ids like s2, outline ids, or this request's target N"},
                              "of": {"type": "string", "description": 'a population (e3), "@2" for what step 2 made, empty for the one shown'},
                              "members": {"type": "array", "items": {"type": "string"}, "description": "member ids like c12"},
                              "by": {"type": "string", "description": "a measured column: area, length, width, elongation, roundness, solidity, intensity, score"},
                              "order": {"type": "string", "description": '"desc" (default, largest first) or "asc"'},
                              "top": {"type": "integer", "description": "only the first n"},
                              "where": {"type": "object", "description": 'measured ranges, e.g. {"elongation": [2.5, null]}, '
                                                                         'or {"colour": "red"}: as drawn now. ' + ROUNDNESS},
                              "outliers": {"type": "string", "description": "members far from the median of this column"},
                              "rest": {"type": "string", "description": 'keep these on the cells\' layer and "fade" or "hide" the rest'},
                              "checks": {"type": "string", "description": 'your target this measurement checks ("target 1"): call it a first guess in the answer, measure in the same answer'},
                              "label": {"type": "string", "description": "optional name to show"},
                              "keep": {"type": "boolean", "description": "keep earlier highlights too"}}),
                      highlight, inline_ms=1500, family="scene"))


def shown_words(magnifier, scene) -> str:
    """What the magnifier shows, in words, so a receipt that closes it says what was closed (live 2026-10-11: "closed
    the magnifier" closed a dissected view the user asked to keep, and nothing in the receipt said so)."""
    try:
        showing = magnifier.sources[magnifier.index] if magnifier.sources else {"kind": "live"}  # its picture's description, never the frame ring
    except (AttributeError, IndexError, TypeError):
        return ""
    obj = scene.objects.get(magnifier.object_id) if scene is not None and getattr(magnifier, "object_id", None) else None
    name = (obj.label or "the outlined object") if obj is not None else ""
    kind = showing.get("kind")
    if kind == "image":
        return f"a picture: {str(showing.get('label') or 'a still picture')[:80]}"
    what = {"live": "a live close-up", "held": "a still close-up"}.get(kind, "a close-up")
    parts = [o.label for o in scene.objects.values() if getattr(o, "parent", None) == obj.id] if obj is not None else []
    return (f"{what} of the {name}" if name else what) + (f", with its dissected parts ({', '.join(parts[:4])})" if parts else "")


def add_close_magnifier(registry: Registry, magnifier, services=None) -> None:
    async def close_magnifier(ctx: ToolContext, args: dict) -> str:
        if not magnifier.open:
            # The magnifier is its own kind: with no panel open, a live magnification window (screen.live_magnifications),
            # never the window in front of another kind; with none open, that is said (T2, live 2026-10-11 16:36:38).
            if services is not None and getattr(services, "hub", None) is not None:
                from ...screen import close_live_magnification

                return await close_live_magnification(services, ctx)
            return ""  # nothing to do, and nothing to show in the chat
        from ...screen import on_screen, open_state, screen_of

        hub = getattr(magnifier, "hub", None)
        before = screen_of(hub).reported_at if hub is not None else 0.0
        showed = shown_words(magnifier, getattr(hub, "scene", None))
        magnifier.close()
        if hub is not None:  # done only once the studio shows it closed (Amendment 2, point 4)
            await on_screen(hub, open_state("magnifier", False), before, "the magnifier is not closed", "close it with its ×")
        return "closed the magnifier" + (f" (it showed {showed})" if showed else "")

    registry.add(Tool("close_magnifier", "Close the magnifier: its panel, else the live magnification window in front "
                      "(other w- windows close with present close).", schema({}), close_magnifier, inline_ms=100,
                      family="magnifier"))


def add_result_tools(registry: Registry, manager, desk, presenter, hub=None, day=None, services=None) -> None:
    def view_for(ctx):
        return presenter.for_client(ctx.client) if ctx.client is not None else presenter

    def desk_for(ctx):
        if ctx.client is None:
            return desk
        from ...research.desk import Desk
        view = view_for(ctx)
        return Desk(manager, shown=lambda: view.rid if view.open else None)

    def find(ref, ctx):
        try:
            return desk_for(ctx).find(ref)
        except ResearchRefused as exc:
            raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on

    async def in_reader(ctx, s, selected) -> str | None:
        """A researcher's work where the studio has the Data reader: its Data entry opened there, on the user's Tab, as
        its row opens (ticket 66, D6; present.open_entry). None without the reader, or for an item Data does not list
        as itself (a reference photo, an older version): it opens as before."""
        import asyncio

        from ...data_reader import reader_studio, work_entry
        from ...resources import resources_of
        from ...screen import Screen
        from .present import open_entry

        screen = getattr(hub, "screen", None) if hub is not None else None
        eid = work_entry(s, selected)
        if services is None or not isinstance(screen, Screen) or eid is None:
            return None
        index = resources_of(hub)
        if reader_studio(screen, ctx, index.session()) is None:
            return None
        entry = await asyncio.to_thread(index.get, eid, index.session())
        if entry is None:
            return None
        return await open_entry(services, entry.id, screen, "brain", ctx=ctx, entry=entry)

    async def show_result(ctx: ToolContext, args: dict) -> str:
        bound = isinstance(args.get("researcher"), dict)
        reference = None
        if bound:
            research_request_current(ctx, hub, day)
            s, _attempt, reference = bound_researcher(ctx, args["researcher"], manager, desk_for(ctx))
            if reference["kind"] == "research-run":
                if not _attempt.completion.done() or _attempt.completion.cancelled():
                    raise ToolFailed("the original researcher has not completed its work yet", next=None)  # no next: showing work does not implicitly wait or substitute another run
                try:
                    reference = manager.result_ref(s, _attempt, _attempt.completion.result())
                    s, _attempt = manager.resolve_attempt(reference)
                except ResearchRefused as exc:
                    raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on from exact completion validation
        if args.get("step") is not None:
            if bound:
                raise ToolFailed("a bound completed result cannot be replaced by a current lesson-step lookup", next=None)  # no next: a latest lesson lookup would replace the explicitly bound original work
            opened = await result_step(ctx, {"researcher": args.get("researcher"), "to": args["step"]})
            return opened.split("\n", 1)[0].rstrip(":")  # where it is open; the question and answer are result_step's
        if bound:
            outcome = _attempt.completion.result()
            if outcome.get("mode") == "answer":
                raise ToolFailed("the original follow-up reply can be read, but has no exact result-viewer presentation", next=None)  # no next: the existing viewer cannot display this exact reply; its read capability is stated in the refusal
        elif args.get("researcher"):
            s = find(args["researcher"], ctx)
        else:
            done = [s for s in manager.researchers() if s.outputs or s.result]
            if not done:
                raise ToolFailed("no researcher has anything to show yet", next="ask me to look into something first")
            s = max(done, key=lambda x: x.finished or x.created)
        if not (s.outputs or s.result):
            raise ToolFailed(f"{s.name} has nothing to show yet",
                             next="wait until it has finished: its work then opens beside the view")
        n = args.get("item")
        from ...screen import on_screen, open_state, screen_of

        before = screen_of(hub).reported_at if hub is not None else 0.0
        index = int(n) - 1 if isinstance(n, (int, float)) and n >= 1 else None
        if bound:
            from ...presenter import first_shown, items
            listed = items(s)
            original_outputs = {(output["id"], output["version"]) for output in reference["outputs"]}
            eligible = [i for i, item in enumerate(listed)
                        if (item.get("id"), item.get("version")) in original_outputs
                        or (item.get("kind") == "summary" and outcome.get("result") is not None
                            and manager.result_hash(s.result) == reference["resultSha256"])]
            if not eligible or (index is not None and index not in eligible):
                raise ToolFailed("the original completed work has no matching result-viewer item", next=None)  # no next: another item cannot substitute for the original completion or explicitly selected item
            if index is None:
                preferred = first_shown(s)
                index = preferred if preferred in eligible else eligible[0]
        from ...presenter import first_shown, items
        listed = items(s)
        chosen_index = index if index is not None else first_shown(s)
        if not 0 <= chosen_index < len(listed):
            raise ToolFailed("that result item does not exist", next="choose one of the retained results")
        selected = listed[chosen_index]
        opened = await in_reader(ctx, s, selected)
        if opened is not None:
            if bound:
                research_request_current(ctx, hub, day)
                manager.resolve_attempt(reference)
            return opened
        if (services is not None and services.presentations is not None and ctx.client
                and selected is not None and selected['kind'] in ('summary', 'image', 'presentation')):
            from .present import _native_present
            images = [o for o in s.current_outputs() if o.get('kind') == 'image']
            picture = next((i for i, o in enumerate(images) if o['id'] == selected.get('id')), None)
            source = {'day': s.day, 'id': s.rid, 'version': selected.get('version'), 'picture': picture}
            if selected['kind'] == 'presentation':
                source['outputId'] = selected['id']
            if selected['kind'] == 'image':
                source['outputId'] = selected['id']
                if picture is None:
                    # An old output still needs an explicit ordinal as well as its immutable output ID.
                    source['picture'] = 0
            result = await _native_present(services, ctx, {'do': 'open', 'source': source,
                'title': f'{s.name}: {selected.get("caption") or "work"}'[:120]})
            if bound:
                research_request_current(ctx, hub, day)
                manager.resolve_attempt(reference)
            return result
        if ctx.client is not None or ctx.request_snapshot is not None:
            from .present import _original_screen
            screen = screen_of(hub)
            scope = _original_screen(screen, ctx)
            # Confirm the original browser's structural viewer, never a peer's
            # global/latest report. Exact item/version was selected above.
            what = presenter.for_client(ctx.client).show(s.rid, index)
            change = screen.send('viewer', 'entry', 'open', {'open': True, 'shows': s.rid}, 'brain', **scope)
            def selected_ready(report):
                item = next((item for item in report.get('items', []) if item.get('id') == 'viewer'), None)
                return (item is not None and bool(item.get('open')) and item.get('shows') == s.rid
                        and item.get('work') == selected.get('work') and item.get('work') is not None
                        and item.get('workReady') is True)
            if not await screen.confirm(change, selected_ready):
                raise ToolFailed(f"{s.name}'s selected work has not loaded in the requesting studio", next="open it from Researchers")
        else:
            what = presenter.show(s.rid, index)
            if hub is not None:
                await on_screen(hub, open_state("viewer", True, s.rid), before, f"{s.name}'s work is not open", "open it from Researchers")
        if bound:
            research_request_current(ctx, hub, day)
            try:
                manager.resolve_attempt(reference)
            except ResearchRefused as exc:
                raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        return f"showing {what} beside the live view"

    registry.add(Tool("show_result", "Open a researcher's work (lesson, video, picture or written result) in the reader "
                      "on the user's Tab. Without a researcher: the latest finished one. "
                      "\"Show me what it found\" is show_me.",
                      schema({"researcher": RESEARCH_REFERENCE, "item": {"type": "integer", "description": "which of its results, 1-based"},
                              "step": {"type": "string", "description": "open a lesson on this step: next, q2, 3..."}}),
                      show_result, inline_ms=1500, family="results"))

    async def read_result(ctx: ToolContext, args: dict) -> str:
        bound = isinstance(args.get("researcher"), dict)
        if bound:
            research_request_current(ctx, hub, day)
        s, _attempt, reference = bound_researcher(ctx, args.get("researcher"), manager, desk_for(ctx))
        if bound:
            try:
                s = manager.result_view(reference)
            except ResearchRefused as exc:
                raise ToolFailed(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        from ...research.selection import view as selected_research_view
        s = selected_research_view(s, ctx.research_selection)
        text = s.readable(str(args.get("part") or "").strip() or None, limit=None)
        if not text.strip():
            return f"{s.name} has nothing written yet ({s.status})"
        # Read in pages of the whole text, each naming the text's revision: a later page of a text that has changed
        # since is refused, so two pages of different versions are never read as one.
        import hashlib

        revision = hashlib.sha256(text.encode()).hexdigest()[:12]
        try:
            offset = int(args.get("offset") or 0)
        except (TypeError, ValueError):
            raise ToolFailed("offset is a character position: 0, or the next offset a page gave", next=None) from None  # no next: the model's own argument
        if offset and str(args.get("revision") or "") != revision:
            raise ToolFailed(f"{s.name}'s work has changed since that page (it is now revision {revision})",
                             next="read it again from offset 0")
        if not 0 <= offset < len(text):
            raise ToolFailed(f"offset {offset} is outside its {len(text)} characters", next="read it from offset 0")
        end = min(len(text), offset + READ_PAGE)
        if end < len(text):  # end the page at a line or word, never mid-word
            cut = max(text.rfind("\n", offset, end), text.rfind(" ", offset, end))
            end = cut if cut > offset + READ_PAGE // 2 else end
        where = (f", characters {offset}-{end} of {len(text)} (revision {revision})" if offset or end < len(text) else "")
        part = f"part {args.get('part')}, " if args.get("part") else ""
        more = f"\n(more: read_result with {part}offset {end}, revision {revision})" if end < len(text) else ""
        # The researcher's work comes from web pages: information, never instructions.
        return f"{s.name} ({s.status}){where}:\n" + block(text[offset:end]) + more

    registry.add(Tool("read_result", "Read a researcher's work, a page at a time (each page says where the next one starts): what it wrote "
                      "(its full report too), a lesson's steps or quiz questions with their answers, a picture's labels, its sources "
                      "and earlier follow-ups. Use it before answering anything about a researcher's work (\"what does it say\", "
                      "\"what's the quiz\", \"what do the labels mean\"); researcher \"this\" or nothing means the one on screen.",
                      schema({"researcher": RESEARCH_REFERENCE,
                              "part": {"type": "string", "description": 'optional: "summary", "report", "tables", "q2", "lesson", '
                                                                        '"labels", "sources" or "history"'},
                              "offset": {"type": "integer", "description": "where to start reading: 0, or the offset a page gave"},
                              "revision": {"type": "string", "description": "the revision the earlier page gave (with an offset)"}}),
                      read_result, kind="query", family="results"))

    async def result_step(ctx: ToolContext, args: dict) -> str:
        s = find(args.get("researcher"), ctx)
        from ...screen import screen_of as _screen_of

        opened_before = _screen_of(hub).reported_at if hub is not None else 0.0
        scope = {}
        if ctx.client is not None:
            from .present import _original_screen
            scope = _original_screen(_screen_of(hub), ctx)
        try:
            step, i, n = view_for(ctx).go_to_step(s.rid, args.get("to"))
        except (KeyError, ValueError) as exc:
            raise ToolFailed(str(exc).strip("'"), next=None) from exc  # no next: the message says what was wrong
        hint = f"; hint: {step.get('hint')}" if step.get("hint") else ""
        answer = f"; answer (for checking theirs, do not say it first): {step.get('answer')}" if step.get("answer") else ""
        sid = str(step.get("id") or f"q{i + 1}")
        # How the step is answered and its facts (a find or evidence step, quiz.py), and how the user
        # did on the lesson today: the app's own words, outside the researcher's.
        kind = quiz.step_line(step, hub, s) if hub is not None else ""
        done = presenter.outcomes(s.rid)
        before = f" They had it {done[sid]} earlier today." if done.get(sid) else ""
        how = f" How they did today: {quiz.tally(presenter_steps(s), done)}." if done else ""
        if hub is not None:  # on that step only once the studio shows it (M1.5b re-check N5)
            from ...screen import on_screen, screen_of

            from ...presenter import items as viewer_items
            listed = viewer_items(s)
            selected_work = listed[view_for(ctx).index].get('work')

            def on_step(report):
                item = next((x for x in report.get("items", []) if x.get("id") == "viewer"), None)
                return (item is not None and bool(item.get("open")) and item.get("shows") == s.rid
                        and item.get("step") == sid and (ctx.client is None or
                            selected_work is not None and item.get("work") == selected_work and item.get("workReady") is True))

            if ctx.client is not None:
                from .present import _original_screen
                screen = screen_of(hub)
                scope = _original_screen(screen, ctx)
                change = screen.send('viewer', 'entry', 'open', {'open': True, 'shows': s.rid, 'step': sid}, 'brain', **scope)
                if not await screen.confirm(change, on_step):
                    raise ToolFailed('The requested lesson step is not shown in the requesting studio', next='open it from Researchers')
            else:
                await on_screen(hub, on_step, opened_before, f"{s.name}'s lesson is not open on step {i + 1}", "open it from Researchers")
        return (f"{s.name}'s lesson is open on step {i + 1} of {n} ({sid}):\n"
                + block(f"{step.get('prompt') or ''}{hint}{answer}")
                + "".join(f"\n{t.strip()}" for t in (kind, before + how) if t.strip()))

    registry.add(Tool("result_step", "Go through a researcher's lesson or quiz one step at a time with the user: opens it on "
                      "a step (to: next, prev, again, q2, 3, or missed: the next one they got wrong or skipped, for \"go "
                      "over what I missed\") and gives you that step's question, hint and answer. Ask the question in your "
                      "own words and let the user try before giving the answer; on \"next question\" use to=next.",
                      schema({"researcher": RESEARCHER, "to": {"type": "string", "description": "next, prev, again, q2, 3, missed..."}}),
                      result_step, kind="query", family="results"))

    if day is not None:
        async def mark_step(ctx: ToolContext, args: dict) -> str:
            s = find(args.get("researcher"), ctx)
            outcome = str(args.get("outcome") or "").strip().lower()
            if outcome not in quiz.OUTCOMES:
                raise ToolFailed("say right, wrong or skipped", next=None)  # no next: the model's own call; it corrects it
            steps = presenter_steps(s)
            ids = [quiz.step_id(st, i) for i, st in enumerate(steps)]
            sid = str(args.get("step") or (view_for(ctx).step if view_for(ctx).rid == s.rid else "") or "").strip().lower()
            if sid.isdigit():
                sid = f"q{sid}"
            if sid not in ids:
                raise ToolFailed(f"{s.name}'s lesson has no step {sid or '(none open)'}; it has {', '.join(ids) or 'no steps'}",
                                 next=None)  # no next: the model's own call; it corrects it
            day.record_outcome(s.rid, sid, outcome, by="brain")  # the studio hears it from the session (app._session_event)
            return f"noted: {sid} {outcome} ({quiz.tally(steps, day.outcomes_of(s.rid))})"

        registry.add(Tool("mark_step", "Record how the user did on a lesson or quiz step once you have judged their answer: "
                          "right, wrong, or skipped (they moved on without answering). Kept for the day; result_step "
                          "to=missed goes over the wrong and skipped ones.",
                          schema({"researcher": RESEARCHER, "step": {"type": "string", "description": "q2 (default: the one open)"},
                                  "outcome": {"type": "string", "enum": list(quiz.OUTCOMES)}}, ["outcome"]),
                          mark_step, inline_ms=100, family="results"))

    def work_in_reader(ctx) -> dict | None:
        """The Data reader showing a researcher's work (its report, or `<rid>:<part>`), in the asking studio's report."""
        from ...data_reader import READER_SURFACES
        from ...screen import Screen
        from .present import _original_screen

        screen = getattr(hub, "screen", None)
        if not isinstance(screen, Screen):
            return None
        scope = _original_screen(screen, ctx)
        report = screen.request_report(**scope) if scope else screen.latest
        return next((item for item in (report or {}).get("items") or [] if item.get("id") in READER_SURFACES
                     and item.get("open") and manager.get(str(item.get("shows") or "").split(":", 1)[0]) is not None), None)

    async def close_result(ctx: ToolContext, args: dict) -> str:
        if not view_for(ctx).open:
            # Not open: the researcher's work in the Data reader (ticket 66, D6), else "close that" is the window in front
            # (screen.frontmost), as present close takes it; else nothing.
            if services is not None and hub is not None:
                from ...screen import close_frontmost
                from .present import reader_close

                shown = work_in_reader(ctx)
                if shown is not None:
                    return await reader_close(services, ctx, shown)
                return await close_frontmost(services, ctx, "the result viewer")
            return ""  # nothing to do, and nothing to show in the chat
        from ...screen import on_screen, open_state, screen_of

        before = screen_of(hub).reported_at if hub is not None else 0.0
        if ctx.client is not None:
            from .present import _original_screen
            _original_screen(screen_of(hub), ctx)
        view_for(ctx).close()
        if hub is not None:  # the video that stayed on screen after "closed": done only once the studio shows it
            if ctx.client is not None:
                from .present import _original_screen
                screen = screen_of(hub)
                scope = _original_screen(screen, ctx)
                change = screen.send('viewer', 'viewer', 'close', {'open': False}, 'brain', **scope)
                if not await screen.confirm(change, open_state('viewer', False)):
                    raise ToolFailed('The result viewer is not closed in the requesting studio', next='close it with its ×')
            else:
                await on_screen(hub, open_state("viewer", False), before, "the result viewer is not closed", "close it with its ×")
        return "closed it; it stays in the Researchers tab to reopen"

    registry.add(Tool("close_result", "Close the researcher's work on screen (it can be reopened).",
                      schema({}), close_result, inline_ms=100, family="results"))
