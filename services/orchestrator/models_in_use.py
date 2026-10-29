"""Who's powering what (Part 2.5; live 2026-10-28: "what models do you have access to" got "Gemini and GPT"): each job
the app does, the model doing it now, where it runs, how fast it answered lately and where to change it.

One builder for the brain's `models_in_use` tool and the performance meter's panel (`/api/health` `models.inUse`), read
from the settings (options.GROUPS and Settings, each choice through `resolve` as the work resolves it), the catalog's
fixed roles (contract/capabilities.json, read from the code's own constant) and, for speed, the calls the app made in
the last ten minutes (research_worker/api_usage.latency: a cloud model's calls by its API name, an operation's runs by
its setting). Cheap: no readiness pass, no provider call, nothing on disk but the cached catalog; the meter polls it.
A job with no recent call says so ("no calls in the last 10 minutes"), never a stale or invented speed.
"""
from __future__ import annotations

from . import options

# (role, the job in plain words, its settings group, what else it says): every job a person asks "who's doing it?" of,
# in the order a conversation meets them. "brain" is the vision model answering with the Acting method (brain/loop.py
# `_look(option=settings.vision, mode=settings.brain)`), so its row names both.
JOBS: tuple[tuple[str, str, str], ...] = (
    ("brain", "Thinking (the brain)", "vision"),
    ("voice", "Talking (the voice)", "voice"),
    ("think", "Thinking behind the voice", "think"),  # the one-brain voice's hard questions and many steps (think.py)
    ("vision", "Seeing (the eyes)", "vision"),
    ("sight", "Running look", "sight"),
    ("fast", "Quick decisions", "fast"),
    ("pointing", "Pointing", "pointing"),
    ("outline", "Outlining", "outline"),
    ("cells", "Cells", "cells"),
    ("embed", "Look-alikes", "embed"),
    ("research", "Researching", "research"),
    ("presentation", "Presentation", "presentation"),
    ("image", "Pictures", "image"),
    ("fixed:video", "Video", ""),
)
WHERE = {"cloud": "cloud", "mac": "this Mac", "both": "cloud and this Mac", "": ""}


def _api_names(option) -> list[str]:
    """The names its calls are recorded under (api_usage.attempt's model): the spec's model ("gemini:gemini-3.8-flash@low"),
    the catalog entry's API version ("veo-3.1-fast-generate-preview") and its key."""
    from gpu_worker.manifests import get

    names = []
    if getattr(option, "spec", ""):
        names.append(option.spec.split(":", 1)[-1].split("@", 1)[0])
    for key in (getattr(option, "model", ""), *getattr(option, "also", ())):
        entry = get(key) if key else None
        if entry is not None and entry.version:
            names.append(entry.version)
        if key:
            names.append(key)
    return list(dict.fromkeys(n for n in names if n))


def _speed(keys: list[str]) -> dict | None:
    from research_worker import api_usage

    best = None
    for key in keys:
        found = api_usage.latency(key)
        if found and (best is None or found["calls"] > best["calls"]):
            best = found
    return best


def _change(role: str, group: str, title: str) -> str:
    """Where a person changes it: the Models menu's job, and by asking when set_default can."""
    from .defaults import kept_groups

    if role == "fixed:video":
        return "fixed in the code"
    if role == "voice" and len(options.GROUPS["voice"]) == 1:
        return "GPT-Live is the only voice here"
    menu = {"brain": "Models › Seeing (the brain answers with it); how it acts: Behaviour › Acting",
            "pointing": "Behaviour › Pointing"}.get(role, f"Models › {title.split(' (')[0]}")
    asking = group in kept_groups() and role not in ("pointing",)
    return menu + ("; or ask: \"use <model> from now on\"" if asking else "")


def rows(settings) -> list[dict]:
    """One row per job: {role, title, model, runs, where, setting, how?, note?, speed, speedWords, change}."""
    from .capabilities import runs

    current = settings.to_wire()  # every choice, read for the list: not reads of the work in progress (options.py)
    out = []
    for role, title, group in JOBS:
        if role == "fixed:video":
            from gpu_worker.manifests import get

            entry = get("veo-3.1-fast")
            row = {"role": role, "title": title, "model": entry.name if entry else "Veo 3.1 Fast", "runs": "cloud",
                   "setting": None, "speed": _speed([entry.version if entry else "", "veo-3.1-fast"])}
        else:
            chosen = current[group]
            group_options = options.GROUPS[group]
            option_id, why = options.resolve(group, chosen) if chosen in group_options else (chosen, "")
            option = group_options.get(option_id)
            where = runs(option.uses) if option is not None else ""
            row = {"role": role, "title": title, "model": option.label if option is not None else str(option_id),
                   "runs": where, "setting": option_id}
            if why:
                row["note"] = f"{option.label if option else option_id} runs instead of the one chosen: {why}"
            if role == "brain":
                row["how"] = options.BRAIN[current["brain"]].label if current["brain"] in options.BRAIN else current["brain"]
            if role == "pointing":
                seeing = options.GROUPS["vision"].get(current["vision"])
                if option is not None and not option.uses and seeing is not None:  # a way of pointing the Seeing model does
                    row.update(how=option.label, model=seeing.label, runs=runs(seeing.uses))
                    option = seeing
            if role == "fast" and option_id == "off":
                row["note"] = "every request waits for the Seeing model's look"
            if role in ("sight", "presentation") and option is not None and not option.uses:
                row["runs"] = ""  # off, or a layout with no model
            if group in ("outline", "cells", "embed"):
                keys = [f"{group}:{option_id}", *(_api_names(option) if option is not None else [])]
            else:
                keys = _api_names(option) if option is not None else []
            row["speed"] = _speed(keys)
        row["where"] = WHERE.get(row["runs"], row["runs"])
        row["change"] = _change(role, group, title)
        row["speedWords"] = speed_words(row)
        out.append(row)
    return out


def speed_words(row: dict) -> str:
    """Its speed as measured: the model's own calls here (every job using that model counts), or why there is none."""
    if row["role"] == "voice":
        return "a live conversation, not timed per call"
    speed = row.get("speed")
    if not speed:
        return "no calls in the last 10 minutes"
    p50 = speed["p50Ms"]
    said = f"{p50 / 1000:.1f} s" if p50 >= 1000 else f"{p50:.0f} ms"
    return f"its calls here typically took {said} (last {speed['calls']} in 10 minutes)"


def text(settings) -> str:
    """What the brain's models_in_use tool says: each job, its model and where it runs, its recent speed, where to
    change it. Names are the app's one names (names.py), so each means one thing to `use`."""
    lines = []
    for row in rows(settings):
        model = row["model"] + (f" ({row['where']})" if row["where"] else "")
        how = f", acting by: {row['how']}" if row["role"] == "brain" else f", {row['how']}" if row.get("how") else ""
        note = f"; {row['note']}" if row.get("note") else ""
        lines.append(f"- {row['title']}: {model}{how}{note}; {row['speedWords']}; change: {row['change']}")
    return ("The models doing each job now (the settings in force; speeds are this app's own calls in the last 10 "
            "minutes). GPT-Live hears and speaks and hands every request to the brain; the one-brain voice sees and acts "
            "itself and hands hard questions and many steps to its thinking:\n" + "\n".join(lines))
