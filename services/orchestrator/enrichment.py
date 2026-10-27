"""Enrichment (session knowledge K1, contract 1 of docs/design/2026-10-07-session-knowledge.md): every model
can add facts about the same members, as a column group beside the measurements.

- **A column group** is `population.columns[gid]`: the execution and implementation that made it, its
  version and basis, the population's revision it was made from and its members, each column's type
  (number with its unit, vector with its model and dimension, category with its source, ranking with its
  calibration), and the values per member. A vector group's numbers are kept apart
  (`population.vectors[gid]`, saved in `vectors.npz`). A group never changes the masks or another group.
- **Enrichments are operations** (scaffolding contract 6: `operations.LOOKS`, `GROUPS`, `LIGHT`, `NAMES`,
  `YOUR_LABEL`), run by the runner, so each has its Execution, and the same inputs (the population at the
  same revision) are a job shared, not run again.
  - *looks*: an embedding per member from its crop, with option EMBED's model, on the same path as
    `seen.py` and `taught.py` (`taught.crop_for`, the heavy worker's `embed`). Vectors of two models are
    never compared: each model's looks are their own group (`looks:dinov2-small`).
  - *groups*: k-means over one looks group, k 2-8 chosen by the silhouette, reported with its score and
    what the groups bench measured (`GROUPS_CALIBRATION`, as data). A group is a group: never a type, a
    health or a viability.
  - *light*: the mean and spread of each colour channel inside each member's own mask (read back off the
    loop, in reserved room, never kept resident); comparable only within one capture.
  - *names*: MedSigLIP's ranking of the subject's candidate names for each group (its representatives'
    crops), as data with its calibration line (measured one picture at a time, said so); the brain decides
    any name.
  - *your label* (naming a group, finding K1-5): the group the user named is kept with the population
    (`named:<model>`), apart from taught.py's examples: never counted towards naming a click's taught line.
    Each member gets its nearest named group by look, with the similarity; the label only from that picture
    model's measured floor (borrowed from the seen-before bench), and none where no floor was measured.
- **One stale check** (`Population.stale`): a group made at revision r is stale once the members are at a
  later revision; every reader goes through it, and it is made again on demand (the job's key carries the
  revision).
- **One grouping read** (`grouping`): the request's own choice of picture model (`use`), else option EMBED's,
  else the newest current grouping by when it was made; named with its model wherever it is said.
- **Pulled, never pushed:** the evidence line names which groups a population has (a few words); values
  reach the brain only through `query_population`, `describe_groups` and the presentation verbs.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import weakref
from typing import Any

import numpy as np

K_MIN, K_MAX = 2, 8
MAX_MEMBERS = 1500  # members embedded at once (a vector group is n x dim x 4 bytes)
REPRESENTATIVES = 5  # per group: the members nearest its centre (shown, named, ranked)
SEED = 7  # clustering is deterministic: the same vectors give the same groups

# What the groups bench measured per picture model (bench/groups_bench.py, docs/bench/groups.md), told with
# the groups as data: the silhouette's number means only what this says (review SF1; never "clear").
GROUPS_CALIBRATION = {
    "dinov2-small": ("Measured on 120 blood cells of 8 kinds (docs/bench/groups.md): the silhouette chose 2 groups "
                     "(0.25), each a mix of several kinds (purity 0.25, ARI 0.20; chance purity 0.12); asked for 8 "
                     "groups, purity was 0.63 (ARI 0.44)."),
    "bioclip-2": ("Measured on 120 blood cells of 8 kinds (docs/bench/groups.md): the silhouette chose 2 groups "
                  "(0.24), each a mix of several kinds (purity 0.25, ARI 0.17; chance purity 0.12); asked for 8 "
                  "groups, purity was 0.66 (ARI 0.44)."),
}
# Names as data are ranked per picture and averaged over a group's representatives: their calibration was
# measured one picture at a time (zeroshot.py), never for a group's average (review SF8).
NAMES_BASIS = ("That was measured one picture at a time; here each group's names are its "
               f"{REPRESENTATIVES} representatives' rankings averaged, which is not measured.")


def looks_id(model: str) -> str:
    return f"looks:{model}"


def groups_id(model: str) -> str:
    return f"groups:{model}"


def named_id(model: str) -> str:
    """The groups the user named, and each member's nearest one (finding K1-5): the picture model's own."""
    return f"named:{model}"


def model_label(model: str | None) -> str:
    """A picture model as the user sees it ("DINOv2 small"), from the looks operation's implementations."""
    from .operations import OPERATIONS

    impl = next((i for i in OPERATIONS["looks"].implementations if (i.option or i.id) == model), None)  # as it is now
    return impl.label if impl is not None else (model or "an unknown picture model")


# --- the column groups ------------------------------------------------------------------------------------
def asked(population) -> tuple[int, list[str], float]:
    """The revision, the members and the time a job is asked at: what its group is stamped with when it lands,
    so a correction made while it ran leaves it stale (K1 re-check B1), never stamped as the cells are now, and a
    job asked earlier never replaces a group asked later at the same revision (the second re-check, SF1)."""
    return population.revision, [m.id for m in population.live()], time.time()


def add_group(hub, population, gid: str, *, execution: str | None, implementation: str, version: str, basis: str,
              columns: dict, values: dict, made_from: tuple[int, list[str]] | None = None,
              vectors: tuple[list[str], np.ndarray] | None = None, extra: dict | None = None) -> dict:
    """Write one column group (a new execution's output replaces the same group's older one) and save it,
    stamped with the revision and the members it was made from (`made_from`, from `asked` when the job was
    asked; now, when it is made at once). A group made from an older revision than the one there never
    replaces it. A vector group's numbers are reserved and kept by the evidence store (Refused when there is
    no room). A layer coloured by the group it replaces is told at once (Layer.forget_stale_style)."""
    from .evidence import evidence_of

    revision, members, at = (*made_from, None)[:3] if made_from is not None else asked(population)
    at = time.time() if at is None else at
    there = population.columns.get(gid)
    if there is not None:
        there_revision = int(there.get("revision") or 0)
        if there_revision > revision or (there_revision == revision and float(there.get("askedAt") or 0.0) > at):
            # A newer one is there: made after a correction this one did not see, or asked after this one at the
            # same revision (it landed first): it stays, and this one's request says it was overtaken (`landed`).
            return there
    store = evidence_of(hub)
    if vectors is not None:
        store.keep_vectors(population, gid, *vectors)
    group = {"execution": execution, "implementation": implementation, "version": version, "basis": basis,
             "revision": revision, "members": list(members), "columns": columns,
             "values": values, "askedAt": round(at, 6), "madeAt": round(time.time(), 6), **(extra or {})}
    population.columns[gid] = group
    store.schedule_save(population)
    _layer_told(hub, population, gid)
    return group


def _layer_told(hub, population, gid: str) -> None:
    """The cells' layer, shown and coloured by the group just replaced, is sent its style again (each cell's own
    colour: the replaced group's legend is not this one's; K1 re-check SF2)."""
    from .population import layer_of

    layer = layer_of(hub)
    if layer.shown == population.id and layer.style.get("columnGroup") == gid:
        layer.broadcast(layer.wire())


def landed(population, gid: str, execution_id: str | None, revision: int, what: str, overtaken: str) -> None:
    """What a job made is what is there now, or its request is refused in words: corrected meanwhile (`still`),
    or overtaken by a later request at the same revision whose group landed first (the second re-check, SF1:
    the answer never claims an earlier request's result is the one there now)."""
    from .brain.registry import ToolFailed

    still(population, revision, what)
    if (population.columns.get(gid) or {}).get("execution") != execution_id:
        raise ToolFailed(overtaken, next="say which you want, or ask about what is there now")


def still(population, revision: int, what: str) -> None:
    """Refused, in words, when the cells were corrected while `what` was being made (K1 re-check B1): what was
    made is kept, stamped with the revision it was made from, so it is stale and never used."""
    from .brain.registry import ToolFailed

    if population.revision != revision:
        raise ToolFailed(f"{population.title} was corrected while its {what} were being made (made from revision "
                         f"{revision}; the cells are at revision {population.revision} now), so they are not used",
                         next="group them again")


def current(population, gid: str | None) -> dict | None:
    """The group, while it is not stale (the one check: `Population.stale`)."""
    group = population.columns.get(gid) if gid else None
    if group is None or population.stale(group):
        return None
    return group


def stale_words(population, group: dict, what: str = "look groups") -> str:
    return (f"{population.title}'s {what} were made before your correction (at revision {group.get('revision') or 0}; "
            f"the cells are at revision {population.revision} now), so they are not used")


def grouping(hub, population) -> tuple[str, dict]:
    """The look groups every reader uses (review SF2: describing, naming, colouring, querying): the request's
    own choice of picture model (`use`), else option EMBED's, else the newest current grouping by when it was
    made. Each grouping is one picture model's, and is said with its model. ToolFailed, with the reason and
    the next step, when there is none to use (none yet, made before a correction, or not with the model the
    request chose: an explicit choice is never substituted, ADR 0011)."""
    from . import ticket as tickets
    from .brain.registry import ToolFailed

    chosen = tickets.chosen("looks")
    if chosen and chosen.get("refused"):
        raise ToolFailed(f"{chosen.get('label')} was asked for and refused ({chosen['refused']}), so no other picture "
                         "model's groups are used in its place", next="name another one, or ask without naming one")
    if chosen and chosen.get("implementation"):
        from .operations import OPERATIONS

        impl = OPERATIONS["looks"].impl(chosen["implementation"])  # as it is now (a contributed one too)
        model = (impl.option or impl.id) if impl is not None else chosen["implementation"]
        group = population.columns.get(groups_id(model))
        if group is None or population.stale(group):
            why = stale_words(population, group, f"{model_label(model)} look groups") if group is not None else \
                f"{population.title} is not grouped by look with {model_label(model)}, the picture model this request chose"
            raise ToolFailed(why, next=f"group them by look with {model_label(model)} first")
        chosen["used"] = True
        return groups_id(model), group
    setting = getattr(getattr(hub, "settings", None), "embed", None)
    group = population.columns.get(groups_id(setting)) if setting else None
    if group is not None and not population.stale(group):
        return groups_id(setting), group
    made = [(g.get("madeAt") or 0.0, gid, g) for gid, g in population.columns.items()
            if gid.startswith("groups:") and not population.stale(g)]
    if made:
        _, gid, group = max(made, key=lambda t: t[0])
        return gid, group
    old = [g for gid, g in population.columns.items() if gid.startswith("groups:")]
    if old:
        raise ToolFailed(stale_words(population, max(old, key=lambda g: g.get("madeAt") or 0.0)), next="group them again")
    raise ToolFailed(f"{population.title} is not grouped by look yet", next="ask to group them by look first")


LOOK_GROUP_WORDS = {"group", "groups", "lookgroup", "lookgroups", "look", "looks", "bylook", "bylookgroup"}
YOUR_LABEL_WORDS = {"yourlabel": "yourLabel", "yourlabels": "yourLabel", "nearestgroupyounamed": "yourLabel",
                    "nearestnamedgroup": "yourLabel", "namedgroup": "yourLabel", "similarity": "similarity"}


def resolve_column(hub, population, name: str) -> str | None:
    """A column a reader names, as the population has it (review SF3): its case kept ("meanred" is "meanRed");
    a member's look group and its your-label column read from the one grouping (`grouping`, "group@bioclip-2").
    None when the population has no such column; ToolFailed when it has it only from before a correction."""
    from .brain.registry import ToolFailed

    key = re.sub(r"[\s_-]+", "", str(name).lower())
    if key in LOOK_GROUP_WORDS:
        _gid, group = grouping(hub, population)
        return f"group@{group.get('model') or _gid.split(':', 1)[1]}"
    if key in YOUR_LABEL_WORDS:
        gid, group = grouping(hub, population)
        model = group.get("model") or gid.split(":", 1)[1]
        named = population.columns.get(named_id(model))
        if named is None:
            raise ToolFailed(f"no look group of {population.title} ({model_label(model)}) has been named yet",
                             next='name one: "group 2 are platelets"')
        if population.stale(named):
            raise ToolFailed(stale_words(population, named, "nearest named groups"),
                             next="name a group again (your earlier names are carried over)")
        return f"{YOUR_LABEL_WORDS[key]}@{model}"
    exact = population.known_columns().get(str(name).strip().lower())
    if exact is None:
        return None
    _gid, group = population.column_group(exact)
    if group is not None and population.stale(group):
        raise ToolFailed(stale_words(population, group, f"{exact} values"), next="group them again")
    return exact


def kinds(population) -> list[str]:
    """In a few words, which groups a population has beyond its measurements (for its evidence line): only
    current ones, each with its picture model (review B3, SF2)."""
    out = []
    for gid, group in population.columns.items():
        if gid == "measure" or current(population, gid) is None:
            continue
        model = model_label(gid.split(":")[-1])
        if gid.startswith("looks:"):
            out.append(f"looks ({model})")
        elif gid.startswith("groups:"):
            out.append(f"{group.get('k', '?')} look groups ({model})")
        elif gid == "light":
            out.append("light")
        elif gid.startswith("names:"):
            made_on = population.columns.get(gid.split(":", 1)[1]) or {}
            if made_on.get("execution") == group.get("grouping"):  # names about the groups as they are now
                out.append(f"MedSigLIP names ({model} groups)")
        elif gid.startswith("named:"):
            out.append(f"named groups ({model})")
    return out


# --- what each enrichment computes (pure, testable without a model) ---------------------------------------
def light_values(image_bgr: np.ndarray, members: list, masks: dict | None = None) -> dict:
    """Mean and spread (standard deviation) of blue, green and red inside each member's own mask, 0-255
    (`masks`: member id -> Mask, read back when the population's pixels were released)."""
    from .evidence import _full_res

    h, w = image_bgr.shape[:2]
    out = {}
    for m in members:
        mask = (masks or {}).get(m.id, m.mask)
        if mask is None:
            continue
        bits = _full_res(mask)
        y0, x0 = int(mask.y0), int(mask.x0)
        ys = slice(max(0, y0), min(h, y0 + bits.shape[0]))
        xs = slice(max(0, x0), min(w, x0 + bits.shape[1]))
        part = bits[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
        pixels = image_bgr[ys, xs][part]
        if not len(pixels):
            continue
        mean, spread = pixels.mean(0), pixels.std(0)
        out[m.id] = {"meanBlue": round(float(mean[0]), 2), "meanGreen": round(float(mean[1]), 2),
                     "meanRed": round(float(mean[2]), 2), "spreadBlue": round(float(spread[0]), 2),
                     "spreadGreen": round(float(spread[1]), 2), "spreadRed": round(float(spread[2]), 2)}
    return out


def _sqdist(x: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """n x k squared distances, one centre at a time (n x dim at most in memory, not n x k x dim)."""
    out = np.empty((len(x), len(centres)))
    for j, c in enumerate(centres):
        out[:, j] = ((x - c) ** 2).sum(-1)
    return out


def _kmeans(x: np.ndarray, k: int, rng: np.random.Generator, rounds: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """k-means (k-means++ start) on unit vectors; (labels, centres)."""
    n = len(x)
    centres = [x[rng.integers(n)]]
    for _ in range(1, k):
        d = _sqdist(x, np.array(centres)).min(1)
        total = d.sum()
        centres.append(x[rng.choice(n, p=d / total)] if total > 0 else x[rng.integers(n)])
    centres = np.array(centres)
    labels = np.zeros(n, int)
    for _ in range(rounds):
        labels_new = _sqdist(x, centres).argmin(1)
        if (labels_new == labels).all() and _ > 0:
            break
        labels = labels_new
        for j in range(k):
            if (labels == j).any():
                centres[j] = x[labels == j].mean(0)
    return labels, centres


def cosine_distances(x: np.ndarray) -> np.ndarray:
    """1 - cosine for unit vectors, n x n, made in place (one n x n array)."""
    d = x @ x.T
    np.clip(d, -1.0, 1.0, out=d)
    np.subtract(1.0, d, out=d)
    return d


def silhouette(x: np.ndarray, labels: np.ndarray, d: np.ndarray | None = None) -> float:
    """The mean silhouette (cosine distance), -1 to 1: how much nearer each member is to its own group
    than to the next one. The stated criterion for the number of groups."""
    d = cosine_distances(x) if d is None else d
    groups = np.unique(labels)
    if len(groups) < 2:
        return -1.0
    scores = []
    for i in range(len(x)):
        own = labels == labels[i]
        if own.sum() <= 1:
            scores.append(0.0)
            continue
        a = d[i, own].sum() / (own.sum() - 1)
        b = min(d[i, labels == g].mean() for g in groups if g != labels[i])
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(scores))


def cluster_bytes(n: int, dim: int) -> int:
    """The room grouping n vectors of `dim` takes at most (reserved before, invariant 3): float64 copies of the
    vectors, the n x n cosine distances, and the k-means distances."""
    return 3 * n * dim * 8 + n * n * 8 + 4 * n * K_MAX * 8


def cluster(ids: list[str], vectors: np.ndarray, k_min: int = K_MIN, k_max: int = K_MAX) -> dict:
    """Groups by look: every k from k_min to k_max (no more than half the members) tried, the one
    with the best silhouette kept. Groups are numbered by size, largest first ("g1"); each gets its
    representatives (the members nearest its centre). Deterministic."""
    x = np.asarray(vectors, np.float64)
    x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    n = len(x)
    top = min(k_max, n // 2)  # at least two members a group
    if top < k_min:
        raise ValueError(f"{n} members are too few to group (at least {2 * k_min})")
    d = cosine_distances(x)
    tried = {}
    for k in range(k_min, top + 1):
        labels, centres = _kmeans(x, k, np.random.default_rng(SEED))
        tried[k] = (silhouette(x, labels, d), labels, centres)
    k = max(tried, key=lambda kk: (round(tried[kk][0], 6), -kk))  # ties: fewer groups
    score, labels, centres = tried[k]
    order = sorted(range(k), key=lambda j: (-(labels == j).sum(), j))
    name = {old: f"g{new}" for new, old in enumerate(order, start=1)}
    reps = {}
    for j in order:
        idx = np.where(labels == j)[0]
        near = idx[np.argsort(((x[idx] - centres[j]) ** 2).sum(1))][:REPRESENTATIVES]
        reps[name[j]] = [ids[i] for i in near]
    return {"k": k, "silhouette": round(score, 3), "tried": {kk: round(v[0], 3) for kk, v in tried.items()},
            "groups": {ids[i]: name[labels[i]] for i in range(n)}, "representatives": reps,
            "sizes": {name[j]: int((labels == j).sum()) for j in order}}


def floor_of(model: str) -> float | None:
    """From what similarity a member is given a named group's label: the picture model's measured floor for
    "the most alike is the same kind", borrowed from the seen-before bench (seen.THRESHOLDS); None for a model
    never measured there (no floor is invented, review B1)."""
    from .seen import THRESHOLDS

    return THRESHOLDS.get(model)


def floor_words(model: str) -> str:
    from .seen import CALIBRATIONS

    floor = floor_of(model)
    if floor is None:
        return (f"how alike is alike enough is unmeasured with {model_label(model)}, so no cell is given the label "
                "(the similarity column is there)")
    return (f"a floor borrowed from the seen-before bench (docs/bench/seen.md: \"{CALIBRATIONS.get(model, '')}\"), "
            "not measured for groups you named")


def nearest_named(ids: list[str], vectors: np.ndarray, named: list[dict], floor: float | None) -> dict:
    """Each member's nearest named group (the cosine to its nearest example, 1-NN as the seen-before bench
    measured it): {"similarity"}, and {"yourLabel": name} from the floor only (none without a floor). `named`
    is oldest first: an equal similarity goes to the newer name (K1 re-check SF3), never by the name's spelling."""
    x = np.asarray(vectors, np.float64)
    x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    index = {mid: i for i, mid in enumerate(ids)}
    rows: dict[str, list[int]] = {}
    newest: dict[str, int] = {}
    for order, entry in enumerate(named):
        rows.setdefault(entry["name"], []).extend(index[m] for m in entry["members"] if m in index)
        newest[entry["name"]] = order
    sims = {name: (x @ x[r].T).max(1) for name, r in rows.items() if r}
    out = {}
    if not sims:
        return out
    for i, mid in enumerate(ids):
        best = max(sims, key=lambda nm: (round(float(sims[nm][i]), 9), newest[nm]))
        s = float(sims[best][i])
        out[mid] = {"similarity": round(s, 3), **({"yourLabel": best} if floor is not None and s >= floor else {})}
    return out


# --- crops of members -------------------------------------------------------------------------------------
def member_crops(hub, population, members: list) -> dict[str, bytes]:
    """Each member's crop (as taught.py and seen.py crop an outline), from the view it was made on;
    decoded inside the memory bound (invariant 3)."""
    from .capture import CaptureGone, Refused, decoding
    from .population import capture_of
    from .taught import crop_for, encode

    capture = capture_of(hub, population)
    if capture is None:
        return {}
    try:
        with decoding(hub, capture, "look", owner="a population's crops") as held:
            image = held.bgr()
            if image is None:
                return {}
            out = {}
            for m in members:
                crop = crop_for(image, tuple(m.box))
                if crop is not None:
                    out[m.id] = encode(crop)
            return out
    except (CaptureGone, Refused):
        return {}


# --- running them through the runner ----------------------------------------------------------------------
def _prompt(population):
    from .operations import Prompt

    return Prompt("members", text=f"{population.id}@r{population.revision}")


class Kept:
    """A column group already made from these members at this revision: reused, as the runner says a reused job."""

    def __init__(self, execution_id: str | None) -> None:
        from types import SimpleNamespace

        self.execution = SimpleNamespace(id=execution_id)
        self.use = SimpleNamespace(reused=True, execution=self.execution)


def _kept(hub, population, operation: str, gid: str) -> Kept | None:
    group = current(population, gid)
    if group is None:
        return None
    from .operations import runner

    runner(hub).kept_use(operation, group.get("execution"))
    return Kept(group.get("execution"))


def embed_model(hub, population) -> str:
    """The looks model this request runs: its own choice (`use`), else option EMBED (the runner decides)."""
    from . import ticket as tickets
    from .operations import _request_choice, runner

    ticket = tickets.current()
    chosen = _request_choice(ticket, "looks")  # as run() reads it: only work done for the request sees its choice
    _op, impl, _policy, _selection = runner(hub).choose("looks", _prompt(population), None, None, "brain-default",
                                                        ticket if chosen else None)
    return impl.option or impl.id


async def run_looks(hub, population, *, origin: str = "brain-default") -> tuple[str, Any]:
    """The looks group for option EMBED's model (made, or reused when this revision already has it)."""
    from .operations import runner
    from .population import capture_of

    kept = _kept(hub, population, "looks", looks_id(embed_model(hub, population)))
    if kept is not None:
        return looks_id(embed_model(hub, population)), kept
    capture = capture_of(hub, population)
    made_from = asked(population)
    result = await runner(hub).run("looks", capture, _prompt(population), origin=origin,
                                   params={"population": population.id, "revision": made_from[0]})
    out = result.output
    gid = looks_id(out["model"])
    if current(population, gid) is None or population.columns[gid].get("execution") != result.execution.id:
        add_group(hub, population, gid, execution=result.execution.id, implementation=out["implementation"],
                  version=out.get("version", ""), basis="each member's crop from the view it was made on",
                  columns={"look": {"type": "vector", "model": out["model"], "dim": int(np.asarray(out["vectors"]).shape[-1])}},
                  values={}, made_from=made_from, vectors=(out["ids"], np.asarray(out["vectors"], np.float32)),
                  extra={"model": out["model"]})
    still(population, made_from[0], "looks")
    return gid, result


async def run_groups(hub, population, *, origin: str = "brain-default", k: int | None = None) -> tuple[str, Any]:
    """Looks first (made or reused), then the groups over them: k by silhouette, or the number asked for."""
    from .operations import runner
    from .population import capture_of

    looks, made = await run_looks(hub, population, origin=origin)
    model = looks.split(":", 1)[1]
    existing = current(population, groups_id(model))
    same_k = existing is not None and (k is None and existing.get("asked") is None or existing.get("asked") == k)
    kept = _kept(hub, population, "groups", groups_id(model)) if made.use.reused and same_k else None
    if kept is not None:
        return groups_id(model), kept
    made_from = asked(population)
    rests_on = (population.columns.get(looks) or {}).get("execution")
    result = await runner(hub).run("groups", capture_of(hub, population), _prompt(population), origin=origin,
                                   params={"population": population.id, "revision": made_from[0], "looks": looks,
                                           "k": k})
    out = result.output
    gid = groups_id(model)
    add_group(hub, population, gid, execution=result.execution.id, implementation="kmeans-silhouette", version="1",
              basis=f"k-means over {looks} (each model's vectors only with its own), " + (f"{k} groups as asked"
                                                                                         if k else "k by silhouette"),
              columns={"group": {"type": "category", "source": "algorithm"}},
              values={mid: {"group": g} for mid, g in out["groups"].items()}, made_from=made_from,
              extra={"k": out["k"], "asked": k, "silhouette": out["silhouette"], "tried": out["tried"], "looks": looks,
                     "model": model, "representatives": out["representatives"], "sizes": out["sizes"],
                     "restsOn": {"looks": rests_on}})
    there = population.columns.get(gid) or {}
    landed(population, gid, result.execution.id, made_from[0], "look groups",
           f"a later request regrouped {population.title} before this grouping finished, so its {out['k']} groups were "
           f"not kept; the groups there now are that request's ({there.get('k', '?')} groups by look "
           f"({model_label(there.get('model') or model)}))")
    return gid, result


async def run_light(hub, population, *, origin: str = "brain-default") -> tuple[str, Any]:
    from .operations import runner
    from .population import capture_of

    kept = _kept(hub, population, "light", "light")
    if kept is not None:
        return "light", kept

    made_from = asked(population)
    result = await runner(hub).run("light", capture_of(hub, population), _prompt(population), origin=origin,
                                   params={"population": population.id, "revision": made_from[0]})
    unit = {"type": "number", "unit": "gray level (0-255)", "basis": "inside the member's own mask; this capture only"}
    columns = {c: dict(unit) for c in ("meanBlue", "meanGreen", "meanRed", "spreadBlue", "spreadGreen", "spreadRed")}
    add_group(hub, population, "light", execution=result.execution.id, implementation="channel-stats", version="1",
              basis="each member's own mask on the view it was made on; comparable only within one capture",
              columns=columns, values=result.output["values"], made_from=made_from)
    landed(population, "light", result.execution.id, made_from[0], "colours",
           f"a later request measured {population.title}'s colours before these finished, so these were not kept")
    return "light", result


async def run_names(hub, population, group_gid: str, *, origin: str = "brain-default") -> tuple[str, Any]:
    """MedSigLIP's ranking of the subject's candidate names, per group (its representatives' crops)."""
    from .operations import runner
    from .population import capture_of
    from .zeroshot import candidates_for

    gid = f"names:{group_gid}"
    grouping_made = (population.columns.get(group_gid) or {}).get("execution")
    there = current(population, gid)
    subject = (getattr(getattr(hub, "scene", None), "context", None) or {}).get("specimen")
    candidates = candidates_for(subject, "subject")
    if there is not None and there.get("grouping") == grouping_made and candidates is not None \
            and there.get("specimen") == candidates.specimen:
        # The same groups, the same members and the same candidate names: kept evidence, reused and recorded as a
        # use of the execution that made it (finding K1-3; the re-check's note 4), never ranked again.
        kept = _kept(hub, population, "names", gid)
        if kept is not None:
            return gid, kept
    made_from = asked(population)
    result = await runner(hub).run("names", capture_of(hub, population), _prompt(population), origin=origin,
                                   params={"population": population.id, "revision": made_from[0], "groups": group_gid})
    out = result.output
    add_group(hub, population, gid, execution=result.execution.id, implementation="medsiglip-448", version="google/medsiglip-448",
              basis=f"MedSigLIP on each group's {REPRESENTATIVES} representatives, the {out['specimen']} names, averaged",
              columns={"names": {"type": "ranking", "calibration": out["calibration"], "calibrationBasis": NAMES_BASIS}},
              values={}, made_from=made_from,
              extra={"byGroup": out["byGroup"], "specimen": out["specimen"], "calibration": out["calibration"],
                     "grouping": grouping_made, "model": group_gid.split(":", 1)[1]})
    landed(population, gid, result.execution.id, made_from[0], "MedSigLIP names",
           f"a later request ranked MedSigLIP's names for {population.title}'s groups before these finished, so these "
           "were not kept")
    return gid, result


_NAMING: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()  # event loop -> {(population, model): Lock}


def _naming_lock(population, model: str) -> asyncio.Lock:
    """One naming of a population's groups at a time, per picture model (the second re-check, SF1): each reads
    the names given before it once the one before has written, so two namings at once never lose one."""
    locks = _NAMING.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault((population.id, model), asyncio.Lock())


async def run_your_label(hub, population, group_gid: str, group_key: str, name: str, *,
                         origin: str = "brain-default") -> tuple[str, Any, dict]:
    """The user named one of these look groups (finding K1-5): its representatives are kept as that name's
    examples with the population (`named:<model>`), apart from taught.py's, and every member gets its nearest
    named group by look, through the runner (an Execution of its own, resting on the looks and the groups).
    The user's earlier names are carried over, across corrections and regroupings, with only the example cells
    still there; a newer name takes the cells it names from an earlier one, which keeps the rest (and is
    replaced when none is left, or when it is the same group named again); one with none of its cells left is
    dropped; a replaced or dropped name is kept as such, to be said when it is asked for. What became of each
    is returned, to be said (K1 re-check SF3; the second re-check's notes). Namings of one population run one
    at a time, each reading the names given before it when it starts (`_naming_lock`)."""
    from .operations import runner
    from .population import capture_of

    groups = population.columns[group_gid]
    model = groups.get("model") or group_gid.split(":", 1)[1]
    async with _naming_lock(population, model):
        if population.stale(groups):  # corrected while it waited for the naming before it
            from .brain.registry import ToolFailed

            raise ToolFailed(stale_words(population, groups), next="group them again, then name the group")
        live = {m.id for m in population.live()}
        examples = [m for m in groups["representatives"][group_key] if m in live]
        before = population.columns.get(named_id(model)) or {}
        retired = dict(before.get("retired") or {})
        carried, said = [], {"kept": [], "moved": [], "replaced": [], "dropped": []}
        for entry in before.get("named", []):
            same_group = entry.get("group") == group_key and entry.get("grouping") == groups.get("execution")
            members = [m for m in entry.get("members", []) if m in live]
            left = [m for m in members if m not in examples]
            if same_group or (members and not left):
                if entry["name"] != name:
                    said["replaced"].append({"name": entry["name"], "how": "same group" if same_group else "all"})
                    retired[entry["name"]] = {"how": "replaced", "by": name}
            elif not members:
                said["dropped"].append(entry["name"])
                retired[entry["name"]] = {"how": "dropped"}
            else:
                carried.append({**entry, "members": left})
                if len(left) < len(members):  # the new name takes the cells they share; the earlier keeps the rest
                    said["moved"].append({"name": entry["name"], "left": len(left), "of": len(entry["members"]),
                                          "moved": len(members) - len(left)})
                else:
                    said["kept"].append({"name": entry["name"], "left": len(left), "of": len(entry["members"])})
        retired.pop(name, None)  # a name given again is a current name
        named = carried + [{"group": group_key, "grouping": groups.get("execution"), "name": name, "members": examples,
                            "namedAt": round(time.time(), 3)}]
        made_from = asked(population)
        result = await runner(hub).run("your-label", capture_of(hub, population), _prompt(population), origin=origin,
                                       params={"population": population.id, "revision": made_from[0], "model": model,
                                               "named": json.dumps(named, sort_keys=True)})
        floor = floor_of(model)
        gid = named_id(model)
        add_group(hub, population, gid, execution=result.execution.id, implementation="nearest-named", version="1",
                  basis=(f"each member's look ({model_label(model)}) against the examples of the groups you named (the "
                         f"{REPRESENTATIVES} nearest each group's centre, not cells you confirmed); its nearest named group, "
                         "never a label the user gave it, and never written into a label"),
                  columns={"yourLabel": {"type": "category", "source": "the nearest group you named, by look", "floor": floor,
                                         "floorIs": floor_words(model)},
                           "similarity": {"type": "number", "unit": "cosine, to the nearest named group's examples"}},
                  values=result.output["values"], made_from=made_from,
                  extra={"named": named, "model": model, "floor": floor, "retired": retired,
                         "replaced": [r["name"] for r in said["replaced"]], "dropped": said["dropped"],
                         "moved": said["moved"],
                         "restsOn": {"looks": (population.columns.get(looks_id(model)) or {}).get("execution"),
                                     "groups": groups.get("execution")}})
        landed(population, gid, result.execution.id, made_from[0], "nearest named groups",
               f"a later naming of {population.title}'s groups landed before this one, so \"{name}\" was not kept")
    return gid, result, said


# --- the adapters the runner calls ------------------------------------------------------------------------
def _population(run, params):
    """The population a job is for, at the revision it was asked at. No pixels are read here, on the loop:
    an adapter that needs masks reads them in its own thread, in reserved room (review SF6)."""
    from .brain.registry import ToolFailed
    from .evidence import evidence_of

    population = evidence_of(run.hub).get(params.get("population"))
    if population is None:
        raise ToolFailed("those cells are not kept any more", next="outline the cells again")
    if population.revision != params.get("revision"):
        raise ToolFailed(f"{population.title} was corrected meanwhile", next="ask again")
    return population


async def gpu_vectors(hub, jpegs: list[bytes], model: str) -> list[list[float]]:
    """The analysis worker's `embed` op (the one taught.py and seen.py use): one model's vectors, never mixed."""
    from .operations import started, worker_client

    client = await started(worker_client(hub, "analysis"))
    if client is None:
        raise RuntimeError("no GPU worker")
    result = await client.call("embed", timeout=60.0, jpegs=jpegs, model=model)
    if result.get("model", model) != model:
        raise RuntimeError(f"the worker embedded with {result.get('model')}, not {model}")
    return result["vectors"]


def register() -> None:
    from .brain.registry import ToolFailed
    from .operations import InputUnread, adapter

    @adapter("looks", "dinov2-small", "bioclip-2", "dinov3-small")
    async def _looks(run, capture, prompt, impl, params) -> dict:
        population = _population(run, params)
        members = population.live()[:MAX_MEMBERS]
        crops = await asyncio.to_thread(member_crops, run.hub, population, members)
        if not crops:
            raise InputUnread(f"{population.title}'s view can't be read now", next="ask again in a moment")
        ids = list(crops)
        vectors = []
        for i in range(0, len(ids), 64):  # batches the heavy worker takes in one call
            vectors += await gpu_vectors(run.hub, [crops[m] for m in ids[i:i + 64]], impl.option)
        return {"ids": ids, "vectors": vectors, "model": impl.option, "implementation": impl.id, "version": impl.version,
                "producer": {"implementation": impl.id, "model": impl.version}}

    @adapter("groups", "kmeans-silhouette")
    async def _groups(run, capture, prompt, impl, params) -> dict:
        from .capture import captures
        from .evidence import evidence_of

        population = _population(run, params)
        gid = params.get("looks")
        looks = current(population, gid)
        if looks is None:
            raise ToolFailed("these cells' looks are not measured yet", next="ask again")
        store = evidence_of(run.hub)
        dim = int(((looks.get("columns") or {}).get("look") or {}).get("dim") or 1024)
        need = store.vectors_bytes(population, gid) + cluster_bytes(len(population.members), dim)
        k = params.get("k")

        def work():  # the file read and the clustering, off the loop, in room reserved first (invariant 3)
            with captures(run.hub).memory.reserve("analysis", need, f"grouping {population.title} by look"):
                vectors = store.vectors_of(population, gid)
                if vectors is None:
                    return None
                if k:
                    return cluster(vectors["ids"], vectors["array"], int(k), int(k))
                return cluster(vectors["ids"], vectors["array"])

        try:
            out = await asyncio.to_thread(work)
        except ValueError as exc:
            raise ToolFailed(str(exc), next="outline more cells, or a view with more of them") from None
        if out is None:
            raise InputUnread(f"{population.title}'s looks were not kept", next="group them by look again")
        return out

    @adapter("light", "channel-stats")
    async def _light(run, capture, prompt, impl, params) -> dict:
        from .capture import CaptureGone, Refused, captures, decoding
        from .evidence import evidence_of
        from .population import capture_of

        population = _population(run, params)
        view = capture_of(run.hub, population)
        if view is None:
            raise InputUnread(f"{population.title}'s view can't be read now", next="ask again in a moment")
        store = evidence_of(run.hub)
        members = population.live()

        def work():
            # Masks released once saved are read back here, in this thread, in room reserved for them, and are
            # not kept: the population's pixels stay released (contract 1; review SF6).
            with captures(run.hub).memory.reserve("analysis", store.mask_bytes(members),
                                                  f"{population.title}'s masks, read back for their colours"):
                masks = store.masks_for(population, members)
                with decoding(run.hub, view, "look", owner="a population's colours") as held:
                    image = held.bgr()
                    return light_values(image, members, masks) if image is not None else None

        try:
            values = await asyncio.to_thread(work)
        except (CaptureGone, Refused) as exc:
            raise InputUnread(f"{population.title}'s view can't be read now ({exc})", next="ask again in a moment") from None
        if not values:
            raise InputUnread(f"{population.title}'s view or masks can't be read now", next="ask again in a moment")
        return {"values": values}

    @adapter("names", "medsiglip-448")
    async def _names(run, capture, prompt, impl, params) -> dict:
        from .zeroshot import candidates_for, gpu_rank

        population = _population(run, params)
        groups = current(population, params.get("groups"))
        if groups is None:
            raise ToolFailed("these cells are not grouped yet", next="group them by look first")
        subject = (getattr(run.hub.scene, "context", None) or {}).get("specimen")
        candidates = candidates_for(subject, "subject")
        if candidates is None:
            # Asking again cannot help: only the subject can (review note: the adapter's own next is kept).
            raise ToolFailed("MedSigLIP has candidate names only for blood smears and Pap smears, not "
                             f"{subject or 'an unnamed specimen'}", next="say what the specimen is, if it is one of those")
        reps = groups["representatives"]
        wanted = [m for ids in reps.values() for m in ids]
        crops = await asyncio.to_thread(member_crops, run.hub, population,
                                        [population.members[m] for m in wanted if m in population.members])
        by_group = {}
        for g, ids in reps.items():
            jpegs = [crops[m] for m in ids if m in crops]
            if not jpegs:
                continue
            ranked = await gpu_rank(run.hub, jpegs, list(candidates.names), candidates.template)
            shares = np.mean([[s for _, s in sorted(r, key=lambda t: candidates.names.index(t[0]))] for r in ranked], 0)
            order = np.argsort(-shares)
            by_group[g] = [{"name": candidates.names[i], "share": round(float(shares[i]), 3)} for i in order[:3]]
        if not by_group:
            raise InputUnread(f"{population.title}'s pictures can't be read now", next="ask again in a moment")
        return {"byGroup": by_group, "specimen": candidates.specimen, "calibration": candidates.calibration}

    @adapter("your-label", "nearest-named")
    async def _your_label(run, capture, prompt, impl, params) -> dict:
        from .capture import captures
        from .evidence import evidence_of

        population = _population(run, params)
        model = params["model"]
        gid = looks_id(model)
        if current(population, gid) is None:
            raise ToolFailed(f"{population.title}'s looks ({model_label(model)}) are not kept", next="group them by look again")
        named = json.loads(params["named"])
        store = evidence_of(run.hub)

        def work():  # the file read off the loop, in room reserved first (invariant 3)
            with captures(run.hub).memory.reserve("analysis", 2 * store.vectors_bytes(population, gid),
                                                  f"{population.title}'s looks, for your labels"):
                vectors = store.vectors_of(population, gid)
                if vectors is None:
                    return None
                return nearest_named(vectors["ids"], vectors["array"], named, floor_of(model))

        values = await asyncio.to_thread(work)
        if not values:  # no looks kept, or none of the named group's cells has one
            raise InputUnread(f"{population.title}'s looks ({model_label(model)}) were not kept", next="group them by look again")
        return {"values": values}
