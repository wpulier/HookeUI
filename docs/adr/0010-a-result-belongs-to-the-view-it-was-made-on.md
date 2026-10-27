# 10. A result belongs to the view it was made on

**Context.** Results arrive after the view they were made on has gone.

- **The live crash.** On 2026-10-06 the chat showed “Find failed: AttributeError: 'NoneType' object has no attribute 'jpeg'”. The cell finder took 6-8 s, and the finder then looked its frame up again by id. The frame ring keeps pixels for 4 s.
- **Late writes in the wrong place.** The outline check rewrote an outline's polygons about 3 s later without the anchor that placed them, so they could land where the slide used to be.
- **Cleared cells came back.** The cell finder cleared old cells and added new ones 6-8 s later, so cells the user cleared in between reappeared.
- **Unverified positions.** The studio drew outlines from an older epoch, dashed, at positions nothing had verified.
- **Nothing to tell the brain.** When something went wrong there was nothing that could say “this result is from the view at 17:38:02”, so a late answer was either drawn at the wrong place or lost.

The motion signals cannot stand in for a check. `StillChange` runs only while the slide is still and gives one verdict for the whole view. `movement.py` runs only for a motion request or a motion watch. So their silence does not mean nothing moved.

**Decision.** Every result names the view it was made on, and it is shown on the live view only where
its own pixels still match.
- **A Capture is the view a piece of work is about:** the frame's bytes and facts, held by whoever needs them, with a peak memory bound reserved before allocation. Slow code never looks a frame up again by id.
- **A Ticket per request** (turn, click or command) records the generations of what it may change. A clear, a cancel, a removed target, a tracking reset or a new day supersedes it.
- **One `apply` is the only way a result changes what is visible.** It checks by destination:
  - **on its captured view** (a close-up or panel showing the capture): the request and target are still valid and the capture is the right one;
  - **on the live view**: the same, **plus** placement verified against the current picture. That means a pixel match at the predicted offset, per member or outline, within the check's validity window (time, slide travel, geometry, observed change).
- **Wording follows the evidence.** A failed or missing check means *unverified*, never *moved*: focus, occlusion and a poor frame fail checks too. The brain is told “131 verified in place, 11 unverified”. An unverified result is shown on its captured view, never over the live image.
- **A superseded request keeps its evidence and changes nothing on screen.** The reason reaches the brain as a fact.

**Cost.** Captures cost memory while held, and a request that cannot reserve it is refused with the
reason. Members drawn live need throttled pixel checks while shown, and members the checks miss are
shown on their capture instead of live. Every writer, the object follower included, goes through one
gate. The hub's re-anchor loop is the one exception, because it verifies by template itself.

Design: `docs/design/2026-10-06-captures-operations-evidence.md`.
