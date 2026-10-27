"""Tools on the session and its conversations: start a session or a conversation, go back to one, recall what was said.

A session is the project (ADR 0017): its conversations, memory and work. Only the user starts one or goes back to one;
a new topic is a conversation in the same session."""
from __future__ import annotations

import asyncio
import time

from ...day.catalogue import days_before
from ...untrusted import inline
from ..registry import Registry, Tool, ToolContext, ToolFailed, schema


def add_thread_tools(registry: Registry, services) -> None:
    day, memory = services.day, services.memory
    if day is None:
        return

    async def new_thread(ctx: ToolContext, args: dict) -> str:
        old = day.active
        thread = day.open_thread(str(args.get("title") or ""))
        if memory is not None and old.turns:
            asyncio.ensure_future(memory.summarize(old.id, force=True))  # the old one stays resumable, summarised
        named = f' "{thread.title}"' if thread.title else ""
        return (f"started a new conversation{named} ({thread.id}); "
                f"the earlier one{f' ({old.title})' if old.title else ''} is kept and can be resumed")

    registry.add(Tool("new_thread", "Start a new conversation when the user asks for one (\"new topic\", \"let's start "
                      "fresh\", \"new thread\"); the current one is kept and can be resumed. Never on your own.",
                      schema({"title": {"type": "string", "description": "optional short name for it"}}), new_thread,
                      inline_ms=200, family="conversation"))

    async def switch_thread(ctx: ToolContext, args: dict) -> str:
        thread = day.find_thread(str(args.get("thread") or ""))
        if thread is None:
            listed = "; ".join(f'{t.id} "{t.title or "untitled"}"' for t in sorted(day.threads.values(), key=lambda t: t.n))
            raise ToolFailed(f"which conversation? Today: {listed}", next=None)  # no next: the message lists the conversations to choose from
        if thread.id == day.active_id:
            return f'already in "{thread.title}"'
        day.switch(thread.id)
        about = ", ".join(x["name"] for x in thread.subjects[-2:])
        # What was said there, so a request like "back to the blood smear: what did we
        # conclude?" is answered from that conversation (this is a query tool).
        recent = [f"{'User' if t.role == 'user' else 'You'}: {inline(t.text, 160)}" for t in thread.turns[-6:] if t.role != "note"]
        return (f'back in "{thread.title}" ({thread.id}, started {time.strftime("%H:%M", time.localtime(thread.started))}'
                + (f"; about {about}" if about else "") + ")"
                + (f". So far: {inline(thread.summary, 300)}" if thread.summary else "")
                + (". Its latest exchanges: " + " / ".join(recent) if recent else ""))

    registry.add(Tool("switch_thread", "Go back to an earlier conversation from today when the user asks (\"back to the "
                      "blood smear conversation\", \"the previous topic\"): by its title, what was looked at, or its number.",
                      schema({"thread": {"type": "string", "description": "title, specimen, t2, or previous"}}, ["thread"]),
                      switch_thread, kind="query", inline_ms=200, family="conversation"))

    # --- sessions (ADR 0017) ---------------------------------------------------------------------------
    def moved(ctx: ToolContext) -> None:
        """The request goes on where it took the user (ADR 0017): its later steps and its answer belong to the session
        opened (brain/loop.py files the request there too)."""
        from ...ticket import current

        ctx.asked = day.here()
        request = current()
        if request is not None:
            request.asked = ctx.asked

    async def new_session(ctx: ToolContext, args: dict) -> str:
        left = day.label
        day.new(str(args.get("name") or ""), by="user")
        moved(ctx)
        named = f' "{inline(day.name, 80)}"' if day.name else ""
        return (f"started a new session{named}, with one empty conversation; the live view started fresh. The session "
                f'left ("{inline(left, 80)}") is kept with its conversations and work and can be opened again')

    registry.add(Tool("new_session", "Start a new session (a new project, with its own conversations, memory and work) "
                      "only when the user asks for a new session in so many words; a new topic or starting fresh is "
                      "new_thread, in this session. When it is unclear, start a conversation with new_thread and say "
                      "that a new session is theirs to ask for. The live view starts fresh; the session left is kept "
                      "and can be opened again. Never on your own.",
                      schema({"name": {"type": "string", "description": "its name, when the user gives one"}}), new_session,
                      inline_ms=300, family="conversation"))

    async def switch_session(ctx: ToolContext, args: dict) -> str:
        ref = str(args.get("session") or "").strip()
        catalogue = day.catalogue
        now = day.clock()
        found = await asyncio.to_thread(catalogue.find, ref, current=day.sid, now=now)
        others = [info for info in found if info.id != day.sid]
        if found and not others:
            return f'already in the session "{inline(day.label, 80)}"'
        if not others:
            recent = [info for info in await asyncio.to_thread(catalogue.list)][:6]
            raise ToolFailed(f"no session matches {inline(ref, 80)!r}; the most recent: "
                             + "; ".join(_session_row(info, now, day.sid) for info in recent),
                             next="ask the user which session they mean")
        if len(others) > 1:
            return ("nothing was opened: these sessions may be meant, ask the user which (newest first): "
                    + "; ".join(_session_row(info, now, day.sid) for info in others[:6])
                    + (f"; and {len(others) - 6} more" if len(others) > 6 else ""))
        info = others[0]
        try:
            day.switch_session(info.id)
        except KeyError:
            raise ToolFailed(f'the session "{inline(info.label, 80)}" can no longer be read',
                             next=None) from None  # no next: its journal is gone, nothing the user can do opens it
        moved(ctx)
        facts = list(memory.facts) if memory is not None else [f for f in day.digest.split("\n") if f.strip()]
        threads = sorted(day.threads.values(), key=lambda t: t.n)
        conversations = "; ".join(
            f'"{inline(t.title or f"Conversation {t.n}", 60)}"' + (f": {inline(t.summary, 300)}" if t.summary else "")
            for t in threads[-6:])
        active = day.active
        recent = [f"{'User' if t.role == 'user' else 'You'}: {inline(t.text, 160)}" for t in active.turns[-6:] if t.role != "note"]
        return (f'opened the session "{inline(day.label, 80)}" ({_span_words(info, now)}, last used '
                f'{_when(info.last_active, now)})'
                + (f". Settled in it: {' / '.join(inline(f, 160) for f in facts)}" if facts else "")
                + (f". Its conversations: {conversations}" if conversations else "")
                + (f'. Latest exchanges in "{inline(active.title or f"Conversation {active.n}", 60)}": '
                   + " / ".join(recent) if recent else "")
                + ". The live view started fresh; its outlines are in its pictures")

    registry.add(Tool("switch_session", "Go back to an earlier session (a project: its conversations, memory and work) "
                      "when the user asks to (\"go back to yesterday's leaf session\", \"pick up Tuesday's blood smear "
                      "project\"): by its name, a day, what was looked at, or previous. A question about another session "
                      "that does not ask to go there is not this. Opening one clears the live view; its conversations "
                      "and work come back. When several may be meant, nothing is opened and they are listed: ask which.",
                      schema({"session": {"type": "string", "description": "its name, a day (yesterday, Oct 11, "
                                                                           "Tuesday), a specimen, or previous"}},
                             ["session"]),
                      switch_session, kind="query", inline_ms=300, family="conversation"))

    if memory is None:
        return

    async def recall(ctx: ToolContext, args: dict) -> str:
        query = str(args.get("query") or ctx.question)
        ref = str(args.get("session") or "").strip()
        store, said = day, ""
        if ref:  # another session the user named: read where it is, never opened (ADR 0017)
            now = day.clock()
            found = [info for info in await asyncio.to_thread(day.catalogue.find, ref, current=day.sid, now=now)]
            if not found:
                recent = (await asyncio.to_thread(day.catalogue.list))[:6]
                raise ToolFailed(f"no session matches {inline(ref, 80)!r}; the most recent: "
                                 + "; ".join(_session_row(info, now, day.sid) for info in recent),
                                 next="ask the user which session they mean")
            if len(found) > 1 and found[0].id != day.sid:
                return ("nothing was searched: these sessions may be meant, ask the user which (newest first): "
                        + "; ".join(_session_row(info, now, day.sid) for info in found[:6]))
            try:
                store = await asyncio.to_thread(day.elsewhere, found[0].id)
            except KeyError:
                raise ToolFailed(f'the session "{inline(found[0].label, 80)}" can no longer be read',
                                 next=None) from None  # no next: its journal is gone, nothing the user can do brings it back
            if store is not day:
                facts = [f for f in store.digest.split("\n") if f.strip()]
                said = (f'In the session "{inline(store.label, 80)}" ({_span_words(found[0], now)}; not opened):\n'
                        + (f"Settled in it: {' / '.join(inline(f, 160) for f in facts[:6])}\n" if facts else ""))
        text = said + await asyncio.to_thread(memory.recall_text, query, 4, store)
        index = getattr(services.hub, "resources", None)
        if index is not None:
            inventory = await asyncio.to_thread(index.summary_text, store.sid)
            # Recall searches prose, not the saved pixels/masks. A missing passage must not
            # be mistaken for missing evidence; point to the same index every reader uses.
            text += ("\n\nSaved resources (not searched by that text lookup; use resources to list/read them, "
                     "or query_population for saved cell measurements):\n" + inventory[:2000])
            if len(inventory) > 2000:
                text += "\nInventory shortened; resources can list the remaining entries."
        return text

    registry.add(Tool("recall", "Find what was said or found earlier in this session (on any of its days), in any "
                      "conversation or researcher's work, when the user refers back to it (\"what did we say about the "
                      "platelet?\", \"like the onion earlier\") and the conversation you have does not show it. With "
                      "session: in another session the user names, read without opening it (\"what did we find in "
                      "Tuesday's blood smear session?\").",
                      schema({"query": {"type": "string", "description": "the words to look for"},
                              "session": {"type": "string", "description": "another session the user names: its name, "
                                                                           "a day, a specimen, or previous"}},
                             ["query"]), recall,
                      kind="query", family="conversation"))


def _day_words(at: float, now: float) -> str:
    """"today", "yesterday", "Oct 11" (local time)."""
    day, today = time.localtime(at), time.localtime(now)
    if (day.tm_year, day.tm_yday) == (today.tm_year, today.tm_yday):
        return "today"
    if time.strftime("%Y-%m-%d", day) == days_before(now, 1):
        return "yesterday"
    return time.strftime("%b %-d", day)


def _when(at: float, now: float) -> str:
    return f"{_day_words(at, now)} {time.strftime('%H:%M', time.localtime(at))}" if at else "never"


def _span_words(info, now: float) -> str:
    """When a session began and the days it spans: "started yesterday 18:02, 2 days"."""
    days = len(info.days) or 1
    return f"started {_when(info.created, now)}" + (f", {days} days" if days > 1 else "")


def _session_row(info, now: float, current: str) -> str:
    """One session as the model is told it, to ask the user which: its name, when, what was seen, its id."""
    seen = ", ".join(inline(s, 40) for s in info.specimens[:3])
    return (f'"{inline(info.label, 60)}" ({_span_words(info, now)}; last used {_when(info.last_active, now)}'
            + (f"; specimens: {seen}" if seen else "") + f"; id {info.id}"
            + ("; open now" if info.id == current else "") + ")")


def add_capability_tool(registry: Registry, services) -> None:
    """The catalogue of what the app can do, read by the agent exactly as the user sees it on the
    Capabilities page (capabilities.py; ADR 0007): whether something asked for exists, who does it,
    and whether the user has checked it, is answered from the same list."""
    async def capabilities(ctx: ToolContext, args: dict) -> str:
        from ... import capabilities as catalogue

        if args.get("operation"):
            # Which models can do this, here and now, without running any of them (M1.4).
            from ...operations import alternatives_text

            return alternatives_text(services.hub, str(args["operation"]).strip().lower(),
                                     str(args.get("prompt") or "").strip().lower() or None)
        if services.capabilities is None:  # which models can do an operation needs only the operations themselves
            raise ToolFailed("the list of everything it can do is not available here",
                             next="ask which models can do one thing (outline, cells, find-like)")
        actor = str(args.get("actor") or "").strip().lower()
        family = str(args.get("family") or "").strip().lower()
        return catalogue.text(services.capabilities(), actor=actor if actor in catalogue.ACTORS else "", family=family)

    registry.add(Tool("capabilities", "Discover what is possible here: shared tools, models, availability and known failures, from the same "
                      "catalogue the user sees. Use it for questions like what cool things can we do, ideas for this "
                      "specimen, what should we try next, or whether you can do something; also consult it when unsure "
                      "of a capability. Turn the catalogue into a few concrete, varied possibilities grounded in the "
                      "current specimen, selected subject, existing results and the user's interests. Explain what each "
                      "would reveal or let them explore. Combine available capabilities creatively, such as measurement "
                      "with comparison or a reference beside a magnification; do not just recite tool or model names. "
                      "Distinguish something usable now from something needing setup or known to be broken. Availability "
                      "does not guarantee a scientific result or a successful external lookup. Mention models when that "
                      "helps the choice; with operation, inspect which models can do it here. Quick actions can be yours; "
                      "sustained investigations can go to researchers while conversation continues. Asking for ideas "
                      "does not itself request running them. This lookup runs no analysis and starts no researcher.",
                      schema({"actor": {"type": "string", "description": '"you", "agent", "auto" or "researcher" (optional)'},
                              "family": {"type": "string", "description": "one family id, e.g. motion (optional)"},
                              "operation": {"type": "string", "description": "outline, cells or find-like (optional)"},
                              "prompt": {"type": "string", "description": "point, box, field, example or text (optional)"}}),
                      capabilities, kind="query", inline_ms=500, family="conversation"))
