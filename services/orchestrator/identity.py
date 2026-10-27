"""Identity (session knowledge K2, contract 2 of docs/design/2026-10-07-session-knowledge.md; ADR 0012
"Identity says how it knows"): how the app knows two things are the same thing, and what it knows of one
thing over time.

- **Two kinds of link, never mixed.** *Same object*: one physical thing in two captures, by continuity
  (one outline followed by the slide's movement and its own pixels), a carry (the outline brought back by
  its own pixels after a gap, a jump or a rejoin, with that match's score), promotion (a member outlined from
  its own mask), a linking model (Trackastra, with its score) or the user ("that's the same one"). *Same
  kind*: only alike (looks, a group, a taught label). Only same-object links join an individual; a box
  overlap (M2.1's first guess) is not one of ADR 0012's bases and joins nothing.
- **An individual** (`i4`): one physical thing across captures, its nodes joined by same-object links
  (a population's member, `e3/c12`, or an outline, `o4@<run>`). Numbered through the session and the date folder,
  never reused (day/ids.py). Its confidence is its weakest link, every link and carry of its chain said with its basis.
- **Runs.** Outline ids start again at o1 in every run of the app, and motion epochs at 0, so an outline node
  carries the run it was made in (`capture.RUN`) and so does every observation (with the bridge's boot and
  seq of its frame). Anything recorded in another run is history only: never measured, placed or matched as
  this run's outline with the same id, whatever its time (review B1 of K2; the M2.3 re-check's rule).
- **Its history**: its observations, each with the time, the view, where it was on the slide in that
  view's motion epoch, and its measurements with their basis (which model's outline or mask, the scale,
  the frame's geometry). Members removed or merged by a correction are no longer it (said, never deleted).
- **Place**: within an epoch, from the slide's offsets (`slidemap.place`), only for an outline whose own
  pixels last matched (`tracking`; one out of view keeps the slide's place, said so); one whose check failed
  says since when. Across epochs only after a verified rejoin (the hub carries the outline over). Otherwise
  "can't tell", with when continuity was lost (when the view left its epoch).
- **Over time**: measurements are compared only between observations sharing a basis (the same producer,
  objective, frame size, and eyepiece radius within 0.5%); otherwise "not comparable", with why. A new
  outline counts as the same thing only when it overlaps the followed one where its pixels matched.
- **A missing link means can't tell, never different.** "Those aren't the same" undoes a link as a new
  event that names it; nothing is deleted.
- **Kept** per date under `identity/<day>/` (an asset): `events.jsonl`, appended, each event with the session it was
  made in: individuals made and their nodes, links given here and their undoing, carries, observations, and
  continuity lost and found. A session's are read back from every date it touched (ADR 0017: an event without a
  session is its date's session's) when it is opened, and new ones go to today's file. Older nodes are history only,
  as across runs.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .capture import RUN
from .evidence import linked_outline

MEASURE_EVERY_S = 15.0  # a followed thing is measured again this often while it stays in view
FOLLOW_FOR_S = 30 * 60.0  # following ends after this long unless asked again (and with the run of the app)
MAX_FOLLOWED = 4  # as the follower (objtrack.MAX_FOLLOWED), which follows these first
SAME_RADIUS = 0.005  # eyepiece radius within 0.5%: the same geometry
COMPARED = ("areaPx", "feretMaxPx", "feretMinPx", "elongation", "circularity", "intensity")
GONE = ("deleted", "merged")  # a member a correction took away: no longer that thing (review SF3 of K2)
# Measuring a followed thing again (review SF1 of K2): the outline model is prompted with the outline's box
# grown by PAD of its size on each side (at least PAD_MIN_PX), so growth past the old box is not cut off; an
# outline that reaches its prompt box (within TOUCH_PX) may be cut off, or spilled into a neighbour, and is not
# measured. The new outline is the same thing only when it overlaps the followed outline, where its own pixels
# matched, by SAME_THING_IOU: a neighbour drawn instead, or the cell and the neighbour that crossed into its box
# drawn as one, is not. 0.5 lets the area double inside the box (more reaches the box's edge anyway).
PAD, PAD_MIN_PX, TOUCH_PX = 0.25, 6.0, 1.5
SAME_THING_IOU = 0.5
# What each basis of a same-object link or carry is, in words (ADR 0012's continuity, re-find, promotion,
# linking, user).
BASIS_WORDS = {
    "promotion": "outlined from its own mask",
    "outlined from this mask": "outlined from its own mask",
    "user": "you said they are the same",
    "linking": "linked by the linking model",
    "re-find": "re-found by its own pixels after the view jumped",
    "rejoin": "its place recognised when the slide came back, and its own pixels matched there",
    "gap": "re-found by its own pixels after they had not matched for a while",
    "follower": "picked up again by the object follower and its own pixels",
}


def member_node(population_id: str, member_id: str) -> str:
    return f"{population_id}/{member_id}"


def outline_node(object_id: str, run: str | None = None) -> str:
    """An outline as a node: its id in the run it was made in (this run unless said; ids start again at o1 every run)."""
    return f"{object_id}@{run or RUN}"


def parse_outline(node: str) -> tuple[str, str | None]:
    """(outline id, run) of an outline node; run None for a node kept before runs were recorded."""
    oid, _, run = str(node).partition("@")
    return oid, (run or None)


def is_member(node: str) -> bool:
    return "/" in str(node)


def hhmmss(at: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at or 0.0))


def _number(i: str) -> int:
    return int(i[1:]) if i[1:].isdigit() else 0


def made_on_earlier_run(obj) -> bool:
    """An outline made on a view read back from another run of the app (Captures.read_back: an id no live frame has,
    negative, and no epoch). Its place is that run's, so nothing places it on the live view, re-finds it or carries it
    (the K2 re-check, SF2: a template match near another run's offsets is as likely a lookalike neighbour)."""
    return getattr(obj, "anchor_epoch", 0) is None or int(getattr(obj, "origin", 0) or 0) < 0


EARLIER_VIEW = ("it was outlined on a view from before the app restarted, so where it is now is not known (its own "
                "pixels are never searched for where that run's offsets put them)")


class Identity:
    """The session's individuals, links and observations, rebuilt from its events (every date it touched) when it is
    opened. `store` (the session store) says which session that is and its dates; without one (a test's) a day is a
    session."""

    def __init__(self, root: Path | None, today: Callable[[], str], hub=None, log=None, store=None) -> None:
        self.root = Path(root) if root is not None else None
        self.today = today
        self.hub = hub
        self.log = log or (lambda record: None)
        self.store = store
        self.day = ""
        self.session = ""
        self._given = 0  # the highest individual number given or read back in this run (day/ids.py)
        self._task: asyncio.Task | None = None
        self._load()
        listeners = getattr(hub, "on_carry", None)
        if isinstance(listeners, list):  # outlines brought back after a gap or a jump (hub.carried; review B3)
            listeners.append(self._carried)
        opened = getattr(store, "listeners", None)
        if isinstance(opened, list):  # another session opened: its things followed are the ones loaded
            opened.append(lambda event: self._load() if event.get("type") == "session-opened" else None)

    # --- the session's files ---------------------------------------------------------------------
    def _sid(self) -> str:
        return getattr(self.store, "sid", None) or self.today()

    def _dates(self) -> list[str]:
        """The dates the session touched (its date folders), oldest first, today's with them."""
        if self.store is None:
            return [self.today()]
        return sorted(set(getattr(self.store, "days", []) or []) | {self.today()})

    def _path(self, day: str | None = None) -> Path | None:
        """A date's file (default: today's, where new events go)."""
        return self.root / (day or self.today()) / "events.jsonl" if self.root is not None else None

    def _load(self) -> None:
        from .day.sid import session_of

        self.day = self.today()
        self.session = self._sid()
        self.of: dict[str, str] = {}  # node -> individual id (nodes given to it)
        self.made: dict[str, dict] = {}  # individual id -> {"at", "why", "by", "run", "until", "followed_at", ...}
        self.links: list[dict] = []  # links given here (user, linking), never removed: an undo marks one "undone"
        self.joins: list[dict] = []  # nodes a link gave an individual ({"i", "node", "link"}), so an undo takes them back
        self.merges: list[dict] = []  # individuals a link made one ({"i", "from", "nodes", "link"})
        self.carries: dict[str, list[dict]] = {}  # node -> each time its outline was brought back, with basis and score
        self.observations: dict[str, list[dict]] = {}  # node -> its observations, oldest first
        self.lost: dict[str, float] = {}  # node -> when continuity was lost (cleared when it is found again)
        for date in self._dates():
            path = self._path(date)
            if path is None or not path.exists():
                continue
            for line in path.read_text().splitlines():
                try:
                    event = json.loads(line)
                    if session_of(event, date) != self.session:
                        continue  # another session's, filed on the same date
                    self._apply({**event, "day": event.get("day") or date})
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # a line cut off by a crash: the rest still load

    def _current(self) -> None:
        """Another session is open (one opened without telling: a test's): its events are the ones loaded. Midnight
        changes nothing but the date new events are written under."""
        if self._sid() != self.session:
            self._load()

    def _apply(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "individual":
            self.made[event["i"]] = {"at": event["at"], "why": event.get("why", ""), "by": event.get("by", "follow_cells"),
                                     "run": event.get("run"), "until": event.get("until"), "day": event.get("day")}
            self._given = max(self._given, _number(event["i"]))
            for node in event["nodes"]:
                self.of.setdefault(node, event["i"])
        elif kind == "joined":
            self.of[event["node"]] = event["i"]
            if event.get("link"):
                self.joins.append({"i": event["i"], "node": event["node"], "link": event["link"]})
        elif kind == "merged":  # one physical thing: a later individual became part of an earlier one
            for node in event["nodes"]:
                self.of[node] = event["i"]
            if event["from"] in self.made:
                self.made[event["from"]]["merged_into"] = event["i"]
            self.merges.append({k: event.get(k) for k in ("i", "from", "nodes", "link")})
        elif kind == "link":  # given here: always same-object (same-kind is never written as a link)
            self.links.append({"kind": "same-object", "id": event.get("id") or f"l{len(self.links) + 1}",
                               **{k: event[k] for k in ("from", "to", "basis", "confidence", "at")},
                               "run": event.get("run"), "by": event.get("by"), "undone": None})
        elif kind == "unlink":  # "those aren't the same": the link stays, marked undone, and what it joined parts
            link = next((k for k in self.links if k["id"] == event["link"]), None)
            if link is not None:
                link["undone"] = {"at": event["at"], "by": event.get("by", "user"), "why": event.get("why", "")}
            for j in self.joins:
                if j["link"] == event["link"] and self.of.get(j["node"]) == j["i"]:
                    self.of.pop(j["node"], None)
            for m in self.merges:
                if m["link"] == event["link"]:
                    for node in m["nodes"] or []:
                        if self.of.get(node) == m["i"]:
                            self.of[node] = m["from"]
                    if m["from"] in self.made:
                        self.made[m["from"]]["merged_into"] = None
        elif kind == "carried":
            self.carries.setdefault(event["node"], []).append(
                {k: event.get(k) for k in ("basis", "confidence", "at", "run")})
            self.lost.pop(event["node"], None)
        elif kind == "observed":
            self.observations.setdefault(event["node"], []).append(event["observation"])
        elif kind == "lost":
            self.lost[event["node"]] = event["at"]
        elif kind == "found":
            self.lost.pop(event["node"], None)
        elif kind == "follow":
            if event["i"] in self.made:
                self.made[event["i"]].update(until=event.get("until"), followed_at=event.get("at"),
                                             follow_run=event.get("run"))

    def _write(self, event: dict) -> None:
        event = {**event, "session": self.session}  # filed under today's date, with the session it was made in
        self._apply({**event, "day": self.today()})
        path = self._path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    # --- nodes ------------------------------------------------------------------------------------
    def outline(self, node: str):
        """This run's scene outline an outline node names, or None: never another run's with the same id."""
        if is_member(node) or self.hub is None:
            return None
        oid, run = parse_outline(node)
        return self.hub.scene.objects.get(oid) if run == RUN else None

    def member(self, node: str):
        """(population, member) of a member node, or (None, None)."""
        from .evidence import evidence_of

        if not is_member(node) or self.hub is None:
            return None, None
        pid, mid = node.split("/", 1)
        population = evidence_of(self.hub).get(pid)
        return population, (population.members.get(mid) if population is not None else None)

    def gone(self, node: str) -> str:
        """Why a member node is no longer the thing ("" when it is): a correction removed or merged it."""
        population, member = self.member(node)
        if member is None or member.status not in GONE:
            return ""
        mid = node.split("/", 1)[1]
        r = next((r["r"] for r in reversed(population.revisions) if mid in (r.get("members") or [])), population.revision)
        return (f"{mid} was {'removed' if member.status == 'deleted' else 'merged with another cell'} by your correction "
                f"(revision {r}), so it no longer counts as this thing")

    # --- links and individuals ---------------------------------------------------------------------
    def _end(self, population, link: dict, side: str) -> str | None:
        ref = str(link.get(side) or "")
        if ref.startswith("c") and ref[1:].isdigit():  # a population's links name its members by id (c12)
            return member_node(population.id, ref) if ref in population.members else None
        if linked_outline(self.hub, {**link, "to": ref}) is not None:  # this run's outline, no newer than the link
            return outline_node(ref)
        # Not the scene's outline: one of the run the link was made in (gone since, or another run's: history only);
        # a link kept before runs were recorded names none.
        return outline_node(ref, link["run"]) if link.get("run") else None

    def _edges(self) -> list[dict]:
        """Every same-object link the session knows: populations' (promotion, a mask outlined) and those given
        here (user, linking), not undone. Same-kind links and box overlaps are never edges here."""
        from .evidence import evidence_of

        edges = []
        store = evidence_of(self.hub) if self.hub is not None else None
        for population in (getattr(store, "items", {}) or {}).values():
            for link in population.links:
                if link.get("kind") != "same-object" or str(link.get("basis") or "").startswith("overlap"):
                    continue
                a, b = self._end(population, link, "from"), self._end(population, link, "to")
                if a is None or b is None:
                    continue
                edges.append({"from": a, "to": b, "basis": link.get("basis", ""), "confidence": float(link.get("confidence", 1.0))})
        edges += [dict(link) for link in self.links if not link.get("undone")]
        return edges

    def component(self, node: str, edges: list[dict] | None = None) -> tuple[set[str], list[dict]]:
        """The nodes joined to `node` by same-object links, and the links among them."""
        edges = self._edges() if edges is None else edges
        nodes, frontier, used = {node}, [node], []
        while frontier:
            here = frontier.pop()
            for e in edges:
                other = e["to"] if e["from"] == here else e["from"] if e["to"] == here else None
                if other is None or self.gone(other):
                    continue
                if e not in used:
                    used.append(e)
                if other not in nodes:
                    nodes.add(other)
                    frontier.append(other)
        return nodes, used

    def _live(self, i: str) -> str:
        """The individual `i` is part of now (itself unless "that's the same one" made it part of another)."""
        seen = set()
        while i in self.made and self.made[i].get("merged_into") and i not in seen:
            seen.add(i)
            i = self.made[i]["merged_into"]
        return i

    def given(self, i: str) -> list[str]:
        return [n for n, ii in self.of.items() if self._live(ii) == i]

    def make(self, nodes: list[str], why: str = "", by: str = "follow_cells", link: str | None = None) -> str:
        """An individual for these nodes (one physical thing): an existing one when any node has one; two
        existing ones become one (the later part of the earlier), so neither is listed twice."""
        self._current()
        known = sorted({self._live(self.of[n]) for n in nodes if n in self.of}, key=_number)
        if known:
            i = known[0]
            for other in known[1:]:
                self._write({"kind": "merged", "i": i, "from": other, "nodes": self.given(other), "link": link,
                             "at": time.time()})
            for n in nodes:
                if self._live(self.of.get(n, "")) != i:
                    self._write({"kind": "joined", "i": i, "node": n, "link": link, "at": time.time()})
            return i
        from .day.ids import next_number

        # New for the session (on any of its days) and for today's folder: two sessions on one date never share one.
        self._given = next_number(self.store, "i", max([self._given, *(_number(i) for i in self.made)]), self.today())
        i = f"i{self._given}"
        self._write({"kind": "individual", "i": i, "nodes": list(nodes), "why": why, "by": by, "run": RUN,
                     "at": time.time()})
        made = getattr(self.store, "made", None)
        if made is not None:  # a product no other event files: the session's list counts it (catalogue.product_of)
            try:
                made("individual", i, day=self.today())
            except Exception:
                pass  # it is kept in identity's own file whatever happens to its line in the journal
        return i

    def same_one(self, a: str, b: str, basis: str = "user", confidence: float = 1.0, by: str = "same_one") -> str:
        """A same-object link someone gave (the user: "that's the same one"; a linking model, with its score)."""
        self._current()
        lid = f"l{len(self.links) + 1}"  # links are never removed, so their numbers hold through the day
        self._write({"kind": "link", "id": lid, "from": a, "to": b, "basis": basis, "confidence": float(confidence),
                     "at": time.time(), "run": RUN, "by": by})
        return self.make([a, b], why=f"linked by {basis}", by=by, link=lid)

    def not_same(self, a: str, b: str, why: str = "") -> list[dict]:
        """"Those aren't the same": every link given here between a and b, undone as a new event that names it
        (the link and what it joined stay in the day's record). The links undone ([] when there were none)."""
        self._current()
        undone = [k for k in self.links if not k["undone"] and {k["from"], k["to"]} == {a, b}]
        for k in undone:
            self._write({"kind": "unlink", "link": k["id"], "by": "user", "why": why[:120], "at": time.time()})
        return undone

    def nodes_of(self, i: str) -> set[str]:
        """Every node of an individual: those it was given, and all joined to them by same-object links (a member
        a correction took away is no longer one)."""
        self._current()
        i = self._live(i)
        edges = self._edges()
        out: set[str] = set()
        for n in self.given(i):
            if not self.gone(n):
                out |= self.component(n, edges)[0]
        return out

    def bases(self, i: str) -> list[tuple[str, float]]:
        """The basis and confidence of every link and carry in its chain."""
        nodes = self.nodes_of(i)
        out = [(e["basis"], e["confidence"]) for e in self._edges() if e["from"] in nodes and e["to"] in nodes]
        out += [(c["basis"], float(c["confidence"])) for n in sorted(nodes) for c in self.carries.get(n, [])]
        return out

    def confidence(self, i: str) -> tuple[float, str]:
        """Its weakest link's confidence, and that link's basis ("" when it is one thing seen continuously)."""
        weakest, basis = 1.0, ""
        for b, c in self.bases(i):
            if c < weakest:
                weakest, basis = c, b
        return weakest, basis

    def individuals(self) -> list[str]:
        self._current()
        return sorted((i for i, m in self.made.items() if not m.get("merged_into")), key=_number)

    def find(self, ref: str | None) -> list[str]:
        """The individuals `ref` means: "i4", a node, an outline id of this run, or none for every one today."""
        self._current()
        key = (ref or "").strip()
        if not key:
            return self.individuals()
        if re.fullmatch(r"i\d+", key):
            return [self._live(key)] if key in self.made else []
        node = outline_node(key) if re.fullmatch(r"o\d+", key) else key
        if node in self.of:
            return [self._live(self.of[node])]
        for i in self.individuals():
            if node in self.nodes_of(i):
                return [i]
        return []

    # --- observations, carries and losses ---------------------------------------------------------------
    def observe(self, node: str, observation: dict) -> None:
        self._current()
        self._write({"kind": "observed", "node": node, "observation": observation})

    def lose(self, node: str, at: float | None = None) -> None:
        if node not in self.lost:
            self._write({"kind": "lost", "node": node, "at": at or time.time()})

    def found(self, node: str) -> None:
        if node in self.lost:
            self._write({"kind": "found", "node": node, "at": time.time()})

    def _watched(self, node: str) -> bool:
        return node in self.of or any(node in self.nodes_of(i) for i in self.individuals())

    def _carried(self, object_id: str, basis: str, confidence: float) -> None:
        """The hub brought an outline back after a gap or a jump (hub.carried): a same-object link of that outline
        across the gap, with its basis and the match's score, recorded when the outline is part of an individual; never
        for an outline made on an earlier run's view (that run's offsets placed the search: the K2 re-check, SF2)."""
        self._current()
        node = outline_node(object_id)
        obj = (getattr(getattr(self.hub, "scene", None), "objects", None) or {}).get(object_id)
        if obj is not None and made_on_earlier_run(obj):  # its offsets were another run's: no carry is the thing (SF2)
            self.log({"kind": "identity-carry", "outline": object_id, "basis": basis, "ignored": "made on an earlier run's view"})
            return
        if self._watched(node):
            self._write({"kind": "carried", "node": node, "basis": basis, "confidence": round(float(confidence), 3),
                         "at": time.time(), "run": RUN})

    def history(self, i: str) -> list[dict]:
        """Every observation of the individual, oldest first: its members' (from their populations) and
        its outlines' (measured again while followed), each with the run it was made in."""
        out = []
        for node in self.nodes_of(i):
            if is_member(node):
                population, member = self.member(node)
                if member is None or member.status in GONE:
                    continue
                mid = node.split("/", 1)[1]
                v = population.view
                x0, y0, x1, y1 = member.box
                measured = {c: population.value(mid, c) for c in COMPARED if population.value(mid, c) is not None}
                made_in = next((r["r"] for r in population.revisions if mid in (r.get("added") or [])), 0)
                out.append({"node": node, "at": v.get("at") or population.made_at, "epoch": v.get("epoch"),
                            "run": v.get("run"), "boot": v.get("boot"), "seq": v.get("seq"), "frame": population.capture_id,
                            "slide": [round((x0 + x1) / 2 - float(v.get("dx") or 0.0), 1),
                                      round((y0 + y1) / 2 - float(v.get("dy") or 0.0), 1)],
                            "producer": f"{population.implementation} mask", "measure": measured,
                            "basis": basis_of(population.scale_id), "revision": made_in})
            else:
                out += [dict(o, node=node) for o in self.observations.get(node, [])]
        return sorted(out, key=lambda o: o["at"] or 0.0)

    # --- following chosen things over time --------------------------------------------------------
    def follow(self, i: str, until: float | None = None) -> list[str]:
        """Follow `i` (for FOLLOW_FOR_S, in this run of the app). Returns those no longer followed because at most
        MAX_FOLLOWED are followed at once (the longest followed stop; said, never silently)."""
        self._write({"kind": "follow", "i": i, "until": until or time.time() + FOLLOW_FOR_S, "at": time.time(), "run": RUN})
        following = self._following()
        stopped = following[:-MAX_FOLLOWED] if len(following) > MAX_FOLLOWED else []
        for j in stopped:
            self._write({"kind": "follow", "i": j, "until": time.time(), "at": time.time(), "run": RUN,
                         "why": f"at most {MAX_FOLLOWED} at once"})
        self.start()
        return stopped

    def _following(self) -> list[str]:
        """Every individual followed now, longest followed first: asked in this run (after a restart its outline is
        another run's: history), not past its time."""
        now = time.time()
        live = [(m.get("followed_at") or m["at"], _number(i), i) for i, m in self.made.items()
                if (m.get("until") or 0) > now and m.get("follow_run") == RUN and not m.get("merged_into")]
        return [i for _, _, i in sorted(live)]

    def followed(self) -> list[str]:
        self._current()
        return self._following()[-MAX_FOLLOWED:]

    def followed_outlines(self) -> list[str]:
        """This run's outline ids of the things followed (the object follower follows these first). Cheap: it is
        asked every follower step."""
        ids = set(self.followed())
        out = []
        for node, i in self.of.items():
            if not is_member(node) and self._live(i) in ids:
                oid, run = parse_outline(node)
                if run == RUN:
                    out.append(oid)
        return out

    def start(self) -> None:
        if self._task is None or self._task.done():
            try:
                self._task = asyncio.get_running_loop().create_task(self._loop())
            except RuntimeError:
                self._task = None

    async def _loop(self) -> None:
        while self.followed():
            try:
                await self.measure_followed()
            except Exception as exc:  # one bad round never stops following
                self.log({"kind": "identity-measure", "error": f"{type(exc).__name__}: {exc}"[:200]})
            await asyncio.sleep(MEASURE_EVERY_S)

    def left_at(self, obj) -> float | None:
        """When continuity was lost for an outline left in another epoch: when the view left that epoch (the hub
        keeps it), else when its own pixels stopped matching; None when neither is known."""
        left = getattr(self.hub, "epoch_left", None) or {}
        found = left.get(obj.anchor_epoch) or obj.lost_since
        return float(found) if found else None

    async def measure_followed(self) -> list[dict]:
        """Each followed individual's outline of this run, measured again on the view now when its place is
        verified there; continuity lost (another epoch, not rejoined) is written down once with when the view left
        its epoch, never guessed past; found again, the loss is cleared."""
        hub = self.hub
        info = getattr(hub, "latest_info", None)
        made = []
        for i in self.followed():
            for node in sorted(self.nodes_of(i)):
                obj = self.outline(node)
                if obj is None or info is None or made_on_earlier_run(obj):  # never placed in this run: nothing to measure
                    continue
                if obj.anchor_epoch != info.epoch:
                    self.lose(node, self.left_at(obj))
                    continue
                if getattr(obj, "track", "tracking") != "tracking":
                    continue  # not verified in place now: not measured, and nothing said about where it is
                self.found(node)
                observation = await measure_outline(hub, obj, info)
                if observation is not None:
                    self.observe(node, observation)
                    made.append(observation)
        return made


def basis_of(scale: dict | None) -> dict:
    scale = scale or {}
    return {"objective": scale.get("objective"), "frame": scale.get("frame"), "discPx": scale.get("discPx"),
            "umPerPx": scale.get("umPerPx"), "scale": "calibrated" if scale.get("measured") else
            ("estimated" if scale.get("umPerPx") else None)}


def means_words(producer: str | None) -> str:
    """How an observation was measured, in the user's words (the producer stays in its record)."""
    producer = str(producer or "")
    if producer.endswith(" outline"):
        return "its outline drawn again"
    if producer.endswith(" mask"):
        return "the cell finder's mask"
    return "another way"


def comparable(a: dict, b: dict) -> str:
    """"" when two observations' measurements share a basis; otherwise why not, in words."""
    if a.get("producer") != b.get("producer"):
        return (f"they were measured by different means ({means_words(a.get('producer'))} and "
                f"{means_words(b.get('producer'))})")
    ba, bb = a.get("basis") or {}, b.get("basis") or {}
    if ba.get("objective") != bb.get("objective"):
        return f"the objective changed ({ba.get('objective') or 'unknown'} to {bb.get('objective') or 'unknown'})"
    if ba.get("frame") != bb.get("frame"):
        return "the camera's frame size changed"
    ra, rb = float(ba.get("discPx") or 0.0), float(bb.get("discPx") or 0.0)
    if ra and rb and abs(ra - rb) / max(ra, rb) > SAME_RADIUS:
        return f"the eyepiece circle's size changed by {abs(ra - rb) / max(ra, rb):.1%} (the phone refocused or moved)"
    return ""


def _raster(polygons: list, x0: int, y0: int, w: int, h: int) -> np.ndarray:
    import cv2

    bits = np.zeros((max(1, h), max(1, w)), np.uint8)
    for poly in polygons:
        cv2.fillPoly(bits, [np.round(np.array(poly, float) - [x0, y0]).astype(np.int32)], 1)
    return bits.astype(bool)


def same_thing(new: list, followed: list) -> tuple[float, bool]:
    """How much a new outline overlaps the followed one (IoU), and whether it is the same thing (at least
    SAME_THING_IOU)."""
    pts = np.array([p for poly in new + followed for p in poly], float)
    x0, y0 = int(np.floor(pts[:, 0].min())) - 1, int(np.floor(pts[:, 1].min())) - 1
    w, h = int(np.ceil(pts[:, 0].max())) - x0 + 2, int(np.ceil(pts[:, 1].max())) - y0 + 2
    a, b = _raster(new, x0, y0, w, h), _raster(followed, x0, y0, w, h)
    union = float((a | b).sum())
    iou = float((a & b).sum()) / union if union else 0.0
    return iou, iou >= SAME_THING_IOU


async def measure_outline(hub, obj, info) -> dict | None:
    """The thing measured on the view now: its outline drawn again by the outline model from its box grown on
    each side (a background run, recorded), checked to be the same thing as the followed outline where its own
    pixels matched, the polygon filled and measured by app code (measure.py) off the event loop."""
    from .capture import CaptureGone, Refused, captures, decoding, scale_at
    from .measure import Mask, measure
    from .operations import Prompt, runner

    capture = captures(hub).get(info.id)
    if capture is None:
        return None
    dx, dy = info.dx - obj.anchor_offset[0], info.dy - obj.anchor_offset[1]
    x0, y0, x1, y1 = obj.bbox
    pad_x, pad_y = max(PAD_MIN_PX, PAD * (x1 - x0)), max(PAD_MIN_PX, PAD * (y1 - y0))
    width, height = float(getattr(info, "width", 0) or 0), float(getattr(info, "height", 0) or 0)
    box = [x0 + dx - pad_x, y0 + dy - pad_y, x1 + dx + pad_x, y1 + dy + pad_y]
    if width and height:
        box = [max(0.0, box[0]), max(0.0, box[1]), min(width - 1, box[2]), min(height - 1, box[3])]
    followed = [[[x + dx, y + dy] for x, y in poly] for poly in obj.polygons]  # where its own pixels matched now
    try:
        done = await runner(hub).run("outline", capture, Prompt("box", box=tuple(box), text=obj.label or ""),
                                     origin="background", params={"priority": "background"})
    except Exception:
        return None
    polygons = done.output.get("polygons") or []
    if not polygons:
        return None
    fov = getattr(info, "fov", None)
    disc = (fov.cx, fov.cy, fov.radius) if fov is not None else None

    def work():  # the fill, the comparison, the decode and the measuring: off the loop (review SF8)
        pts = np.array([p for poly in polygons for p in poly], float)
        (lx, ly), (hx, hy) = pts.min(axis=0), pts.max(axis=0)
        bx0, by0, bx1, by1 = int(np.floor(lx)), int(np.floor(ly)), int(np.ceil(hx)), int(np.ceil(hy))
        # Reaching the prompt box (not the frame's own edge) may mean it was cut off there.
        touches = (lx - box[0] <= TOUCH_PX and box[0] > 0) or (ly - box[1] <= TOUCH_PX and box[1] > 0) \
            or (box[2] - hx <= TOUCH_PX and (not width or box[2] < width - 1)) \
            or (box[3] - hy <= TOUCH_PX and (not height or box[3] < height - 1))
        overlap, same = same_thing(polygons, followed)
        mask = Mask(_raster(polygons, bx0, by0, bx1 - bx0 + 1, by1 - by0 + 1), bx0, by0, 1.0)
        try:
            with decoding(hub, capture, "look", owner="a followed thing's measurement") as held:
                measured = measure(mask, held.gray(), disc)
        except (CaptureGone, Refused):
            return None
        return measured, overlap, same, touches

    worked = await asyncio.to_thread(work)
    if worked is None:
        return None
    measured, overlap, same, touches = worked
    executed = done.execution.executed or done.execution.requested
    observation = {"at": getattr(info, "wall_at", None) or time.time(), "frame": info.id, "epoch": info.epoch, "run": RUN,
                   "boot": getattr(info, "boot", None) or None, "seq": getattr(info, "seq", None),
                   "slide": [round((x0 + x1) / 2 - obj.anchor_offset[0], 1), round((y0 + y1) / 2 - obj.anchor_offset[1], 1)],
                   "producer": f"{executed} outline", "execution": done.execution.id, "reused": bool(done.use.reused),
                   "overlap": round(overlap, 3), "basis": basis_of(scale_at(hub, info)), "measure": {}}
    if not same:
        observation["notMeasured"] = (f"the outline drawn again overlaps the followed one by {overlap:.0%}, so it may be a "
                                      "neighbour, or include one: not counted as its measurement")
    elif touches:
        observation["notMeasured"] = "the outline drawn again reached the edge of the box it was drawn in, so it may be cut off"
    else:
        observation["measure"] = {c: measured.get(c) for c in COMPARED if measured.get(c) is not None}
    return observation


def where_words(hub, identity: Identity, i: str) -> str:
    """Where the individual is now, measured from the slide's movement where its own pixels last matched; or why
    that can't be told."""
    from .slidemap import IN_VIEW_RADIUS, Place, place, place_words

    info = getattr(hub, "latest_info", None)
    reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
    um = reading.um_per_px if reading is not None else None
    nodes = sorted(identity.nodes_of(i))
    # Every outline of it is looked at before answering (the K2 re-check, SF): one whose own pixels match now wins,
    # then one out of view, then one being re-found, then one from an earlier run; never the first one found.
    placed, outside, refinding, earlier = None, None, None, False
    for node in nodes:
        obj = identity.outline(node)
        if obj is None:
            continue
        if made_on_earlier_run(obj):  # never matched in this run: no "since", no place (the K2 re-check, SF2)
            earlier = True
            continue
        if obj.track in ("refinding", "lost"):  # its own check failed: never "in view now" (review B2)
            # Only in the view it was made in: when the view has left its epoch, where it is can't be told, and the
            # loss is said at when the view left (below; review SF4), not as its own pixels failing.
            if info is None or obj.anchor_epoch == getattr(info, "epoch", None):
                if refinding is None or (obj.lost_since or 0) > (refinding.lost_since or 0):
                    refinding = obj
            continue
        found = place(obj, info) if info is not None else None
        if found is None:
            continue
        if obj.track == "out_of_view":
            outside = outside or found
        else:
            placed = placed or found
    if placed is not None:
        return f"{place_words(placed, um)} (its outline: its own pixels matched there, carried by the slide's measured movement)"
    if outside is not None:
        return (f"{place_words(outside, um)}, from the slide's measured movement since its own pixels last matched "
                "(out of view, they can't be checked)")
    if refinding is not None:
        since = f"since {hhmmss(refinding.lost_since)}" if refinding.lost_since else "lately"
        return (f"I can't confirm it's still there: its own pixels have not matched {since}, so its outline is not "
                "drawn until they do")
    if earlier:
        return f"can't tell: {EARLIER_VIEW}"
    history = identity.history(i)
    last = history[-1] if history else None
    if last is None:
        return "can't tell: it has not been seen yet"
    if last.get("run") != RUN:  # another run's offsets and epochs mean nothing now (review B1)
        return (f"can't tell: it was last seen at {hhmmss(last['at'])} in an earlier run of the app, so the slide's "
                "movement since is not known")
    if info is not None and getattr(info, "tracked", None) is False and last.get("epoch") == info.epoch:
        return (f"can't tell: the current frame's slide movement is not registered; "
                f"it was last seen at {hhmmss(last['at'])}")
    if info is not None and last.get("epoch") == getattr(info, "epoch", None) and getattr(info, "fov", None) is not None:
        fov = info.fov
        sx, sy = last["slide"]
        ddx, ddy = sx + info.dx - fov.cx, sy + info.dy - fov.cy
        distance = math.hypot(ddx, ddy)
        return (place_words(Place(ddx, ddy, distance / (2 * fov.radius), distance <= fov.radius * IN_VIEW_RADIUS), um)
                + f" (where it was at {hhmmss(last['at'])}, by the slide's movement since; not checked by its own pixels)")
    # When the view left its epoch: written down by the measuring round, or the hub's own record now (review SF4).
    lost_at = [identity.lost[n] for n in nodes if n in identity.lost]
    for node in nodes:
        obj = identity.outline(node)
        if obj is not None and info is not None and obj.anchor_epoch != info.epoch and identity.left_at(obj):
            lost_at.append(identity.left_at(obj))
    lost = max(lost_at, default=None)
    if lost is None:
        # A re-find can replace an outline's anchor and clear its recorded loss
        # before the next measurement. The retained observation still names the
        # epoch whose departure the hub measured; last-seen time is not a loss clock.
        lost = (getattr(hub, "epoch_left", None) or {}).get(last.get("epoch"))
    loss = f"at {hhmmss(lost)}" if lost is not None else "but the loss time is unavailable"
    return (f"can't tell: continuity was lost {loss} (the slide's movement since is not known); "
            f"it was last seen at {hhmmss(last['at'])}")


def change_words(identity: Identity, i: str) -> str:
    """Whether it changed: the first and last observations that share a basis, compared; or why not."""
    from .population import PLAIN_COLUMNS, UNITS

    history = [o for o in identity.history(i) if o.get("measure")]
    if len(history) < 2:
        return "not measured twice yet, so nothing can be said of a change"
    last = history[-1]
    first = next((o for o in history[:-1] if not comparable(o, last)), None)
    if first is None:
        return f"not comparable: {comparable(history[0], last)}"
    parts = []
    for column in ("areaPx", "elongation", "circularity"):
        a, b = first["measure"].get(column), last["measure"].get(column)
        if a is None or b is None:
            continue
        unit = UNITS.get(column, "")
        name = PLAIN_COLUMNS.get(column, column)
        change = (b - a) / a if a else 0.0
        parts.append(f"{name} {a:.3g} to {b:.3g}{(' ' + unit) if unit else ''} ({change:+.0%})")
    um = (last.get("basis") or {}).get("umPerPx")
    area = ""
    if um and first["measure"].get("areaPx") and last["measure"].get("areaPx"):
        area = (f"; {first['measure']['areaPx'] * um * um:,.0f} to {last['measure']['areaPx'] * um * um:,.0f} µm² "
                f"({(last.get('basis') or {}).get('scale')} scale)")
    return (f"from {hhmmss(first['at'])} to {hhmmss(last['at'])}, both measured the same way "
            f"({means_words(last['producer'])}, same objective and frame): " + ", ".join(parts) + area
            + "; a measured outline varies a few per cent between views, so small changes may be that")


def _cell_order(cell: str) -> tuple:
    """Cells in the order of their numbers (c2 before c10)."""
    return (int(cell[1:]) if cell[1:].isdigit() else math.inf, cell)


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def thing_words(identity: Identity, i: str) -> str:
    """The individual in the user's words: its cells by their numbers (as the studio names them), its outlines
    by their names; never its producer, and its id only beside it."""
    cells, outlines, earlier = [], [], 0
    for node in sorted(identity.nodes_of(i)):
        if is_member(node):
            cells.append(node.split("/", 1)[1])
        else:
            obj = identity.outline(node)
            if obj is not None:
                outlines.append(obj.label)
            else:
                earlier += 1
    named = [f"the {label}" for label in outlines if label]
    if cells:
        words = _and(cells) + (f", outlined as {_and(named)}" if named else ", outlined" if outlines else "")
    elif outlines:
        words = _and(named) if named else "an outline"
    else:
        words = "an outline" if earlier else "nothing left of it"
    if earlier and not outlines:
        words += " (its outline was in an earlier run of the app)"
    return words


def how_words(identity: Identity, i: str) -> str:
    """How it is known to be one thing: every basis in its chain, in words, and its weakest link."""
    bases = identity.bases(i)
    if not bases:
        return "one thing, seen continuously (its outline followed)"
    seen: dict[str, float] = {}
    for basis, c in bases:
        words = BASIS_WORDS.get(basis, basis)
        seen[words] = min(c, seen.get(words, 1.0))
    said = "; ".join(f"{w} ({c:.2f})" if c < 1.0 else w for w, c in seen.items())
    weakest, _ = identity.confidence(i)
    return f"one thing: {said}; otherwise seen continuously" + (f"; as sure as its weakest link, {weakest:.2f}" if weakest < 1.0 else "")


def compare_populations(hub, a, b) -> list[str]:
    """Two captures' cells compared (contract 2, "comparing captures"): as populations, which needs no identity
    (counts, medians of what was measured, coverage), only on a shared basis; cell by cell only through
    same-object links between them (cells counted, each once), and said when there are none."""
    from .population import PLAIN_COLUMNS, UNITS, value

    when = lambda p: hhmmss(p.view.get("at") or p.made_at)  # noqa: E731
    lines = [f"{a.id} at {when(a)}: {a.count_words()}", f"{b.id} at {when(b)}: {b.count_words()}"]
    why = comparable({"producer": a.implementation, "basis": basis_of(a.scale_id)},
                     {"producer": b.implementation, "basis": basis_of(b.scale_id)})
    if why:
        lines.append(f"their measurements are not compared: {why}; the counts are instances each model segmented, "
                     "not a biological count")
    else:
        for column in ("areaPx", "elongation", "circularity"):
            va = [v for v in (value(a, m, column) for m in a.live()) if isinstance(v, (int, float))]
            vb = [v for v in (value(b, m, column) for m in b.live()) if isinstance(v, (int, float))]
            if va and vb:
                unit = UNITS.get(column, "")
                lines.append(f"median {PLAIN_COLUMNS.get(column, column)}: {np.median(va):.3g} then {np.median(vb):.3g}"
                             f"{(' ' + unit) if unit else ''}")
        ca, cb = (a.columns.get("measure") or {}).get("coverage"), (b.columns.get("measure") or {}).get("coverage")
        if ca is not None and cb is not None:
            lines.append(f"coverage of the eyepiece: {ca:.1%} then {cb:.1%}")
    pairs, bases = linked_pairs(identity_of(hub), a, b)
    partners_a, partners_b = {}, {}
    for x, y in pairs:
        partners_a.setdefault(x, set()).add(y)
        partners_b.setdefault(y, set()).add(x)
    one_to_one = sorted(x for x, ys in partners_a.items() if len(ys) == 1 and len(partners_b[next(iter(ys))]) == 1)
    several = sorted({x for x, ys in partners_a.items() if len(ys) > 1} | {y for y, xs in partners_b.items() if len(xs) > 1})
    if one_to_one:
        n = len(one_to_one)
        lines.append(f"{n} {'cell is' if n == 1 else 'cells are'} linked as the same cell in both ({', '.join(sorted(bases))}), "
                     f"so {'it' if n == 1 else 'those'} can be compared one by one")
    else:
        lines.append("no cell is linked as the same in both, so they can't be compared cell by cell (following them, "
                     "or a linking model, makes such links)")
    if several:
        lines.append(f"{_and(several)} {'is' if len(several) == 1 else 'are'} linked to two or more cells (a division, or an "
                     "ambiguity), so not compared one by one")
    return lines


def linked_pairs(identity: Identity, a, b) -> tuple[set[tuple[str, str]], set[str]]:
    """The same-object links between two populations' live cells, each pair once as (a's cell, b's cell), and
    their bases."""
    pairs, bases = set(), set()
    for e in identity._edges():
        ends = {e["from"].split("/")[0]: e["from"], e["to"].split("/")[0]: e["to"]}
        if a.id in ends and b.id in ends and a.id != b.id:
            pairs.add((ends[a.id].split("/", 1)[1], ends[b.id].split("/", 1)[1]))
            bases.add(e["basis"])
    return pairs, bases


def linkable(hub, a, b) -> str:
    """"" when the linking model can be asked about two populations here; otherwise why it can't tell (review
    B4 of K2): both made in this run of the app, in one motion epoch (a verified rejoin counts), with the same
    objective and geometry, the slide's motion registered on both, and their eyepiece fields overlapping. The
    linking model is given only the part of the slide both views show, aligned by their offsets
    (operations._trackastra), so the slide may move between them."""
    from .population import _epoch_now, _ViewOf

    for p in (a, b):
        if p.view.get("run") != RUN:
            return (f"{p.id} was made in an earlier run of the app, so how the slide moved between the two views is not "
                    "known")
    links = getattr(hub, "epoch_links", None)
    now = _epoch_now(hub)
    va, vb = _ViewOf(a, links, now), _ViewOf(b, links, now)
    if va.epoch is None or va.epoch != vb.epoch:
        return "the app lost track of the slide's movement between the two views, so where each cell went is not known"
    if a.view.get("tracked") is False or b.view.get("tracked") is False:
        return "the slide's motion was not registered on one of the views"
    why = comparable({"producer": "", "basis": basis_of(a.scale_id)}, {"producer": "", "basis": basis_of(b.scale_id)})
    if why:
        return why
    if (va.width, va.height) != (vb.width, vb.height):
        return "the camera's frame size changed"
    da, db = a.view.get("disc"), b.view.get("disc")
    if da and db:
        ax, ay = da[0] - va.dx, da[1] - va.dy  # each eyepiece circle's centre on the slide
        bx, by = db[0] - vb.dx, db[1] - vb.dy
        if math.hypot(ax - bx, ay - by) >= da[2] + db[2]:
            return "the two views show different parts of the slide (their fields do not overlap)"
    return ""


def _inside_both(hub, population, member, a, b) -> bool:
    """A member's centre lies inside both views' eyepiece circles (the part of the slide both views show)."""
    from .population import _epoch_now, _ViewOf

    links = getattr(hub, "epoch_links", None)
    x0, y0, x1, y1 = member.box
    now = _epoch_now(hub)
    own = _ViewOf(population, links, now)
    sx, sy = (x0 + x1) / 2 - own.dx, (y0 + y1) / 2 - own.dy
    for p in (a, b):
        disc, v = p.view.get("disc"), _ViewOf(p, links, now)
        if disc and math.hypot(sx + v.dx - disc[0], sy + v.dy - disc[1]) > disc[2]:
            return False
    return True


async def link_populations(hub, a, b) -> str:
    """Run the linking model (operation `link`: Trackastra) on two populations it can be asked about (`linkable`),
    and keep its one-to-one links between cells both views show as same-object links with its own score (basis
    "linking"); a cell linked to two is a division or an ambiguity, not linked; a link already kept is not kept
    twice. Words for what happened."""
    from .brain.registry import ToolFailed
    from .operations import Prompt, runner
    from .population import capture_of

    why = linkable(hub, a, b)
    if why:
        return f"no links made: can't tell which cells are the same: {why}"
    try:
        done = await runner(hub).run("link", capture_of(hub, b), Prompt("populations", text=f"{a.id}>{b.id}"),
                                     params={"a": a.id, "b": b.id})
    except ToolFailed as exc:  # can't run here, or the adapter can't tell (the views do not line up...): said, and
        return f"no links made: {exc}"  # the populations are still compared below
    identity = identity_of(hub)
    out = done.output or {}
    links = out.get("links") or []
    froms, tos = {}, {}
    for link in links:
        froms[link["from"]] = froms.get(link["from"], 0) + 1
        tos[link["to"]] = tos.get(link["to"], 0) + 1
    already, _ = linked_pairs(identity, a, b)
    # The model's links that were not one to one: the adapter keeps them out of `links` and names both ends
    # (`operations.one_to_one`).
    kept, two, outside, again, scores = 0, {str(c) for c in out.get("ambiguous") or []}, 0, 0, []
    for link in links:
        x, y = str(link["from"]), str(link["to"])
        if froms[x] > 1 or tos[y] > 1:  # kept defensively: an implementation that returned them is still not linked
            two |= {x, y}  # both ends, as the adapter names them
            continue
        mx, my = a.members.get(x), b.members.get(y)
        if mx is None or my is None or not (_inside_both(hub, a, mx, a, b) and _inside_both(hub, b, my, a, b)):
            outside += 1
            continue
        if (x, y) in already:
            again += 1
            continue
        score = float(link.get("score") or 0.0)
        identity.same_one(member_node(a.id, x), member_node(b.id, y), basis="linking", confidence=score,
                          by="query_population")
        already.add((x, y))
        kept += 1
        scores.append(score)
    said = [f"{kept} {'cell' if kept == 1 else 'cells'} linked one to one by the linking model"
            + (f" (its scores {min(scores):.2f} to {max(scores):.2f})" if scores else "")]
    compared = out.get("compared")
    if isinstance(compared, dict):  # only cells inside the part both views show were compared; the rest can't tell
        said.append(f"it compared {compared.get('a', 0)} of {a.id}'s cells and {compared.get('b', 0)} of {b.id}'s, those "
                    "inside the part of the slide both views show; for the others it can't tell")
    if two:
        said.append(f"{_and(sorted(two, key=_cell_order))} {'was' if len(two) == 1 else 'were'} in links that were not one to one (a "
                    "division, a merge, or the model could not tell which), so not linked")
    if outside:
        said.append(f"{outside} left out: outside the part of the slide both views show")
    if again:
        said.append(f"{again} already linked earlier, not linked twice")
    return "; ".join(said)


def identity_of(hub) -> Identity:
    found = getattr(hub, "identity", None)
    if not isinstance(found, Identity):
        found = Identity(None, today=lambda: time.strftime("%Y-%m-%d"), hub=hub)  # no session store: a day is a session
        try:
            hub.identity = found
        except AttributeError:
            pass
    return found
