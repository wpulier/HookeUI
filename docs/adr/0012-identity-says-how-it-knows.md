# 12. Identity says how it knows

**Context.** Several parts of the app each know a piece of “is this the same thing?”:
- the object follower knows continuity while it holds (`objtrack.py`);
- template re-finding knows a place came back (`hub._reanchor`, `motion.py`'s rejoin);
- the slide map knows where things were within an epoch (`slidemap.py`);
- embeddings know what *looks alike* (`seen.py`, `taught.py`).

These are different facts. “Looks like the platelet from before” is not “is the platelet from
before”. A follower that lost a cell does not mean the cell is gone. Measurements taken at another
objective, or with another lamp setting, are not comparable. Today nothing joins these pieces, and
nothing stops one from being read as another.

**Decision.** Identity is two kinds of link that are never mixed, and every claim says how it is
known.
- **Same object** links one physical thing across captures. Its basis is continuity, re-find, promotion, a linking implementation, or the user. Each link has a confidence, and a chain's confidence is its weakest link. Connected same-object links make an **individual** (`i4`) with a history.
- **Same kind** links things that are alike: embedding similarity at or above that model's threshold, the same group, or the user's taught label. Same kind never joins individuals.
- **A missing link means *can't tell***, never *different*. The brain is told why (“continuity was lost at 14:02”).
- **Comparisons over time** use only observations whose measurements share a basis: the same objective and geometry, and the same imaging for intensity. Otherwise the answer is pixels with the reason, or “not comparable”.
- **Links are evidence:** saved, journaled and read back like populations.

**Cost.** More answers are “can't tell” than a confident guess would give. Following members and
comparing captures need the identity layer before they are built, so both moved out of the
scaffolding's M3 into this plan. Links take space in the day's evidence.

Design: `docs/design/2026-10-07-session-knowledge.md`.
