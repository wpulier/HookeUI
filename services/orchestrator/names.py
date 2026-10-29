"""What a model's name means: the one place a name the user, a model or the app says becomes an id.

Live 2026-10-11 (typed chat): asked for the outline options, the brain shortened "SAM 3.1 (on this Mac)" to
"SAM 3.1 (local)", and its own `use` refused that name (a prefix-and-substring guess found nothing); the labels
the app itself showed ("EdgeTAM (on this Mac)", every cell finder's) were refused too, and labels with commas
("micro-sam, light microscopy") made every list joined with ", " ambiguous.

- **One name each.** An implementation's label (operations.OPERATIONS) and an option's label (options.GROUPS) is its
  one display name: no commas, and no place it runs unless two variants differ only by that ("SAM 3.1 (cloud)",
  "SAM 3.1 (this Mac)"). Where it runs is a field of its own (`Entry.where`), said beside the name (`said`).
- **Its keys:** its id and option id; its name; its name with where it runs, said every way the app or a person says
  it (WHERE); its labels before 2026-10-11, kept for one release (OLD_LABELS); its family's name and the family's
  spoken or short forms (FAMILIES, FAMILY_ALIASES), and those with where it runs.
- **A family named alone** means the family's first member the operation has: its default variant ("SAM 3.1" is
  SAM 3.1 (cloud)), and the receipt says so (`Meaning.family`). Who asks when that one can't run is the caller's
  (choosing.pick: only when another of the family can).
- **Matching is exact on keys** (`key`: lower case; brackets, commas, semicolons, colons, quotes, hyphens and
  underscores as spaces). Never a prefix or a part of a name. A key meaning two things in one namespace is an error
  when the index is built (tests/test_names.py).

Namespaces: each operation by its id ("outline", "cells"...), each option group as "option:<group>", and each model
fixed in the code as its capability role ("fixed:find-all"; contract/capabilities.json), so every name a model is
shown means something here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

_PUNCTUATION = re.compile(r"[()\[\],;:\"'‘’“”\-_]+")

# Where a model runs, each way it is said. The first is how the app says it in brackets; SAID_PLAIN after a name
# that has brackets of its own ("micro-sam (light microscopy) on this Mac").
WHERE = {
    "mac": ("on this Mac", "this Mac", "on the Mac", "local", "locally"),
    "cloud": ("cloud", "in the cloud", "the cloud", "fal", "fal cloud", "on fal", "fal.ai"),
}
SAID_PLAIN = {"mac": "on this Mac", "cloud": "in the cloud"}

# Families: a name people use for several variants, its members in order. The first member an operation has is
# what the family named alone means there (its default variant, the user's 2026-10-11 decision Q18).
FAMILIES: dict[str, tuple[str, ...]] = {
    "SAM 3.1": ("sam3.1-fal", "sam3.1-local", "sam3.1-all"),
    "EdgeTAM then SAM 3.1": ("edgetam>sam3.1-fal", "edgetam>sam3.1-local"),
    "SAM 2.1": ("sam2.1-tiny",),
    "EdgeTAM": ("edgetam", "edgetam-automask", "edgetam-video"),
    "Cellpose-SAM": ("cellpose-sam", "cellpose-sam-tuned"),
    "micro-sam": ("micro-sam-lm",),
    "DINOv2": ("dinov2-small",),
    "DINOv3": ("dinov3-small",),
    "DINO": ("dinov2-small", "dinov3-small"),  # "the dino model" (live 2026-10-11 16:28:55): its default, DINOv2 small
    "BioCLIP": ("bioclip-2",),
    "MedSigLIP": ("medsiglip-448",),
    "Trackastra": ("trackastra",),
    "k-means": ("kmeans-silhouette",),
}
# How people say a family aloud or for short: the same as saying the family.
FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "SAM 3.1": ("sam3.1", "sam 3", "sam3", "sam three point one", "sam three one"),
    "EdgeTAM then SAM 3.1": ("edgetam and sam 3.1", "edge tam then sam 3.1"),
    "SAM 2.1": ("sam2.1", "sam 2", "sam2", "sam two point one", "sam two one", "sam 2 1"),
    "EdgeTAM": ("edge tam",),
    "Cellpose-SAM": ("cellpose", "cell pose", "cell pose sam"),
    "micro-sam": ("microsam", "micro sam lm"),
    "DINOv2": ("dino v2", "dino v 2"),
    "DINOv3": ("dino v3", "dino v 3"),
    "BioCLIP": ("bio clip", "bioclip 2"),
    "MedSigLIP": ("med siglip", "medsiglip 448"),
    "Trackastra": ("track astra", "general 2d", "general 2d trackastra"),
    "k-means": ("kmeans",),
}
# The labels before 2026-10-11, kept for one release: a model or a person may still say them (logs, old answers).
OLD_LABELS: dict[str, tuple[str, ...]] = {
    "edgetam": ("EdgeTAM (on this Mac)",),
    "sam2.1-tiny": ("SAM 2.1 tiny (on this Mac)",),
    "sam3.1-local": ("SAM 3.1 (on this Mac)",),
    "sam3.1-fal": ("SAM 3.1 (fal cloud)",),
    "edgetam>sam3.1-fal": ("EdgeTAM, then SAM 3.1 (fal)",),
    "edgetam>sam3.1-local": ("EdgeTAM, then SAM 3.1 (on this Mac)",),
    "micro-sam-lm": ("micro-sam, light microscopy", "micro-sam, light microscopy (on this Mac)"),
    "cellpose-sam": ("Cellpose-SAM (on this Mac)",),
    "cellpose-sam-tuned": ("Cellpose-SAM, fine-tuned on your corrections",
                           "Cellpose-SAM, fine-tuned on your corrections (on this Mac)"),
    "edgetam-automask": ("EdgeTAM automatic masks (on this Mac)",),
    "sam3.1-all": ("SAM 3.1 (fal cloud), every match",),
    "vision-boxes": ("The vision model's boxes, outlined",),
    "kmeans-silhouette": ("k-means, the number of groups by silhouette",),
    "nearest-named": ("The nearest named group, by look",),
}
OLD_OPTION_LABELS: dict[tuple[str, str], tuple[str, ...]] = {
    ("vision", "gemini-3.8-flash-hires"): ("Gemini 3.8 Flash, high resolution",),
    ("pointing", "snap-first"): ("Instant guess, then box",),
    ("pointing", "molmo"): ("Molmo 2 points (on this Mac)",),
    ("brain", "fc"): ("Function calls, answer first",),
    ("quality", "flag"): ("Name it anyway, note it",),
    ("style", "classic"): ("Filled, on the edge",),
    ("style", "edge"): ("Line just outside, no fill",),
    ("menu", "compact"): ("Grouped, first sentences",),
    ("menu", "brief"): ("Every tool, the longest briefly",),
    ("admit", "quote"): ("The model says, the app checks",),
    ("dust", "filter"): ("Tell, and drop outlines on dust",),
    ("zeroshot", "medsiglip-subject"): ("MedSigLIP, for blood and Pap smears",),
    ("zeroshot", "medsiglip-always"): ("MedSigLIP, on every click",),
    ("session_context", "on"): ("Referenced, capped",),
}


def key(text) -> str:
    """A name as compared: lower case, brackets, commas, semicolons, colons, quotes, hyphens and underscores as
    spaces, one space between words."""
    if "#" in str(text):
        return str(text).strip()
    return " ".join(_PUNCTUATION.sub(" ", str(text or "").lower()).split())


_WHERE_KEYS = {where: tuple(key(w) for w in words) for where, words in WHERE.items()}


def says_where(name: str) -> str:
    """Where a name itself says it runs ("SAM 3.1 (cloud)": "cloud"), or ""."""
    said = key(name)
    for where, words in _WHERE_KEYS.items():
        if any(said == w or said.endswith(" " + w) for w in words):
            return where
    return ""


def said(name: str, where: str) -> str:
    """A name with where it runs, as the app says it: "EdgeTAM (on this Mac)", "Gemini 3.8 Flash (cloud)",
    "micro-sam (light microscopy) on this Mac"; a name that says where already ("SAM 3.1 (cloud)") as it is."""
    if not where or where not in WHERE or says_where(name):
        return name
    return f"{name} {SAID_PLAIN[where]}" if "(" in name else f"{name} ({WHERE[where][0]})"


def said_impl(impl_id: str | None) -> str:
    """An implementation's name with where it runs ("EdgeTAM (on this Mac)"), from any operation; the id itself when it
    is none of them."""
    from .operations import OPERATIONS

    for op in OPERATIONS.values():
        found = op.impl(impl_id) if impl_id else None
        if found is not None:
            return said(found.label, where_of(found))
    return impl_id or ""


@dataclass(frozen=True)
class Entry:
    """One thing a name can mean in a namespace."""
    id: str
    name: str
    where: str = ""  # "mac", "cloud" or "" (app code, a way of working)
    choosable: bool = True  # a request may be given it (`use`); declared fallbacks and one-way operations are not
    impl: str = ""  # the implementation it is (an option of OUTLINE, CELLS or EMBED), else ""


@dataclass(frozen=True)
class Meaning:
    """What a name said in a namespace means."""
    id: str
    name: str
    family: str = ""  # the family named alone: `id` is its first member here (its default variant)
    members: tuple[str, ...] = ()  # the family's members here, the default first
    old: bool = False  # an old label (kept for one release)
    others: tuple[str, ...] = ()  # the names of the family's other members here (resolve fills them)

    def note(self) -> str:
        """What the receipt says when a family was named alone and has other members here ("" otherwise)."""
        if not self.family or not self.others:
            return ""
        return f"{self.family} named alone is its default, {self.name}; the other: {' or '.join(self.others)}"


@dataclass
class Namespace:
    entries: dict[str, Entry] = field(default_factory=dict)
    keys: dict[str, Meaning] = field(default_factory=dict)

    def put(self, text: str, meaning: Meaning, where: str) -> None:
        k = key(text)
        if not k:
            return
        found = self.keys.get(k)
        if found is not None and found.id != meaning.id:
            raise ValueError(f"{where}: {text!r} would mean both {found.id} and {meaning.id}")
        if found is None or (found.family and not meaning.family):
            self.keys[k] = meaning  # a direct key wins over the same id reached by its family


def one_way(op) -> bool:
    """An operation done one way only (following, measuring motion, grouping...): there is no model to choose."""
    return len(op.implementations) == 1


def choosable(op, impl) -> bool:
    """Whether a request can be given `impl` for `op`: the operation has a choice, `impl` runs as the operation (an
    adapter), and it is one of the operation's options, or the operation has no options and it is the operation's own
    (a declared fallback only, like the spot ring or the vision model's boxes, is never chosen)."""
    from .operations import ADAPTERS

    if one_way(op) or (op.id, impl.id) not in ADAPTERS:
        return False
    return impl.option is not None or (op.selected_by is None and impl is op.implementations[0])


def where_of(impl) -> str:
    """Where an implementation runs: its worker's place ("cloud", "mac"), "" for app code."""
    if impl.worker == "cloud":
        return "cloud"
    if impl.worker in ("interactive", "analysis", "follow", "molmo") or (impl.worker == "cpu" and impl.kind == "model"):
        return "mac"
    return ""


def _fill(space: Namespace, label: str, entries: list[Entry], old: dict[str, tuple[str, ...]]) -> Namespace:
    """Every key of every entry, then the families' (a direct key wins over a family's for the same words)."""
    for e in entries:
        space.entries[e.id] = e
        direct = Meaning(e.id, e.name)
        for text in (e.id, e.impl, e.name):
            space.put(text, direct, label)
        if e.where and not says_where(e.name):
            for w in WHERE[e.where]:
                space.put(f"{e.name} {w}", direct, label)
        for text in old.get(e.id, ()):
            space.put(text, Meaning(e.id, e.name, old=True), label)
    for family, members in FAMILIES.items():
        here = [space.entries[m] for m in _member_ids(space, members)]
        if not here:
            continue
        ids = tuple(e.id for e in here)
        default = here[0]
        for text in (family, *FAMILY_ALIASES.get(family, ())):
            space.put(text, Meaning(default.id, default.name, family=family, members=ids), label)
            for where, words in WHERE.items():  # the first member that runs there: "SAM 3.1 (local)"
                member = next((e for e in here if e.where == where), None)
                if member is not None:
                    for w in words:
                        space.put(f"{text} {w}", Meaning(member.id, member.name, family=family, members=(member.id,)), label)
    return space


def _member_ids(space: Namespace, members: tuple[str, ...]) -> list[str]:
    """A family's members in this namespace, by implementation id (an option is found by the implementation it is)."""
    by_impl = {e.impl or e.id: e.id for e in space.entries.values()}
    return [by_impl[m] for m in members if m in by_impl]


@lru_cache(maxsize=1)
def index() -> dict[str, Namespace]:
    """Every namespace's keys, built once from the code's own lists. Raises ValueError when a key would mean two
    things in one namespace."""
    from . import capabilities
    from .capabilities import runs
    from .operations import OPERATIONS
    from .options import GROUPS

    spaces: dict[str, Namespace] = {}
    for op in OPERATIONS.values():
        entries = [Entry(i.id, i.label, where_of(i), choosable(op, i), i.id) for i in op.implementations]
        spaces[op.id] = _fill(Namespace(), op.id, entries, OLD_LABELS)
    selects = {op.selected_by: op for op in OPERATIONS.values() if op.selected_by}
    for group, options in GROUPS.items():
        op = selects.get(group)
        entries, old = [], {}
        for o in options.values():
            impl = next((i for i in op.implementations if i.option == o.id), None) if op is not None else None
            where = where_of(impl) if impl is not None else {"both": "cloud"}.get(runs(o.uses), runs(o.uses))
            entries.append(Entry(o.id, o.label, where, True, impl.id if impl is not None else ""))
            old[o.id] = OLD_OPTION_LABELS.get((group, o.id), ()) + (OLD_LABELS.get(impl.id, ()) if impl is not None else ())
        spaces[f"option:{group}"] = _fill(Namespace(), f"option:{group}", entries, old)
    for role in capabilities.load()["roles"]:
        if not role["id"].startswith("fixed:"):
            continue
        entries = [Entry(role["id"], role.get("label", ""), {"both": "cloud"}.get(runs(role.get("uses", ())), runs(role.get("uses", ()))))]
        fallback = role.get("fallback")
        if fallback and key(fallback.get("label")) != key(role.get("label")):
            entries.append(Entry(role["id"] + ":fallback", fallback["label"],
                                 {"both": "cloud"}.get(runs(fallback.get("uses", ())), runs(fallback.get("uses", ())))))
        spaces[role["id"]] = _fill(Namespace(), role["id"], entries, {})
    return spaces


def resolve(namespace: str, text, *, choosable_only: bool = True) -> Meaning | None:
    """What `text` means in `namespace` (an operation's id, "option:<group>" or a fixed role), or None. For an
    operation, only what a request can be given, unless `choosable_only` is False."""
    space = index().get(namespace)
    if space is None:
        return None
    meaning = space.keys.get(key(text))
    if meaning is None:
        return None
    if choosable_only and not space.entries[meaning.id].choosable:
        return None
    if meaning.family:
        members = tuple(m for m in meaning.members if space.entries[m].choosable or not choosable_only)
        others = tuple(space.entries[m].name for m in members if m != meaning.id)
        meaning = Meaning(meaning.id, meaning.name, meaning.family, members, meaning.old, others)
    return meaning


def option(group: str, text) -> Meaning | None:
    """What `text` means among an option group's options ("SAM 3.1 (local)" in "outline": sam3.1-local)."""
    return resolve(f"option:{group}", text, choosable_only=False)


def anywhere(text) -> dict[str, Meaning]:
    """Every namespace `text` means something in, and what (for the tests: every name shown resolves)."""
    out = {}
    for name, space in index().items():
        meaning = space.keys.get(key(text))
        if meaning is not None:
            out[name] = meaning
    return out


def choice_operations() -> list:
    """The operations a request can be given a model for (`use`), in the code's order."""
    from .operations import OPERATIONS

    return [op for op in OPERATIONS.values() if any(choosable(op, i) for i in op.implementations)]


# Settings whose model (or way) can be named, beside the operations (Slice 5, the user's Q14: "the vision model and
# every other model role can be switched by asking"): True when a request can be given one for itself (`use`), False
# when only from now on (`set_default`): the running look is no request's own, and naming a click with MedSigLIP is
# each click's.
SETTINGS: dict[str, bool] = {"vision": True, "fast": True, "outline_check": True, "research": True, "image": True,
                             "presentation": True, "sight": False, "zeroshot": False,
                             "think": False}


def roles(*, request: bool = False, settable: bool = False) -> list[tuple[str, str]]:
    """(role, namespace) for every role a model can be named for: the operations with a choice, then SETTINGS.
    `request`: only those a request can be given for itself (`use`); `settable`: only those with a setting to change
    (`set_default`)."""
    out = [(op.id, op.id) for op in choice_operations() if not settable or op.selected_by]
    out += [(group, f"option:{group}") for group, per_request in SETTINGS.items() if per_request or not request]
    return out


def choosable_ids(settable: bool = False) -> list[str]:
    """Every id a request can be given (`use`), each once: implementations of the operations with a choice, and the
    options of SETTINGS a request can have; `settable`: every id a default can be set to (`set_default`)."""
    out: list[str] = []
    for role, namespace in roles(request=not settable, settable=settable):
        space = index()[namespace]
        for e in space.entries.values():
            if e.choosable and e.id not in out:
                out.append(e.id)
    return out


def choices_line(namespace: str) -> str:
    """One role's choices, "id = name; ...": what a refusal of an unknown name lists."""
    space = index()[namespace]
    return "; ".join(f"{e.id} = {e.name}" for e in space.entries.values() if e.choosable)


def choices_text() -> str:
    """The models a request can be given, once per role ("id = name"), and what a family named alone means: the
    one place the brain is told them (the `use` tool's arguments); `set_default` and `compare_models` take the same
    ids."""
    lines = [f"{role}: {choices_line(namespace)}" for role, namespace in roles()]
    families = []
    for op in choice_operations():
        space = index()[op.id]
        for family, members in FAMILIES.items():
            here = [m for m in _member_ids(space, members) if space.entries[m].choosable]
            if len(here) > 1:
                families.append(f"{family} = {here[0]}")
    later = [group for group, per_request in SETTINGS.items() if not per_request]
    return ("Models by operation or setting, id = name. " + " | ".join(lines) + "."
            + (" A family named alone means its default: " + "; ".join(families) + "." if families else "")
            + (f" {' and '.join(later)}: from now on only (set_default)." if later else ""))
