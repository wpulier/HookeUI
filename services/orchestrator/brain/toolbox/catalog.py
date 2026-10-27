"""The agent relays the Models page (P4 of docs/design/2026-10-12-the-open-catalog.md): what runs here, why not, and how
to turn it on, in the user's words; a download only on their yes; never a key.

- `models` (query): the Models page in words (orchestrator/catalog_view.py): for nothing named, what the user's current
  choices need, what is ready and what is available to add, and their keys; for a model or a job ("SAM 3.1",
  "outlining"), each model's status, uses, facts and next step; for a key ("my Google key"), what it turns on. A model
  whose next step is a download is offered as a proposal (`p1`, kept PROPOSAL_S): the agent asks, and the user's yes
  starts exactly it.
- `get_model` (effect): starts the proposed download on the user's yes (`confirms`, and `asked_with` quoting their reply,
  checked against what they said in the last half minute: research/policy.quoted), through the same queue as the
  page's Download (downloads.py).
- `open_models` (effect): opens the Models page in the studio that asked, at a model's card (its Add key, its Download),
  done once that studio says it shows it.

Keys are the user's alone: no tool takes a key's value, and the agent never asks for one to be said or typed in the
conversation (the card's Add key keeps it out of every log and transcript).
"""
from __future__ import annotations

import itertools
import json
import re
import time

from ..registry import Registry, Tool, ToolContext, ToolFailed, schema

PROPOSAL_S = 180.0  # how long a proposed download waits for the user's yes
KEYS_ARE_THEIRS = ("Keys are the user's to add: open_models shows its card with Add key. Never ask them to say or type a "
                   "key in the conversation, and never repeat one.")


def add_catalog_tools(registry: Registry, services) -> None:
    if services.capabilities is None:
        return
    setup_service = getattr(services, "turn_on", None)
    proposals: dict[str, dict] = {}
    capability_offers: dict[str, dict] = {}
    ids = itertools.count(1)

    def wire() -> dict:
        return services.capabilities()

    def norm_job(text: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())

    def job_card(w: dict, name: str) -> dict | None:
        shelf = (w.get("setup") or {}).get("shelf") or {}
        matches = [c for c in [*shelf.get("cards", []), *shelf.get("behind", [])]
                   if norm_job(name) in (norm_job(c["id"]), norm_job(c["title"]))]
        return matches[0] if len(matches) == 1 else None

    def owner(ctx: ToolContext) -> tuple:
        asked = ctx.asked
        return (getattr(asked, "session", None), getattr(asked, "thread", None),
                getattr(asked, "routing_generation", None), ctx.workspace_id, ctx.client)

    def terms(card: dict) -> str:
        return json.dumps({k: card.get(k) for k in ("turnOn", "privacy", "cost")}, sort_keys=True)

    def offer_capability(ctx: ToolContext, card: dict) -> str:
        if setup_service is None or not card.get("turnOn") or card.get("turning"):
            return ""
        for key, value in list(capability_offers.items()):
            if time.time() - value["at"] > PROPOSAL_S:
                capability_offers.pop(key, None)
        identity = owner(ctx)
        pid = next((key for key, value in capability_offers.items()
                    if value["job"] == card["id"] and value["owner"] == identity), None) or f"c{next(ids)}"
        capability_offers[pid] = {"job": card["id"], "owner": identity, "terms": terms(card), "at": time.time()}
        from ...untrusted import block

        plan = card["turnOn"]
        facts = block("; ".join(s["says"] for s in plan["steps"]) +
                      f". {plan.get('privacy', card['privacy'])}; {plan.get('cost', card['cost'])}",
                      tag="setup-terms", source="catalog and local checks", limit=4000)
        return (f"Capability offer ({pid}): {card['title']} — {facts} "
                f"Ask whether to turn it on. On their yes, call turn_on with job {card['id']!r}, confirms {pid!r} "
                "and asked_with quoting their reply. The offer belongs only to this conversation.")

    def propose(row: dict) -> str:
        now = time.time()
        for pid, p in list(proposals.items()):
            if now - p["at"] > PROPOSAL_S:
                proposals.pop(pid, None)
        from gpu_worker.manifests import get

        model = (row.get("next") or {}).get("model") or row["id"]
        kind = "install" if (row.get("next") or {}).get("kind") == "install" else "download"
        # Named for the model whose files it fetches (EdgeTAM video runs EdgeTAM's: the offer is EdgeTAM's download).
        entry = get(model)
        name = getattr(entry, "name", "") or row["name"]
        own_python = bool(entry and entry.env)
        pid = next((pid for pid, p in proposals.items() if p["model"] == model), None) or f"p{next(ids)}"
        proposals[pid] = {"model": model, "name": name, "says": row["next"]["says"], "at": now, "kind": kind,
                          "own_python": own_python}
        shared = f" (its files are {name}'s)" if name != row["name"] else ""
        what = (f"download {name} ({row['next']['says'].removeprefix('Download ')})" if kind == "download" else
                f"install {name} (its own Python, made by the app from its pinned lockfile; a few minutes)" if own_python else
                f"{row['next']['says']} ({row['next'].get('how', name)})")
        return (f" Offered ({pid}){shared}: ask the user whether to {what}; on their yes call get_model with model "
                f"{name!r} (or 'all' for every one offered) and asked_with quoting their reply.")

    def offer_download(model: str) -> str:
        """The offer a refusal carries (choosing.py's `use`, `set_default`): when what stops a model is its download, it
        is offered exactly as `models` offers it, so "can you use it now?" ends in "Want me to download it?"."""
        from gpu_worker import manifests as catalog_

        m = catalog_.get(model)
        key = m.key if m is not None else model
        row = next((r for r in wire().get("models") or [] if r["id"] == key), None)
        if row is None or (row.get("next") or {}).get("kind") != "download":
            return ""
        return propose(row)

    services.offer_download = offer_download

    def offer(rows: list[dict]) -> str:
        """Each download a row could take next, offered; a key step pointed at its card."""
        out = []
        for r in rows:
            nxt = r.get("next") or {}
            if r.get("status") != "needs-setup" or r.get("download", {}).get("state") in ("queued", "downloading", "checking"):
                continue
            if nxt.get("kind") in ("download", "install") and nxt.get("model"):
                out.append(f"{r['name']}:" + propose(r))
            elif nxt.get("kind") == "key":
                out.append(f"{r['name']}: needs a key first. {KEYS_ARE_THEIRS}")
            elif nxt.get("kind") == "licence":
                out.append(f"{r['name']}: its licence must be accepted on its page ({nxt.get('url')}) with the account "
                           "the user's Hugging Face key belongs to, before it downloads.")
        return "\n".join(out)

    def maintained(update: dict | None) -> str:
        """Its maintenance in the same words as its card (maintenance/status.py): the policy a person recorded and what
        is happening to its next version. The agent may propose a policy, quoting the user's words; it never records
        one: the user does, on the screen."""
        if not update:
            return ""
        policy = update.get("policy") or {}
        line = f" Maintenance: {policy.get('words', 'Update when I choose')}"
        if policy.get("less"):
            line += f" (for now as Review updates: {policy['less']})"
        return line + (f"; {update['says']}." if update.get("says") else ".")

    async def models(ctx: ToolContext, args: dict) -> str:
        from ... import catalog_view

        w = wire()
        rows, keys = w.get("models") or [], (w.get("setup") or {}).get("keys") or {}
        if args.get("new"):  # New for you (catalog_news.py): what others added that this Mac hasn't known, offered only
            from ... import catalog_news

            fresh = ((w.get("setup") or {}).get("news") or {}).get("new") or []
            found = [r for r in rows if r["id"] in {n["key"] for n in fresh}]
            more = offer(found)
            return catalog_news.words_for_agent(fresh) + (f"\nTo turn one on:\n{more}" if more else "")
        about = str(args.get("about") or "").strip()
        card = job_card(w, about) if about else None
        if card is not None:
            from ...capability_shelf import words

            offered = offer_capability(ctx, card)
            return words({"cards": [card]}) + (f"\n{offered}" if offered else "") + "\n" + KEYS_ARE_THEIRS
        if about and about.lower() not in ("everything", "all", "setup", "what do i need", "status"):
            key = catalog_view.key_matching(keys, about)
            found = catalog_view.matching(rows, about)
            # What its card says about the outside world (a licence's access, a key) is asked again first, so the
            # answer is today's: "I accepted the licence" is seen at once (observed.py).
            asked = [r for r in found[:2] if any(f.get("observed") and f.get("ok") is not True for f in r.get("facts", []))]
            if asked and getattr(services, "observe", None) is not None:
                for r in asked:
                    await services.observe(r["id"])
                w = wire()
                rows, keys = w.get("models") or [], (w.get("setup") or {}).get("keys") or {}
                found = catalog_view.matching(rows, about)
            if key is not None and not found:
                return catalog_view.key_words(key, rows) + " " + KEYS_ARE_THEIRS
            if not found:
                return (f"no model or job in the catalog is called {about!r}. Ask with nothing named for the whole list, "
                        "or name a model (SAM 3.1), a job (outlining) or a key (Google).")
            more = offer(found)
            kept = (w.get("setup") or {}).get("maintenance") or {}
            return "\n".join(catalog_view.row_words(r) + maintained(kept.get(r["id"])) for r in found[:8]) + \
                (f"\n{more}" if more else "")
        more = offer([r for r in rows if r["group"] == "needs-you"])
        from ... import capability_shelf

        shelf = (w.get("setup") or {}).get("shelf")
        jobs = ("\n" + capability_shelf.words(shelf)) if shelf else ""
        offered = [offer_capability(ctx, card) for card in (shelf or {}).get("cards", [])]
        if any(offered):
            jobs += "\n" + "\n".join(x for x in offered if x)
        return catalog_view.overview(rows, keys) + jobs + (f"\nTo turn on what the user's choices need:\n{more}" if more else "")

    registry.add(Tool(
        "models", "Which models run here, which don't and why, and how to turn each on: the Models page in words. Ask with "
        "nothing named for what the user's choices need (\"what do I need to set up?\"), or name a model (\"SAM 3.1\"), a "
        "job (\"outlining\") or a key (\"my Google key\": what it turns on). Answers in the past and present, never a "
        "promise that one key turns everything on. A download it offers starts only on the user's yes (get_model). "
        "new: what others added to the catalog that this Mac hasn't known yet (\"anything new?\"), never turned on by itself.",
        schema({"about": {"type": "string", "description": "a model, a job or a key (optional: nothing for the whole setup)"},
                "new": {"type": "boolean", "description": "what is new in the catalog since the user last looked"}}),
        models, kind="query", inline_ms=500, family="conversation"))

    def names_it(row: dict, words: str) -> bool:
        """Whether the user's own words name this model: its name, its id, or its family's word ("DINOv3" for DINOv3
        small), as whole words."""

        def norm(text: str) -> str:
            return " ".join(re.sub(r"[^a-z0-9.]+", " ", text.lower()).split())

        said, name = f" {norm(words)} ", norm(row["name"]).split()
        return any(f" {n} " in said for n in (" ".join(name), norm(row["id"]), name[0] if name else "") if n)

    async def get_model(ctx: ToolContext, args: dict) -> str:
        from ...downloads import Failed
        from ...research.policy import quoted
        from .research import recent_request

        # The offers made in the last PROPOSAL_S, picked by the offer's id, the model's name (what the conversation says:
        # the next turn sees the answer, not the tool's result), or "all" for every one offered.
        now = time.time()
        fresh = {pid: p for pid, p in proposals.items() if now - p["at"] <= PROPOSAL_S}
        wanted = str(args.get("confirms") or args.get("model") or "").strip()
        if wanted in fresh:
            chosen = [wanted]
        elif wanted.lower() in ("all", "everything", "every one", "all of them", "them", "everything offered"):
            chosen = list(fresh)
        else:
            from ... import catalog_view

            names = {r["id"] for r in catalog_view.matching(
                [{"id": p["model"], "name": p["name"], "usedFor": []} for p in fresh.values()], wanted)}
            chosen = [pid for pid, p in fresh.items() if p["model"] in names]
        if not chosen and wanted:
            # The user asked for it in so many words ("please download DINOv3"): their request is the yes, so it is
            # offered and taken at once rather than asked back (live 2026-10-12: "Would you like me to download it now?"
            # after they had asked). Their words are still quoted and checked below, and only a download its card offers.
            from ... import catalog_view

            found = [r for r in catalog_view.matching(wire().get("models") or [], wanted)
                     if (r.get("next") or {}).get("kind") in ("download", "install") and (r.get("next") or {}).get("model")]
            # Their own words must name it: a bare "yes" to nothing offered is still no request.
            if len(found) == 1 and names_it(found[0], str(args.get("asked_with") or "")):
                propose(found[0])
                fresh = {pid: p for pid, p in proposals.items() if time.time() - p["at"] <= PROPOSAL_S}
                chosen = [pid for pid, p in fresh.items()
                          if p["model"] == ((found[0].get("next") or {}).get("model") or found[0]["id"])]
        if not chosen:
            raise ToolFailed("that download was not offered just now", next="ask models about it, then offer it again")
        if not quoted(str(args.get("asked_with") or ""), recent_request(services, ctx), least=1):
            raise ToolFailed("a download starts on the user's own yes", next="quote their reply in asked_with")
        if services.downloads is None:
            raise ToolFailed("downloads are not available here", next=None)  # no next: a copy of the app without them
        from ...readiness import size_words

        started, refused = [], []
        for pid in chosen:
            proposal = fresh[pid]
            if proposal.get("kind") == "install":  # a managed environment or tool generation, through the same queue
                from ...envs import Failed as InstallFailed

                try:
                    row = services.installs.start(proposal["model"], by="you, by asking")
                except (InstallFailed, AttributeError) as exc:
                    refused.append((proposal["name"], str(exc), None))
                    continue
                proposals.pop(pid, None)
                started.append(f"installing {proposal['name']}'s own Python ({row.get('packages', 0)} packages)"
                               if proposal.get("own_python") else f"{proposal['name']}: {proposal['says']} queued")
                continue
            try:
                row = services.downloads.start(proposal["model"], by="you, by asking")
            except Failed as exc:
                nxt = {"key": f"open_models at {proposal['name']} so the user can add the key there",
                       "licence": f"the user accepts its licence at {exc.url}, then asks again",
                       "disk": "the user makes room on the disk"}.get(exc.step)
                refused.append((proposal["name"], exc.says, nxt))
                continue
            proposals.pop(pid, None)
            started.append(f"downloading {proposal['name']}" + (f" ({size_words(row['total'])})" if row.get("total") else ""))
        if not started:
            name, says, nxt = refused[0]
            raise ToolFailed(f"{name}: {says}", next=nxt)  # next: from the download's own step
        return (f"started {', '.join(started)} into the catalog's folder, one at a time beside the live view; "
                "each shows on the Models page and in Activity, and is checked before it is used. Say it has started, "
                "never that it is done."
                + "".join(f" {name} could not start: {says}" + (f" ({nxt})" if nxt else "") + "." for name, says, nxt in refused))

    registry.add(Tool(
        "get_model", "Start downloading a model, on the user's own words: their yes to what the models tool offered, or "
        "their asking for it by name (\"download DINOv3\"). model is its name (or 'all' for every one offered in the last "
        "few minutes), asked_with quotes their words. Never without them; never for a key (keys are theirs to add).",
        schema({"model": {"type": "string", "description": "the offered model's name, or 'all'"},
                "confirms": {"type": "string", "description": "the offer's id (p1), when known (optional)"},
                "asked_with": {"type": "string", "description": "the user's own reply, quoted"}}, required=["model", "asked_with"]),
        get_model, kind="effect", inline_ms=1000, family="conversation"))

    async def turn_on(ctx: ToolContext, args: dict) -> str:
        """Turn a job on, as its card's Turn on does, on the user's own yes (capability_shelf.py)."""
        import asyncio
        import re

        from ...research.policy import quoted
        from .research import recent_request

        said_by_user = recent_request(services, ctx)
        if not quoted(str(args.get("asked_with") or ""), said_by_user, least=1):
            raise ToolFailed("a capability is turned on on the user's own yes", next="quote their reply in asked_with")
        card = job_card(wire(), str(args.get("job") or ""))
        if card is None:
            raise ToolFailed(f"no job is called {args.get('job')!r}", next="ask models with nothing named for the jobs")
        # A real quotation is necessary but not sufficient: it must authorize these named terms, in this owner.
        words = ctx.question if ctx.channel != "voice" else said_by_user
        normalized = f" {norm_job(words or '')} "
        negative = re.search(r"\b(?:no|not|never|don['’]?t|cancel|stop)\b", (words or "").lower())
        named = "(?:" + "|".join(re.escape(norm_job(n)) for n in (card["id"], card["title"])) + ")"
        direct = re.fullmatch(r"(?:please )?(?:(?:can|could|would) you (?:please )?|i want you to )?"
                              r"(?:turn on|enable|activate|set up) (?:the )?" + named + r"(?: please)?",
                              normalized.strip()) and not negative
        fresh = {pid: p for pid, p in capability_offers.items() if time.time() - p["at"] <= PROPOSAL_S
                 and p["owner"] == owner(ctx) and p["job"] == card["id"] and p["terms"] == terms(card)}
        pid = str(args.get("confirms") or "")
        if not pid and len(fresh) == 1:
            pid = next(iter(fresh))
        agrees = re.search(r"\b(?:yes|yeah|yep|sure|okay|ok|go ahead|please do|do it)\b", normalized) and not negative
        if not direct and not (pid in fresh and agrees):
            raise ToolFailed("a capability is turned on on the user's own yes to its current offer, or an explicit named request",
                             next="ask models about this job, then offer its setup")
        if pid in fresh:
            capability_offers.pop(pid, None)
        if card["colour"] == "green":
            return f"{card['title']} is on already" + (f" (using {card['using']})." if card['using'] else "; its proved alternative is available on the card.")
        if card["colour"] == "grey":
            raise ToolFailed(card["word"], next=None)  # no next: nothing of it can run on this Mac, said why
        try:
            started = await asyncio.to_thread(setup_service, card["id"], asked=ctx.asked, expected=card.get("turnOn"))
        except ValueError as exc:
            raise ToolFailed(str(exc), next="review this job's current setup with models") from exc
        steps = ((card.get("turnOn") or {}).get("steps")) or []
        theirs = [s for s in steps if s.get("by") == "you"]
        said = f"Turning on {card['title']} ({(card.get('turnOn') or {}).get('label', '')}): " + \
            "; ".join(s.get("says", "") for s in steps) + f". {card['privacy']}; {card['cost'].lower() or 'its cost is the provider\'s'}."
        if theirs:
            said += (" The user's own step comes first on its card: " + theirs[0].get("says", "") + ". "
                     + (KEYS_ARE_THEIRS if theirs[0].get("kind") == "key" else "open_models shows its card."))
        from ...untrusted import block
        return block(said + (f" Now: {started.get('says')}." if started.get("says") else ""),
                     tag="setup-outcome", source="catalog and setup queues", limit=5000)

    if setup_service is not None:
        registry.add(Tool(
            "turn_on", "Turn a capability on, as its card's Turn on does: every step it needs, the app's own (a download, an "
            "install) done by the app, the user's own (a key, a licence, allowing a community catalog) asked on its card. "
            "job is what it does (\"Find every cell\", \"Talk with you\") or its id. Only on the user's own yes, quoted in "
            "asked_with; never for a key's value (keys are theirs to add on the card).",
            schema({"job": {"type": "string", "description": "the job, as the models tool lists it"},
                    "confirms": {"type": "string", "description": "the capability offer id (c1) the user accepted"},
                    "asked_with": {"type": "string", "description": "the user's own words asking for it, quoted"}},
                   required=["job", "asked_with"]),
            turn_on, kind="effect", inline_ms=1000, family="conversation"))

    async def open_models(ctx: ToolContext, args: dict) -> str:
        from ... import catalog_view

        if services.open_page is None:
            raise ToolFailed("the studio can't be asked to open a page here", next=None)  # no next: no studio to show it
        about = str(args.get("model") or "").strip()
        row = None
        if about:
            w = wire()
            rows = w.get("models") or []
            found = catalog_view.matching(rows, about)
            key = catalog_view.key_matching((w.get("setup") or {}).get("keys") or {}, about) if not found else None
            if key is not None:  # a key named ("add my OpenAI key"): the card of a model that waits on it, chosen first
                found = sorted((r for r in rows if (r.get("next") or {}).get("service") == key.get("service")),
                               key=lambda r: r["group"] != "needs-you")
            if not found and key is None:
                raise ToolFailed(f"no model in the catalog is called {about!r}", next="ask models with nothing named")
            row = found[0] if found else None
        # A key named that nothing waits on (already added): the Toolbox's Keys band, where it can be changed.
        target = f"#models/{row['id']}" if row else "#models/keys" if about and key is not None else "#models"
        shown = await services.open_page(getattr(ctx, "client", None), target)
        where = (f"{row['name']}'s card on the Models page" if row else
                 "the keys on the Models page" if target == "#models/keys" else "the Models page")
        state = ""
        if about and key is not None:
            state = (f"; the {key.get('short')} key is already added ({key.get('sourceSays') or 'it is there'}), and Change "
                     "is there" if key.get("present") else f"; the {key.get('short')} key is not added yet")
        if shown is None or not shown.get("shown"):
            raise ToolFailed(f"the studio did not say it shows {where}", next="ask the user to open it from the chip at the top")
        if not shown.get("visible", True):
            return f"opened {where} in the studio; it shows when the user looks at the studio again{state}"
        action = (row or {}).get("next") or {}
        how = {"key": "its Add key is there", "download": "its Download is there", "licence": "its licence page is linked there"}
        return f"the studio shows {where}" + (f"; {how[action['kind']]}" if action.get("kind") in how else "") + state

    registry.add(Tool(
        "open_models", "Open the Models page in the studio, at one model's card when named (its Add key, Download or "
        "licence page): \"add my OpenAI key\" opens OpenAI's model card. Done once the studio says it shows it.",
        schema({"model": {"type": "string", "description": "a model's name, or a job (optional)"}}),
        open_models, kind="effect", inline_ms=4000, family="conversation"))
