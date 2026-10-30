"""Choosing how something is done (contract 7 of docs/design/2026-10-06-captures-operations-evidence.md;
ADR 0011): for this request only (`use`), or from now on (`set_default`).

- `use(operation, implementation, policy)` checks the choice can run here before anything runs and puts it
  on the request's ticket. Everything the request dispatches keeps it (its later steps, its jobs, deep
  paths through options.overriding); nothing else sees it. An explicit choice runs `raw` (exactly that,
  or it fails) unless a route is asked for. Something already on screen that another model made is made
  again with the choice (ticket.reusable). A choice nothing in the request used is said.
- `set_default(operation, implementation)` changes the setting, as the Models menu does; work already
  dispatched keeps what it resolved.
- A name means what names.py says it means (an exact key: the id, the name, the name with where it runs, an old
  label, a family's name for its default variant). The tools take fixed lists of ids, told once as "id = name"
  (names.choices_text, on `use`). A name that means nothing is refused with the choices for the model to pick again
  within the request (PICK_AGAIN); a later `use` that works replaces the refusal (live 2026-10-11).

What can do something, without running anything, is the capabilities tool's `operation` (operations.
alternatives_text). Model choice is never the core path (Amendment 4): these are for when the user asks.
"""
from __future__ import annotations

import numpy as np

from ... import names
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema

OPERATION_WORDS = {"outline": "outlining", "cells": "segmenting every cell", "classify": "classifying each cell",
                   "find-like": "finding every one like it",
                   "track": "following outlined things", "link": "linking the cells of two views",
                   "looks": "describing how each cell looks", "groups": "grouping cells by look",
                   "light": "measuring the colour inside each cell", "names": "ranking names for each group",
                   "your-label": "matching cells to the groups you named",
                   "motion": "measuring what moves"}  # every operation (the M3.5 review; K1's and M3.2's too)
ASK_WHICH = "ask which models can do it"
# Where a comparison's table goes (placement.py's words a table takes: show_measurements'), when it is to be shown.
TABLE_PLACEMENTS = ("beside", "new-tab", "only", "replace", "window")
PLACEMENT_SCHEMA = {"type": "string", "enum": list(TABLE_PLACEMENTS),
                    "description": "where the comparison's table goes: beside (side by side in this Tab, the live view "
                                   "included), new-tab (a Tab of its own), only, replace or window; empty: kept in Data "
                                   "(classify: beside)"}


def turn_on(group: str, option_id: str) -> str:
    """How the user turns an option on, from its readiness (orchestrator/readiness.py), as the Models menu and `make
    doctor` say it: "; to turn it on: add your fal.ai key (put FAL_KEY=... in ~/Desktop/micromind.txt)"."""
    from ...readiness import option

    step = option(group, option_id).next
    if step is None or step.kind in ("edition", "install", "weights"):  # those say how in their own why already
        return ""
    # What the agent can do about it (brain/toolbox/catalog.py): offer the download on the user's yes, or open its card
    # where the user adds the key themselves.
    offer = {"download": "; the models tool offers the download, which starts on the user's yes",
             "key": "; open_models opens its card, where the user adds the key"}.get(step.kind, "")
    return f"; to turn it on: {step.says[0].lower()}{step.says[1:]}" + (
        f" ({step.how})" if step.how and step.how != step.says else "") + offer
# A model whose last check on this build failed (model_checks.py, Slice 8): refused with the check's words, never run.
CHECK_AGAIN = "pick another model, or have it checked again (Check now on the Capabilities page)"


def said_option(group: str, option) -> str:
    """An option's one name with where it runs (capabilities.said_option: "Gemini 3.5 Flash (cloud)")."""
    from ...capabilities import said_option as said

    return said(group, {"id": option.id, "label": option.label})


def broken_check(hub, role: str | None, option: str | None, name: str) -> None:
    """Refuse a model whose last check here, on this build, said it is broken: "<name> is broken here: <why>"."""
    from ...model_checks import refusal

    said = refusal(hub, role, option, name) if role and option else ""
    if said:
        raise ToolFailed(said, next=CHECK_AGAIN)
# A name that means nothing goes back to the model with the choices, within the request (live 2026-10-11: "ask which
# models can do it" sent the brain to the user after it had shortened a name itself).
PICK_AGAIN = "pick one of these and try again"
BY = ("user", "brain")  # who chose a request's model (`use`): its run's origin, user-chosen or brain-chosen


# The settings whose model can be named (names.SETTINGS), in words.
SETTING_WORDS = {"vision": "the vision model (looking and answering)", "fast": "the quick-decision model",
                 "outline_check": "the outline check", "research": "the researchers' model",
                 "image": "the researchers' picture model", "presentation": "the presentation model",
                 "sight": "the running look", "zeroshot": "the medical-names model for clicks",
                 "think": "the voice's thinking (its hard questions and many steps)"}
# What already ran for a request with a setting's model, by the request's answer phases (brain/admission.py): a
# choice made after it does not change it, and that is said (Slice 5).
RAN_BY_PHASE = {"vision": (("look", "look"), ("query", "look")), "fast": (("fast", "quick decision"),
                                                                                 ("hands", "quick decision"))}


def role_words(role: str) -> str:
    return OPERATION_WORDS.get(role) or SETTING_WORDS.get(role) or role


def kept_groups() -> tuple[str, ...]:
    """The settings set_default changes (defaults.kept_groups): its `sets`."""
    from ...defaults import kept_groups as groups

    return groups()


def log_choice(request, ctx: ToolContext, role: str, choice: dict) -> None:
    """Every choice a request makes, with the step that made it (the request's choice per step; the turn log)."""
    log = getattr(request, "choice_log", None)
    if isinstance(log, list):
        log.append({"step": getattr(ctx, "step", 0), "operation": role,
                    **{k: v for k, v in choice.items() if k in ("implementation", "policy", "origin", "refused", "label")}})


def nothing_after(ctx: ToolContext) -> str:
    """A fact for a choice that is its plan's last step: nothing has been drawn with it (live 2026-10-11, "use SAM 2.1
    for this" 0 of 3: the plan was `use` alone and the round after it, seeing only "uses SAM 2.1 tiny, from this
    step on", added no step). "" when a step follows, or when the plan's steps are not known (a direct call)."""
    steps = list(getattr(getattr(ctx, "steps", None), "tools", {}) or {})  # in the order they run (actions.py)
    here = getattr(ctx, "step", None)
    if here not in steps or steps.index(here) < len(steps) - 1:
        return ""
    return "; no step comes after it in this plan, so nothing has been done with it yet"


def already_ran(request, group: str) -> tuple[str, str] | None:
    """(what, its model's name) of the request's last call of `group`'s model that already ran, or None."""
    from ...options import VISION

    admission = getattr(request, "admission", None)
    phases = dict(RAN_BY_PHASE.get(group, ()))
    done = [a for a in (getattr(admission, "attempts", ()) or ()) if getattr(a, "phase", None) in phases]
    if not done:
        return None
    model = getattr(done[-1], "model", "") or ""
    return phases[done[-1].phase], (VISION[model].label if model in VISION else model or "the default")


def choosable(op, impl) -> bool:
    """Whether a request can be given `impl` for `op` (names.choosable)."""
    return names.choosable(op, impl)


def candidates(operation: str, name: str) -> list:
    """The implementation `name` means for `operation` (names.resolve: an exact key, never a part of a name), as a
    list of none or one, among those a request can be given."""
    from ...operations import OPERATIONS

    meaning = names.resolve(operation, name)
    op = OPERATIONS.get(operation)
    return [op.impl(meaning.id)] if meaning is not None and op is not None else []


def find_impl(operation: str, name: str):
    """The one implementation `name` means, or None."""
    found = candidates(operation, name)
    return found[0] if found else None


def unknown_name(op, name) -> ToolFailed:
    """A name that means nothing for `op`: said with every choice, "id = name", for the model to pick again, and the
    operation it does mean something for when there is one ("cellpose-sam" asked for outlining)."""
    elsewhere = [other.id for other in names.choice_operations() if other.id != op.id and names.resolve(other.id, name)]
    return ToolFailed(f"there is no {str(name or '')!r} for {OPERATION_WORDS[op.id]}"
                      + (f" (it is a choice for operation {' or '.join(elsewhere)})" if elsewhere else "")
                      + f"; the choices (id = name): {names.choices_line(op.id)}", next=PICK_AGAIN)


def choice_for(name) -> str:
    """Where a model named for something with no choice of model is one: "; DINO is a choice for looks", or "" (T4,
    live 2026-10-11 16:28:55: "use the dino model to dissect", and dissecting takes no model choice)."""
    if not str(name or "").strip():
        return ""
    found = [(role, meaning) for role, namespace in names.roles(request=True)
             if (meaning := names.resolve(namespace, name)) is not None]
    if not found:
        return ""
    said = found[0][1].family or found[0][1].name
    return f"; {said} is a choice for {' or '.join(dict.fromkeys(role for role, _ in found))}"


def pick_said(hub, op, name):
    """The implementation the user means for `op` and what the receipt says about the name ("" or, for a family named
    alone, which variant it is), or ToolFailed. A family's default that can't run here when another of the family can:
    the user is asked which (the user's 2026-10-11 decision Q18), never switched silently."""
    from ...operations import availability

    meaning = names.resolve(op.id, name)
    if meaning is None:
        raise unknown_name(op, name)
    impl = op.impl(meaning.id)
    if meaning.family and len(meaning.members) > 1:
        why = availability(hub, impl)
        runnable = [op.impl(m) for m in meaning.members if m != impl.id and not availability(hub, op.impl(m))]
        if why and runnable:
            others = " or ".join(i.label for i in runnable)
            raise ToolFailed(f"{meaning.family} named alone is {impl.label}, which can't run here ({why}); {others} can",
                             next=f"ask the user whether to use {others}")
    return impl, meaning.note()


def pick(hub, op, name):
    """The implementation the user means for `op`, or ToolFailed saying why and what to do (pick_said)."""
    return pick_said(hub, op, name)[0]


def impl_model(impl) -> str:
    """The catalog model an implementation runs (operations.LOAD_KEYS maps the few whose id is another use of it)."""
    from ...operations import LOAD_KEYS

    return LOAD_KEYS.get(impl.id, impl.id)


def add_choosing_tools(registry: Registry, services) -> None:
    def download_offer(model: str, why: str) -> str:
        """When what stops a model is its download: the download offered to the user (brain/toolbox/catalog.py)."""
        offer = getattr(services, "offer_download", None)
        if not model or offer is None or "not downloaded" not in why:
            return ""
        try:
            return ";" + offer(model)
        except Exception:  # noqa: BLE001 - the refusal stands without its offer
            return ""

    hub = services.hub

    def operation_of(args: dict, default: str | None = "outline"):
        from ...operations import OPERATIONS

        operation = str(args.get("operation") or default or "").strip().lower()
        if not operation:
            raise ToolFailed("say what the model is for: operation is one of "
                             + ", ".join(role for role, _ in names.roles(request=True)), next=PICK_AGAIN)
        named = choice_for(args.get("implementation"))  # where the model named is a choice, when it is one
        if operation in OPERATIONS and names.one_way(OPERATIONS[operation]):  # following, measuring motion: one way each
            only = OPERATIONS[operation].implementations[0]
            raise ToolFailed(f"there is no choice to make for {OPERATION_WORDS[operation]}: it is done one way, "
                             f"{only.label}{named}", next="ask about " + ", ".join(role_words(r) for r, _ in names.roles()))
        if operation not in OPERATIONS:
            raise ToolFailed(f"there is no {operation!r} to choose a model for{named}; operation is one of "
                             + ", ".join(role for role, _ in names.roles()), next=PICK_AGAIN)
        return OPERATIONS[operation]

    async def use(ctx: ToolContext, args: dict) -> str:
        from ... import ticket as tickets
        from ...operations import availability

        request = tickets.current()
        if request is None:
            raise ToolFailed("a choice is made for a request, and there is none here", next="ask for the work with the model named")
        role = str(args.get("operation") or "").strip().lower()
        if role in names.SETTINGS:
            return use_setting(ctx, request, role, args)
        op = operation_of(args, default=None)  # required: which operation the model is for (Slice 5)
        named = str(args.get("implementation") or "").strip()
        try:
            impl, said = pick_said(hub, op, named)
            why = availability(hub, impl)
            if why:
                runnable = [i.label for i in op.implementations if i is not impl and names.choosable(op, i)
                            and not availability(hub, i)]
                offer = download_offer(impl_model(impl), why)
                raise ToolFailed(f"{impl.label} can't run here ({why}){offer}" + (f"; these can: {', '.join(runnable)}" if runnable else ""),
                                 next="ask the user whether to download it" if offer else ASK_WHICH)
            broken_check(hub, op.selected_by, impl.option, names.said_impl(impl.id))
            policy = str(args.get("policy") or "raw").strip().lower()
            if policy not in ("raw", "route"):
                raise ToolFailed('the policy is "raw" (exactly that model) or "route" (with its declared fallbacks)',
                                 next='say "exactly that model" or "with its fallbacks"')
            by = str(args.get("by") or "").strip().lower()
            if by not in BY:
                # Who chose it is said, never assumed (2026-10-11: a choice the brain made was recorded as the user's).
                raise ToolFailed(f"say who chose the model: by is {' or '.join(BY)} (user: the user named it; brain: you "
                                 "chose it)", next=PICK_AGAIN)
        except ToolFailed as refused:
            # The work the choice was for is not done some other way: the request's runs of this operation
            # are refused with this reason (scaffolding review, M1.4 item 8; ADR 0011), until a `use` of it that
            # works replaces this (the model picks again within the request: PICK_AGAIN).
            request.choices[op.id] = {"implementation": None, "label": named or "that model", "refused": str(refused),
                                      "next": refused.next, "used": True}
            log_choice(request, ctx, op.id, request.choices[op.id])
            if op.selected_by:
                request.overrides.pop(op.selected_by, None)  # an earlier choice of it in this request no longer holds
            raise
        who = "user-chosen" if by == "user" else "brain-chosen"
        # In force for the steps after this one until the next `use` of this operation (a choice per step: "outline it
        # with SAM 2.1, then with micro-sam" draws it with each; actions.py runs a request's steps in order).
        request.choices[op.id] = {"implementation": impl.id, "policy": policy, "origin": who, "used": False,
                                  "label": impl.label}
        log_choice(request, ctx, op.id, request.choices[op.id])
        if impl.option and op.selected_by:
            request.overrides[op.selected_by] = impl.option  # settings reads in this request see it (options.overriding)
        how = "exactly that, with no fallback" if policy == "raw" else "with its declared fallbacks"
        return (f"for this request, {OPERATION_WORDS[op.id]} uses {impl.label}, {how}, from this step on"
                + (f" ({said})" if said else "") + nothing_after(ctx))

    def use_setting(ctx: ToolContext, request, group: str, args: dict) -> str:
        """A setting's model for this request only (the vision model, the quick decisions, the outline check,
        researchers' engine and pictures, the presentation model): every settings read made for the request sees it
        (options.overriding). What already ran for the request ran with the default, and that is said."""
        from ...options import GROUPS, unavailable

        named = str(args.get("implementation") or "").strip()
        try:
            if not names.SETTINGS[group]:
                raise ToolFailed(f"{role_words(group)} is not part of one request: it can be changed from now on (set_default)",
                                 next="say whether to change it from now on")
            meaning = names.option(group, named)
            if meaning is None:
                raise ToolFailed(f"there is no {named!r} for {role_words(group)}; the choices (id = name): "
                                 f"{names.choices_line('option:' + group)}", next=PICK_AGAIN)
            option = GROUPS[group][meaning.id]
            why = unavailable(group, option.id)
            if why:
                runnable = [o.label for o in GROUPS[group].values() if o is not option and not unavailable(group, o.id)]
                offer = download_offer(option.model or (option.also[0] if option.also else ""), why)
                raise ToolFailed(f"{option.label} can't run here ({why}){offer or turn_on(group, option.id)}"
                                 + (f"; these can: {', '.join(runnable)}" if runnable else ""),
                                 next="ask the user whether to download it" if offer else ASK_WHICH)
            broken_check(hub, group, option.id, said_option(group, option))
            by = str(args.get("by") or "").strip().lower()
            if by not in BY:
                raise ToolFailed(f"say who chose the model: by is {' or '.join(BY)} (user: the user named it; brain: you "
                                 "chose it)", next=PICK_AGAIN)
        except ToolFailed as refused:
            # Nothing else is chosen in its place: what follows in the request reads the default, and that is said.
            request.choices[group] = {"implementation": None, "group": group, "label": named or "that model",
                                      "refused": str(refused), "next": refused.next, "used": True}
            log_choice(request, ctx, group, request.choices[group])
            request.overrides.pop(group, None)
            raise
        request.choices[group] = {"implementation": option.id, "group": group,
                                  "origin": "user-chosen" if by == "user" else "brain-chosen", "used": True,
                                  "label": option.label}
        log_choice(request, ctx, group, request.choices[group])
        request.overrides[group] = option.id  # settings reads in this request see it (options.overriding)
        ran = already_ran(request, group)
        return (f"for this request, {role_words(group)} uses {option.label}, from this step on"
                + (f"; this request's {ran[0]} already ran with {ran[1]}, so it stays as it is" if ran else ""))

    def set_default_setting(group: str, args: dict, who: str) -> str:
        from ...options import GROUPS, unavailable

        meaning = names.option(group, args.get("implementation"))
        if meaning is None:
            raise ToolFailed(f"there is no {str(args.get('implementation') or '')!r} for {role_words(group)}; the choices "
                             f"(id = name): {names.choices_line('option:' + group)}", next=PICK_AGAIN)
        option = GROUPS[group][meaning.id]
        why = unavailable(group, option.id)
        if why:
            offer = download_offer(option.model or (option.also[0] if option.also else ""), why)
            raise ToolFailed(f"{option.label} can't run here ({why}){offer or turn_on(group, option.id)}",
                             next="ask the user whether to download it" if offer else ASK_WHICH)
        broken_check(hub, group, option.id, said_option(group, option))
        change(group, option.id, who)
        return (f"from now on, {role_words(group)} uses {option.label} (the Models menu shows it; kept for today); work "
                "already started keeps what it had")

    def change(group: str, option_id: str, who: str) -> None:
        """One way to change a setting from now on: as the studio's menus do (commands.change_settings)."""
        if getattr(services, "change_settings", None) is not None:
            problems = services.change_settings({group: option_id}, who)
        else:
            problems = hub.settings.update({group: option_id})
            if services.publish_settings is not None:
                services.publish_settings()
        if problems:
            raise ToolFailed("; ".join(problems), next="open the Models menu to see what can be chosen")

    # The models a request can be given, once, "id = name" by operation (names.choices_text); set_default and
    # compare_models take the same ids. A fixed list, so a name the model shortened is never what is sent (live
    # 2026-10-11: "SAM 3.1 (local)").
    use_params = schema({"operation": {"type": "string", "enum": [role for role, _ in names.roles(request=True)],
                                       "description": "what the model is for: an operation or a setting"},
                         "implementation": {"type": "string", "enum": names.choosable_ids(),
                                            "description": "the model's id, from the list below"},
                         "policy": {"type": "string", "description": "raw (default) or route"},
                         "by": {"type": "string", "enum": list(BY), "description": "who chose it: the user named it, or you did"}},
                        ["operation", "implementation", "by"])
    use_params["description"] = names.choices_text()
    registry.add(Tool("use", "The model the user names for this request only (\"use SAM 3.1 for this\", \"try GPT for "
                      "this one\"), for the steps after it. A choice alone changes nothing on screen: the step it is for "
                      "follows it in the same plan. To draw an outlined thing again with it, highlight that outline "
                      "(things: [\"o1\"]); find inside an outline is for parts within it.",
                      use_params, use, inline_ms=200, family="conversation"))

    magnifier = getattr(services, "magnifier", None)  # stand-ins without one: no compare_models
    if magnifier is not None:
        from ... import model_comparison

        compared = Compared(hub)  # the last comparison's arms, kept to choose from (Q16)

        async def compare_models(ctx: ToolContext, args: dict) -> str:
            """Named comparison (M2; Part 2.5 W5: up to six): the models named do the same work on the same input, each
            through the runner, and the comparison is kept as one table from their records; never the arena."""
            if args.get("keep"):
                return await compared.keep(services, ctx, str(args["keep"]), expected=args.get("comparison"))
            op = operation_of(args)
            if op.id not in model_comparison.OPERATIONS:
                raise ToolFailed(f"models can be compared here at outlining, segmenting every cell or classifying cells, "
                                 f"not {OPERATION_WORDS[op.id]}",
                                 next="ask to compare them at outlining a thing, segmenting every cell or classifying cells")
            policy = str(args.get("policy") or "raw").strip().lower()
            if policy not in ("raw", "route"):
                raise ToolFailed('the policy is "raw" (exactly those models) or "route" (each with its declared fallbacks)',
                                 next='say "exactly those models" or "with their fallbacks"')
            placement = args.get("placement")
            from ...placement import PLACEMENTS

            if placement is not None and placement not in PLACEMENTS:
                raise ToolFailed(f"{placement!r} is not a place for the comparison's table", next="say one of: " + ", ".join(PLACEMENTS))
            chosen = model_comparison.select(hub, op.id, args.get("implementations"), pick=pick)
            unknown, cannot = chosen.unknown, chosen.cannot
            named = len(chosen.impls) + len(cannot) + len(chosen.left_out)
            if named < 2:
                if unknown:  # a name that means nothing goes back to the model with the choices (live 2026-10-11)
                    raise ToolFailed(f"a comparison needs two different models (not known: {'; '.join(unknown)}); the "
                                     f"choices (id = name): {names.choices_line(op.id)}", next=PICK_AGAIN)
                raise ToolFailed("a comparison needs two different models", next=ASK_WHICH)
            most = model_comparison.MAX_MODELS
            if args.get("exact") and (unknown or cannot or not 2 <= named <= most):
                raise ToolFailed(f"The selected comparison needs two to {most} available, distinct models. "
                                 + "; ".join(cannot + unknown), next="check model setup and choose again")
            impls = chosen.impls
            if len(impls) < 2:
                raise ToolFailed("; ".join(cannot) or "a comparison needs two models that can run here", next=ASK_WHICH)
            kept: dict = {}
            if op.id == "outline":
                words, record = await _compare_outlines(services, ctx, args, impls, policy, kept)
            elif op.id == "cells":
                words, record = await _compare_cells(services, ctx, args, impls, policy, kept)
            else:
                words, record = await _compare_classes(services, ctx, args, impls, policy)
            if op.id != "classify":
                words += compared.hold(kept)
                compared.session = getattr(ctx.asked, "session", None)
            from ..registry import CURRENT_RUN
            active_run = CURRENT_RUN.get()
            if active_run is not None and "comparison" in active_run.values and op.id != "classify":
                active_run.values["comparison"]["id"] = compared.token
            words += await _keep_table(services, ctx, record, placement, default_shown=op.id == "classify")
            said = chosen.said()
            return words + (f"; {said}" if said else "")

        compare_ids = [i for i in names.choosable_ids()
                       if any(names.resolve(o, i) for o in model_comparison.OPERATIONS)]
        registry.add(Tool("compare_models", f"Run the models the user names (two to {model_comparison.MAX_MODELS}) on "
                          "the same input, side by side, measured from each run's record (\"compare EdgeTAM and SAM 3.1 "
                          "on that\", \"compare five classifiers on these cells\"); not the blind test. classify: each "
                          "model names every cell of a population (of; the cells shown if none) from `classes` (else the "
                          "specimen's list, else in its own words), and the comparison says how often they agree, each "
                          "one's counts per class, time and cost. The comparison is kept as a table in Data; `placement` "
                          "shows it (classify: beside by default). Then keep=<one of them> puts that one's outline or "
                          "cells on the board (\"keep the SAM 2.1 one\").",
                          schema({"operation": {"type": "string", "enum": list(model_comparison.OPERATIONS),
                                                "description": "outline (default), cells or classify"},
                                  "implementations": {"type": "array", "maxItems": 8,
                                                      "description": "the models' ids, as listed under use",
                                                      "items": {"type": "string", "enum": compare_ids}},
                                  "object": {"type": "string"},
                                  "of": {"type": "string", "description": "cells or classify: the population (e3); "
                                                                          "empty: the cells shown"},
                                  "classes": {"type": "array", "maxItems": 24, "items": {"type": "string"},
                                              "description": "classify: the classes to name each cell from, as the user "
                                                             "said them"},
                                  "placement": PLACEMENT_SCHEMA,
                                  "input": {"type": "string", "enum": ["view"], "description": "Compare cells on the current input instead of the displayed population"},
                                  "exact": {"type": "boolean", "description": "Refuse the whole comparison if any selected model cannot run"},
                                  "comparison": {"type": "string", "description": "Exact comparison id when keeping a result from the Studio"},
                                  "policy": {"type": "string", "description": "raw (default) or route"},
                                  "keep": {"type": "string", "description": "the model of the last comparison whose result "
                                                                            "to keep on the board (its id)"}}),
                          compare_models, inline_ms=1500, family="magnifier", outline_refs=("object",)))

    async def set_default(ctx: ToolContext, args: dict) -> str:
        from ...operations import availability

        by = str(args.get("by") or "").strip().lower()
        if by not in BY:
            raise ToolFailed(f"say who chose the model: by is {' or '.join(BY)} (user: the user named it; brain: you "
                             "chose it)", next=PICK_AGAIN)
        role = str(args.get("operation") or "").strip().lower()
        if role in names.SETTINGS:
            return set_default_setting(role, args, by)
        op = operation_of(args, default=None)
        impl, said = pick_said(hub, op, args.get("implementation"))
        if not impl.option or not op.selected_by:
            raise ToolFailed(f"{impl.label} is not one of the models {OPERATION_WORDS[op.id]} can be set to", next=ASK_WHICH)
        why = availability(hub, impl)
        if why:
            offer = download_offer(impl_model(impl), why)
            raise ToolFailed(f"{impl.label} can't run here ({why}){offer}",
                             next="ask the user whether to download it" if offer else ASK_WHICH)
        broken_check(hub, op.selected_by, impl.option, names.said_impl(impl.id))
        change(op.selected_by, impl.option, by)
        return (f"from now on, {OPERATION_WORDS[op.id]} uses {impl.label} (the Models menu shows it; kept for today); work "
                "already started keeps what it had" + (f" ({said})" if said else ""))

    settable = [role for role, _ in names.roles(settable=True)]
    registry.add(Tool("set_default", "The model an operation or setting uses from now on, kept for today (\"always use "
                      "SAM 2.1\", \"switch the vision model to Gemini 3.5 Flash\"); for one request, `use`.",
                      schema({"operation": {"type": "string", "enum": settable,
                                            "description": "what the model is for: an operation or a setting"},
                              "implementation": {"type": "string", "enum": names.choosable_ids(settable=True),
                                                 "description": "the model's id, as listed under use"},
                              "by": {"type": "string", "enum": list(BY), "description": "who chose it: the user named it, or you did"}},
                             ["operation", "implementation", "by"]),
                      set_default, inline_ms=200, family="conversation", sets=kept_groups()))

    async def models_in_use(ctx: ToolContext, args: dict) -> str:
        from ... import models_in_use as in_use

        return in_use.text(services.hub.settings)

    registry.add(Tool("models_in_use", "Which model does each job right now (the brain, the voice, seeing, the running "
                      "look, quick decisions, pointing, outlining, cells, look-alikes, researching, presentation, pictures, "
                      "video): its name, cloud or this Mac, how fast it answered lately, and where to change it. For "
                      "\"what models are powering you?\"; name each job's model as it says.",
                      schema({}), models_in_use, kind="query", inline_ms=200, family="conversation"))


KEPT_COMPARISON_S = 300.0  # a comparison's arms are kept to choose from for 5 minutes (or until the next, or a keep)


class Compared:
    """The last named comparison, kept to choose from (the user's Q16, 2026-10-11: "keep the SAM 2.1 one" was
    refused: its view had left the 4 s ring and the outline could not be added). Its view is held in the unsaved
    allowance until the next comparison, a keep, or KEPT_COMPARISON_S; `keep` puts one arm on the board: an outline
    arm as an outline made by that arm's execution (replacing the app's earlier outline of that thing, never the
    user's), a cells arm by showing its population."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self.held = None  # capture.Hold of the compared view
        self.kept: dict = {}
        self.timer = None
        self.token = None
        self.session = None

    def release(self) -> None:
        if self.held is not None:
            self.held.release()
        if self.timer is not None:
            self.timer.cancel()
        self.held, self.kept, self.timer = None, {}, None
        self.token = None
        self.session = None

    def hold(self, kept: dict) -> str:
        """Hold what was compared (words said when it can't be held)."""
        import asyncio

        from ...capture import Refused, captures

        self.release()
        ran = [a for a in kept.get("arms") or [] if not a.get("failed")]
        if kept.get("capture") is None or len(ran) < 1:
            return ""
        try:
            self.held = captures(self.hub).hold(kept["capture"], "unsaved", "hold", "a comparison kept to choose from")
        except (Refused, ValueError) as exc:
            return f"; its results can't be kept to choose from ({exc})"
        self.kept = kept
        from uuid import uuid4
        self.token = uuid4().hex
        try:
            self.timer = asyncio.get_running_loop().call_later(KEPT_COMPARISON_S, self.release)
        except RuntimeError:
            self.timer = None
        if kept.get("operation") == "cells":  # each arm's cells are a population kept in Data, whatever this hold does
            return "; each result is kept in Data"
        return "; each result is kept for 5 minutes to keep one on the board"

    async def keep(self, services, ctx: ToolContext, which: str, *, expected: str | None = None) -> str:
        from ... import tools as app_tools
        from ...operations import label_of, runner

        if expected is not None and (expected != self.token or self.session != getattr(ctx.asked, "session", None)):
            raise ToolFailed("That comparison has changed or expired.", next="run a new comparison in this Project")
        if not self.kept:
            raise ToolFailed("there is no comparison from the last 5 minutes to keep a result from",
                             next="compare the models again, then say which to keep")
        operation = self.kept["operation"]
        meaning = names.resolve(operation, which)
        arm = next((a for a in self.kept["arms"] if meaning is not None and a["impl"] == meaning.id and not a.get("failed")), None)
        if arm is None:
            choices = "; ".join(f"{a['impl']} = {a['label']}" for a in self.kept["arms"] if not a.get("failed"))
            raise ToolFailed(f"the last comparison has no result of {which!r}; its results (id = name): {choices}",
                             next=PICK_AGAIN)
        if operation == "cells":
            from .present import act

            said = await act(services, {"what": arm["population"], "do": "show"}, ctx=ctx)
            self.release()
            return f"kept {label_of(arm.get('executed') or arm['impl'])}'s cells ({arm['population']}): {said}"
        record = runner(self.hub).get(arm.get("execution"))
        made_by = record.made_by() if record is not None else {"operation": "outline", "implementation": arm.get("executed")}
        obj = self.kept.get("object")
        polygons = arm.get("polygons") or []
        if not polygons:
            raise ToolFailed(f"{arm['label']} outlined nothing in the comparison", next="keep another one")
        added = await app_tools.add_outline(self.hub, self.kept["capture"], polygons,
                                            source=obj.source if obj is not None and str(obj.source).startswith("ai") else "ai:compare",
                                            label=getattr(obj, "label", "") or "", status=getattr(obj, "status", "outline"),
                                            made_by={**made_by, "origin": "compare"},
                                            naming=dict(obj.naming) if obj is not None and obj.naming else None)
        if obj is not None and obj.id in self.hub.scene.objects and str(obj.source).startswith("ai") and obj.id != added.id:
            from ...ticket import current

            request = current()  # gated, as highlight's replace_earlier: only while this request's ticket stands
            if request is None or not request.superseded("outline:app"):
                self.hub.scene.remove(obj.id)  # the app's earlier outline of that thing: replaced; the user's own stays
        if added.id not in ctx.objects:
            ctx.objects.append(added.id)
        from ..registry import CURRENT_RUN, record_made
        from .common import record_outline

        if CURRENT_RUN.get() is not None:
            record_outline(ctx, added)
            record_made(ctx, added.id)
        self.release()
        return (f"kept {label_of(arm.get('executed') or arm['impl'])}'s outline of the {added.label or 'thing'} on the "
                f"board ({added.id})")


def _ran(a: dict) -> str:
    """What produced an arm's result, by label: the model asked for, or "X → Y, its fallback" (ADR 0011)."""
    from ...operations import label_of

    if a.get("executed") and a["executed"] != a["impl"]:
        return f"{a['label']} → {label_of(a['executed'])}, its fallback"
    return a["label"]


def _scale(capture) -> tuple[float | None, str]:
    """µm per pixel of the view and where it comes from ("estimated" from the objective, or "calibrated")."""
    scale = getattr(capture, "scale", None)
    if not isinstance(scale, dict) or not scale.get("umPerPx"):
        return None, ""
    return float(scale["umPerPx"]), ("calibrated" if scale.get("measured") else "estimated")


def _ran_name(a: dict) -> str:
    """The model that produced an arm's result, by its label."""
    from ...operations import label_of

    return label_of(a.get("executed")) if a.get("executed") else a["label"]


def _pairs(ran: list, measure) -> list[str]:
    """Every pair of models that ran, named by what ran (a fallback by the model it fell back to): two arms that
    ran the same model are said to be its output twice, never an overlap; a pair already said is not said again."""
    out, said = [], set()
    for i in range(len(ran)):
        for j in range(i + 1, len(ran)):
            a, b = ran[i], ran[j]
            if a.get("executed") and a.get("executed") == b.get("executed"):
                out.append(f"{a['label']} and {b['label']} are the same model's output ({_ran_name(a)}), "
                           "so they are not compared")
                continue
            pair = frozenset((a.get("executed") or a["impl"], b.get("executed") or b["impl"]))
            if pair in said:
                continue
            said.add(pair)
            out.append(measure(a, b))
    return out


async def _compare_outlines(services, ctx: ToolContext, args: dict, impls: list, policy: str, kept: dict | None = None) -> str:
    """Each model outlines the same thing, prompted by the same box, on the view the thing was outlined on
    (or, if that view is gone, the view now where the thing is verified in place)."""
    import asyncio

    from ... import comparing
    from ...capture import CaptureGone, Refused, captures, decoding
    from ...operations import Prompt, Unavailable, runner
    from .common import resolve_object

    hub = services.hub
    obj = resolve_object(hub, args.get("object"), ctx)
    capture = captures(hub).get(obj.origin) if getattr(obj, "origin", None) is not None else None
    if capture is not None and obj.origin_polygons:
        pts = np.array([p for poly in obj.origin_polygons for p in poly], float)
    else:
        info = getattr(hub, "latest_info", None)
        if info is None or not getattr(obj, "shown", True) or obj.anchor_epoch != getattr(info, "epoch", None):
            raise ToolFailed("the view it was outlined on is gone and its place on the view now isn't verified",
                             next="hold the slide still for a second, then ask again")
        capture = captures(hub).get(info.id)
        if capture is None:
            raise ToolFailed("there is no fresh view to compare them on", next="wait for the view to come back, then ask again")
        shift = np.array([info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]])
        pts = np.array([p for poly in obj.polygons for p in poly], float) + shift
    box = (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))
    prompt = Prompt("box", box=box, text=obj.label or "")
    arms = []
    for impl in impls:
        try:
            done = await runner(hub).run("outline", capture, prompt, choice=impl.id, policy=policy, origin="compare")
        except (Unavailable, ToolFailed) as exc:
            arms.append({"impl": impl.id, "label": impl.label, "failed": str(exc)})
            continue
        made = done.made_by()
        executed = done.execution.executed or impl.id
        reused = bool(done.use.reused)
        arm = {"impl": impl.id, "label": impl.label, "executed": executed, "polygons": done.output.get("polygons") or [],
               "reused": reused, "at": done.execution.at, "ms": None if reused else round(done.execution.ms, 1),
               "execution": done.execution.id, "fallbackFrom": made.get("fallbackFrom"), **_cost(impl, done, reused)}
        arm["areaPx"] = round(comparing.polygon_area(arm["polygons"]), 1)
        arms.append(arm)
    ran = [a for a in arms if not a.get("failed") and a["polygons"]]
    if kept is not None:
        kept.update(operation="outline", capture=capture, arms=arms, object=obj)
    um, source = _scale(capture)
    parts = []
    for a in arms:
        if a.get("failed"):
            parts.append(f"{a['label']} failed ({a['failed']})")
            continue
        if not a["polygons"]:
            parts.append(f"{_ran(a)} outlined nothing")
            continue
        size = f"{a['areaPx']:,.0f} px²" + (f" ({a['areaPx'] * um * um:,.0f} µm², {source} scale)" if um else "")
        how = f"reused from its run at {comparing.when(a['at'])} (its time not counted)" if a["reused"] else f"in {a['ms']:.0f} ms"
        a["caption"] = f"{_ran(a)} · {a['areaPx']:,.0f} px²"
        parts.append(f"{_ran(a)} {size}, {how}")
    pairs = _pairs(ran, lambda a, b: f"{_ran_name(a)} and {_ran_name(b)} overlap "
                                     f"{comparing.overlap(a['polygons'], b['polygons']) * 100:.0f}% (IoU)")
    measured = _measured_pairs(ran, lambda a, b: {"iou": comparing.overlap(a["polygons"], b["polygons"])})
    shown = ""
    if len(ran) >= 2:
        try:
            with decoding(hub, capture, "look", owner="a comparison") as held:
                image = held.bgr()
                jpeg = await asyncio.to_thread(comparing.picture, image, ran, box) if image is not None else None
        except (CaptureGone, Refused):
            jpeg = None
        if jpeg is not None:
            from ...screen import picture_shown

            try:  # shown once the studio's report names the picture (flow 7)
                await picture_shown(services.magnifier, jpeg, "Compared on the same view: " + " | ".join(_ran(a) for a in ran),
                                    object_id=obj.id)
                shown = "; shown side by side in the magnifier"
            except ToolFailed:
                shown = "; the picture did not show in the magnifier"
    from ..registry import attach

    from ... import model_comparison

    stored = [{k: v for k, v in a.items() if k not in ("caption", "polygons")} for a in arms]
    record = model_comparison.build("outline", stored, policy=policy, summary={"pairs": measured}, um_per_px=um,
                                    on={"capture": getattr(capture, "id", None), "object": obj.id,
                                        "box": [round(v, 1) for v in box]})
    attach(values={"comparison": {"operation": "outline", "policy": policy, "capture": getattr(capture, "id", None),
                                  "object": obj.id, "box": [round(v, 1) for v in box], "pairs": pairs,
                                  "arms": [{k: v for k, v in a.items() if k != "caption"} for a in arms]}})
    how = "exactly those models" if policy == "raw" else "each with its declared fallbacks"
    return (f"Compared on the view at {comparing.when(getattr(capture, 'wall_at', None))} ({how}): " + "; ".join(parts)
            + "".join(f"; {p}" for p in pairs) + shown), record


def _studio_draws() -> bool:
    """A cell comparison the user ran by hand from the workspace's Tools: the workspace draws each model's retained masks
    on its Stage in that model's colour (apps/studio/src/workspace-study-content.ts openStudyComparison), so the
    side-by-side picture would show the same comparison twice. The agent's comparisons keep the picture."""
    from ...activity import BY_HAND
    from ...ticket import current

    return BY_HAND.get() and getattr(current(), "workspace_origin", None) is not None


async def _compare_cells(services, ctx: ToolContext, args: dict, impls: list, policy: str, compared: dict | None = None) -> str:
    """Each model segments every cell of the same view: the cells shown (or named), else the view now. A
    population already kept for that view and model is reused, and said to be, its use recorded as a reuse."""
    import asyncio

    from ... import comparing
    from ...capture import RUN, CaptureGone, Refused, captures, decoding
    from ...evidence import evidence_of
    from ...operations import runner
    from ...population import capture_of, layer_of, resolve, segment

    import re

    hub = services.hub
    ref = str(args.get("of") or args.get("object") or "").strip()
    named = re.fullmatch(r"e\d+|@\d+", ref) is not None
    base = resolve(hub, ref, ctx) if named else (None if args.get("input") == "view" else layer_of(hub).population())
    if named and base is None:  # a name that names nothing is said to (never "a view that's gone")
        from ...evidence import evidence_of

        kept = ", ".join(list(evidence_of(hub).items)[-6:]) or "none yet"
        raise ToolFailed(f"there are no cells called {ref} today (kept: {kept})", next="name one of those, or compare on the view now")
    capture = capture_of(hub, base) if base is not None else None
    if named and capture is None:  # never another view in its place, unsaid (M2.2 re-check, SF4)
        raise ToolFailed(f"the view {ref} was segmented on is not here any more", next="compare them on the view now")
    if capture is None:  # nothing shown or named: the view now
        info = getattr(hub, "latest_info", None)
        capture = captures(hub).get(info.id) if info is not None else None
    if capture is None:
        raise ToolFailed("there is no fresh view to compare them on", next="wait for the view to come back, then ask again")
    evidence = evidence_of(hub)
    arms = []
    for impl in impls:
        # A population this model already made of this view is reused, and said to be (contract 4).
        kept = next((p for p in reversed(list(evidence.items.values())) if p.operation == "cells"
                     and p.capture_id == capture.id and p.implementation == impl.id and p.view.get("run") == RUN), None)
        try:
            population = kept or await segment(hub, capture=capture, choice=impl.id, policy=policy, origin="compare")
        except ToolFailed as exc:
            arms.append({"impl": impl.id, "label": impl.label, "failed": str(exc)})
            continue
        from ..registry import record_made

        record_made(ctx, population.id)  # "@n" names it to the steps after this one (M2.2 re-check, SF2)
        execution = (population.made_by or {}).get("execution")
        if kept is not None:
            runner(hub).kept_use("cells", execution)  # a reuse, recorded as one (invariant 5)
        record = runner(hub).get(execution)
        reused = kept is not None or bool((population.made_by or {}).get("reused"))
        arms.append({"impl": impl.id, "label": impl.label, "executed": population.implementation, "population": population.id,
                     "count": len(population.live()), "reused": reused, "revision": population.revision,
                     "at": record.at if record is not None else population.made_at, "execution": execution,
                     "ms": None if reused or record is None else round(record.ms, 1), **_cost(impl, None, reused),
                     "boxes": [list(m.box) for m in population.live()],
                     "polygons": [p for m in population.live() for p in m.polygons]})
    ran = [a for a in arms if not a.get("failed")]
    parts = []
    for a in arms:
        if a.get("failed"):
            parts.append(f"{a['label']} failed ({a['failed']})")
            continue
        how = f"reused from its run at {comparing.when(a['at'])} (its time not counted)" if a["reused"] else (
            f"in {a['ms'] / 1000:.1f} s" if a.get("ms") else "just now")
        if a.get("revision"):
            how += f"; corrected by you since (revision {a['revision']})"
        a["caption"] = f"{_ran(a)} · {a['count']}"
        parts.append(f"{_ran(a)}: {a['count']} instances segmented in the view ({a['population']}), {how}")
    pairs = _pairs(ran, lambda a, b: f"{comparing.matched(a['boxes'], b['boxes'])} found by both {_ran_name(a)} and "
                                     f"{_ran_name(b)} (boxes at least {comparing.MATCH_IOU:.0%} alike)")
    measured = _measured_pairs(ran, lambda a, b: {"matched": comparing.matched(a["boxes"], b["boxes"])})
    shown = ""
    if compared is not None:
        compared.update(operation="cells", capture=capture, arms=arms)  # kept to choose from (Compared)
    if len(ran) >= 2 and _studio_draws():
        pass  # the workspace draws it and says so once it has (never claimed here: ADR 0006)
    elif len(ran) >= 2:
        f = capture.fov
        # Framed on the eyepiece circle; a view with none is framed whole (Slice 5: it was not shown at all).
        box = ((f.cx - f.radius, f.cy - f.radius, f.cx + f.radius, f.cy + f.radius) if f is not None
               else (0.0, 0.0, float(capture.width), float(capture.height)))
        try:
            with decoding(hub, capture, "look", owner="a comparison") as held:
                image = held.bgr()
                jpeg = await asyncio.to_thread(comparing.picture, image, ran, box, 420) if image is not None else None
        except (CaptureGone, Refused):
            jpeg = None
        if jpeg is not None:
            from ...screen import picture_shown

            try:  # shown once the studio's report names the picture (flow 7)
                await picture_shown(services.magnifier, jpeg, "Compared on the same view: " + " | ".join(_ran(a) for a in ran))
                shown = "; shown side by side in the magnifier"
            except ToolFailed:
                shown = "; the picture did not show in the magnifier"
    from ..registry import attach

    from ... import model_comparison

    stored = [{k: v for k, v in a.items() if k not in ("polygons", "caption", "boxes")} for a in arms]
    record = model_comparison.build("cells", stored, policy=policy, summary={"pairs": measured},
                                    on={"capture": capture.id})
    attach(values={"comparison": {"operation": "cells", "policy": policy, "capture": capture.id, "pairs": pairs,
                                  "arms": [{k: v for k, v in a.items() if k not in ("polygons", "caption")} for a in arms]}})
    how = "exactly those models" if policy == "raw" else "each with its declared fallbacks"
    return (f"Compared on the view at {comparing.when(getattr(capture, 'wall_at', None))} ({how}): " + "; ".join(parts)
            + "".join(f"; {p}" for p in pairs) + shown), record


async def _compare_classes(services, ctx: ToolContext, args: dict, impls: list, policy: str) -> tuple[str, dict]:
    """Each model names every cell of the same population (the one named, else the one shown) from the same classes,
    through the runner (model_comparison.compare_classes); the words say each run and how often they agree."""
    from ... import model_comparison
    from ...population import resolve

    hub = services.hub
    ref = str(args.get("of") or args.get("object") or "").strip() or None
    population = resolve(hub, ref, ctx)
    if population is None:
        raise ToolFailed(f"there are no cells called {ref} to classify" if ref else "no cells are outlined to classify",
                         next="outline the cells (outline_cells), then compare the classifiers on them")
    record = await model_comparison.compare_classes(hub, population, impls, classes=args.get("classes"), policy=policy)
    if not any(not a.get("failed") for a in record["arms"]):
        raise ToolFailed("no model could classify them: " + "; ".join(f"{a['label']}: {a['failed']}" for a in record["arms"]),
                         next="check the models' keys and setup, then compare again")
    from ..registry import attach

    on = record["on"]
    how = "exactly those models" if policy == "raw" else "each with its declared fallbacks"
    head = (f"Classified {on['members']} of {population.title}'s {on['of']} cells ({population.id}) with each model "
            f"({how}), the same cells and the same classes ("
            + (", ".join(record["summary"]["classes"]) if record["summary"]["classes"] else "none given: in their own words")
            + "): ")
    attach(values={"comparison": {"operation": "classify", "policy": policy, "population": population.id,
                                  "arms": [{k: v for k, v in a.items()} for a in record["arms"]]}})
    return head + model_comparison.words(record), record


def _cost(impl, done, reused: bool) -> dict:
    """An outline or cell run's cost as its record says it: a model on this Mac costs nothing; a cloud one's is not
    reported for these operations (said so)."""
    if reused:
        return {"costUsd": None, "costBasis": "a reuse: nothing new was spent", "runs": "cloud" if impl.worker == "cloud" else "this Mac"}
    if impl.worker == "cloud":
        return {"costUsd": None, "costBasis": "not reported for this model's runs", "runs": "cloud"}
    return {"costUsd": 0.0, "costBasis": "runs on this Mac", "runs": "this Mac"}


def _measured_pairs(ran: list, measure) -> list[dict]:
    """Every pair of models that ran, by implementation, with what `measure` says of them (as `_pairs` words them: two
    arms that ran the same model are not compared)."""
    out, said = [], set()
    for i in range(len(ran)):
        for j in range(i + 1, len(ran)):
            a, b = ran[i], ran[j]
            if a.get("executed") and a.get("executed") == b.get("executed"):
                continue
            pair = frozenset((a.get("executed") or a["impl"], b.get("executed") or b["impl"]))
            if pair in said:
                continue
            said.add(pair)
            out.append({"a": a["impl"], "b": b["impl"], **measure(a, b)})
    return out


async def _keep_table(services, ctx: ToolContext, record: dict, placement: str | None, *, default_shown: bool) -> str:
    """The comparison kept as one table in Data (a result entry whose table the measurements table reads), and shown
    where it was asked (placement), or beside by default when it is the comparison's only view (classify)."""
    from ... import model_comparison
    from ..registry import CURRENT_RUN, attach

    hub = services.hub
    results = getattr(getattr(hub, "resources", None), "results", None)
    run = CURRENT_RUN.get()
    if results is None or run is None:
        return ""
    kept_values = model_comparison.values(record)
    # The record's fields beside what the studio already reads of a comparison (commands._comparison_summary): its arms
    # and pairs as they were, its token.
    attach(values={**kept_values, "comparison": {**kept_values["comparison"], **(run.values.get("comparison") or {})}})
    try:
        entry = results.from_run(run, ctx, hub, produced=True)
    except (OSError, ValueError, ToolFailed) as exc:
        return f"; its table could not be kept ({exc})"
    if entry is None:
        return ""
    kept = f"; the comparison's table is kept in Data as {entry['id']} ({model_comparison.title(record)})"
    place = placement or ("beside" if default_shown else None)
    if place is None or ctx.channel == "system" or getattr(services, "open_page", None) is None:
        return kept + (f"; show_measurements of={entry['id']} opens it" if place is None else "")
    from .tables import open_table

    day = getattr(services, "day", None)
    try:
        shown = await open_table(services, ctx, session=getattr(day, "sid", ""), target=entry["id"], revision=0,
                                 title=str(entry.get("title") or model_comparison.title(record)),
                                 rows=len(record["rows"]), what=f"the comparison's table {entry['id']}", tab=None,
                                 guard=lambda: None, placement=place)
    except ToolFailed as exc:
        return kept + f", not shown ({exc}); show_measurements of={entry['id']} opens it"
    return kept + f": {shown}"
