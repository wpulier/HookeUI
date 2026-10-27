"""Models chosen "from now on" (`set_default`, the Models menu), kept until changed and said again on a restart.

The user's 2026-10-11 decisions Q14 and Q15: the vision model and every other model role can be switched by asking,
for one request (`use`) or from now on; from now on is kept across restarts and days until the user changes it (the
user's preferences, day/prefs.py) and said on restart ("Outlining still uses SAM 2.1 tiny, as you asked at 02:41";
a choice of an earlier day says the day: "as you asked on Oct 11 at 02:41"), or said when it can't run any more.

One way to change a setting (`change`): the Models menu (commands.settings) and `set_default` go through it, so
both keep the choice (the preference `defaults`: group -> {option, at, by}) and start the same work a change
needs (a picture model loads, MedSigLIP warms). It is never repeated in the day's digest: the models are told
the settings where they need them (day/context.py).

A restored choice is checked as runnable before it is applied (options.unavailable: this edition, its key, its code
installed here); one that can't run is said, and the code's own default stays. Loading a restored model ahead of its
first use is Slice 1's (the perf work): RESTORED is where it hooks in.
"""
from __future__ import annotations

import time
from typing import Any, Callable

PREF = "defaults"
# Called with (hub, restored) after a restart applied the kept choices: Slice 1 preloads a restored model here.
RESTORED: list[Callable[[Any, list[dict]], None]] = []


def kept_groups() -> tuple[str, ...]:
    """The settings whose choice is kept: every model role (names.SETTINGS) and the operations' settings
    (outline, cells, embed)."""
    from . import names
    from .operations import OPERATIONS

    return tuple(dict.fromkeys([*(op.selected_by for op in OPERATIONS.values() if op.selected_by), *names.SETTINGS]))


# Settings kept until changed that no tool may set (choosing.kept_groups is set_default's list): sending the library's
# pictures is the user's own choice in the Behaviour menu, kept across restarts once made.
KEPT_SETTINGS = ("library_looks",)


def kept_settings() -> tuple[str, ...]:
    """Every setting whose choice is kept across restarts: the model roles and operations, and KEPT_SETTINGS."""
    return (*kept_groups(), *KEPT_SETTINGS)


def record(day, changes: dict, applied: dict, by: str, clock: Callable[[], float] | None = None) -> None:
    """Keep the model choices among `changes` that were applied (`applied`: the settings' wire after)."""
    if day is None:
        return
    clock = clock or time.time  # read when called, never a clock kept from import (test_golden's clock guard)
    groups = kept_settings()
    kept = dict(day.prefs.get(PREF) or {})
    for group, option in changes.items():
        if group in groups and applied.get(group) == option:
            kept[group] = {"option": option, "at": clock(), "by": by}
    if kept != (day.prefs.get(PREF) or {}):
        day.set_pref(PREF, kept)


def restore(settings, day) -> list[dict]:
    """Apply the kept choices to `settings`, each checked as runnable first: [{group, option, at, by,
    problem}], `problem` saying why one could not be applied ("" when it was)."""
    from .options import GROUPS, unavailable

    out = []
    for group, entry in sorted((day.prefs.get(PREF) or {}).items() if day is not None else []):
        if not isinstance(entry, dict):
            continue
        option = entry.get("option")
        if group not in kept_settings() or option not in GROUPS.get(group, {}):
            problem = f"there is no {option!r} for it any more"
        else:
            problem = unavailable(group, option) or "; ".join(settings.update({group: option}))
        out.append({"group": group, "option": option, "at": entry.get("at"), "by": entry.get("by"), "problem": problem})
    return out


def resume(settings, day) -> list[dict]:
    """The kept choices that are not in effect now and can run now (a model downloaded, its code installed, its key
    added since): [{group, option, at, by}], not yet applied. A restart that couldn't apply one used the default; the
    moment it can run, it is the user's choice again (App.resume_defaults), never only at the next restart (the catalog's
    phase 3, live 2026-10-12: DINOv3 downloaded again and look-alikes stayed on DINOv2 until a restart)."""
    from .options import GROUPS, unavailable

    out = []
    for group, entry in sorted((day.prefs.get(PREF) or {}).items() if day is not None else []):
        if not isinstance(entry, dict):
            continue
        option = entry.get("option")
        if group not in kept_settings() or option not in GROUPS.get(group, {}) or getattr(settings, group, None) == option:
            continue
        if not unavailable(group, option):
            out.append({"group": group, "option": option, "at": entry.get("at"), "by": entry.get("by")})
    return out


def resumed_words(back: list[dict], clock: Callable[[], float] | None = None) -> str:
    """What is said when kept choices apply again: "Look-alikes uses DINOv3 small (on this Mac) again: it can run now,
    as you asked at 22:46."."""
    from . import capabilities
    from .capabilities import said_option
    from .options import GROUPS

    titles = {r["id"]: r.get("title", r["id"]) for r in capabilities.load()["roles"]}
    clock = clock or time.time
    today = time.strftime("%Y-%m-%d", time.localtime(clock()))
    out = []
    for r in back:
        label = said_option(r["group"], {"id": r["option"], "label": GROUPS[r["group"]][r["option"]].label})
        who = "as you asked" if r.get("by") in ("user", "menu") else "as chosen"
        out.append(f"{titles.get(r['group'], r['group'])} uses {label} again: it can run now, {who} {_when(r.get('at'), today)}.")
    return " ".join(out)


def words(restored: list[dict], settings, clock: Callable[[], float] | None = None) -> str:
    """What a restart says of the kept choices, one sentence each; one made on an earlier day says the day."""
    from . import capabilities
    from .names import said
    from .capabilities import said_option
    from .options import GROUPS

    titles = {r["id"]: r.get("title", r["id"]) for r in capabilities.load()["roles"]}
    clock = clock or time.time  # read when called, never a clock kept from import (test_golden's clock guard)
    today = time.strftime("%Y-%m-%d", time.localtime(clock()))
    out = []
    for r in restored:
        group, option = r["group"], r["option"]
        title = titles.get(group, group)
        label = (said_option(group, {"id": option, "label": GROUPS[group][option].label})
                 if option in GROUPS.get(group, {}) else str(option))
        when = _when(r.get("at"), today)
        who = "as you asked" if r.get("by") in ("user", "menu") else "as chosen"
        if r["problem"]:
            now = getattr(settings, group, None) if group in GROUPS else None
            instead = (said(GROUPS[group][now].label, "") if now in GROUPS.get(group, {}) else "the default")
            out.append(f"{title} was set to {label} {when}, but it can't run now ({r['problem']}): {instead} is used.")
        else:
            out.append(f"{title} still uses {label}, {who} {when}.")
    return " ".join(out)


def _when(at, today: str) -> str:
    """When a choice was made: "at 02:41" today, "on Oct 11 at 02:41" on an earlier day."""
    if not isinstance(at, (int, float)):
        return "earlier"
    t = time.localtime(at)
    day = "" if time.strftime("%Y-%m-%d", t) == today else time.strftime("on %b %-d ", t)
    return day + time.strftime("at %H:%M", t)


def change(hub, day, changes: dict, by: str, *, warm: Callable[[dict, list[str]], None] | None = None,
           publish: Callable[[], None] | None = None) -> list[str]:
    """Change settings from now on: apply them, keep the model choices, start what a change needs (`warm`,
    with the problems), tell every studio (`publish`). Returns the problems of the changes that were refused (said
    by the caller)."""
    problems = hub.settings.update(changes)
    record(day, changes, hub.settings.to_wire(), by)
    if warm is not None:
        warm(changes, problems)
    if publish is not None:
        publish()
    return problems
