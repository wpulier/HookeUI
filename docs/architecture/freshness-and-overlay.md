# Freshness and overlay contract

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- **Newest frame only, end to end (the audit of 2026-10-12):** every stage that can't keep up skips to the newest frame
  and never queues, so delay can't build up anywhere. Demo night's lag was frames queueing at every stage of a loaded
  Mac.
  - **The capture app:** one frame in flight.
  - **The bridge:** the recorder has its own thread, and drops only recording frames more than 3 s behind
    (`RECORDING_BEHIND_S`, counted as `dropped`). Health never waits on it. Each MJPEG part ends with the next
    boundary, Nagle off.
  - **The orchestrator:** `FrameSource` drains the stream continuously and analyses the newest complete frame,
    counting the rest as `skipped`; background decodes run off the event loop.
  - **The studio:** one decode at a time, the newest winning, never an older frame.
  - **The check:** sim `live-under-load` (twice the cores busy, a recording on) fails when what the app shows falls
    more than 10 frames behind the bridge, or the gap grows.
- **True delay:** each frame carries `X-Frame-At`, when the bridge took it (`ba` in the frame header). The
  performance meter's camera-to-screen runs from there, and `intake` is the time between the bridge and the app,
  which the meter couldn't see before.
- **A frozen iPhone picture heals itself:** byte-identical frames for 3 s get a camera reset, at most once a minute.
  For Blackmagic the checklist names it and Reset camera is one click: a still scene through H.264 can repeat
  exactly.

- The numbers below are written once, in `contract/overlay.json`, and read by the studio
  (`apps/studio/src/overlay.ts`), the server (`orchestrator/contract.py`) and the sim harness;
  change them there. It also holds the magnifier's limits and the order a researcher's work
  is listed in.
- Freshness is two named values: `feedStaleS` (3 s): with no frame newer than that, the
  orchestrator reports a non-live state; `frameDimMs` (1.5 s): a picture with no new frame for
  that long is dimmed by the studio, which draws no overlays on it.
  - A still picture is the normal state of a microscope (a slide at rest) and is never called
    frozen, dimmed, refused or mentioned to the voice. Every frame carries `still`, the seconds
    its eyepiece view has not changed at all (`orchestrator/quality.py`), for the logs only.
    (2026-10-06: a "frozen" state based on it blanked the live view of a still slide; the iPhone
    denoises a still view to differences of 0.0024-0.0066 a frame.)
- Every frame carries a cumulative motion offset and an epoch. Outlines are stored in
  the coordinates of their anchor frame and drawn at `p + (offset_now - offset_anchor)`.
- A new motion epoch means continuity was lost (for example a blurred whip-pan). Objects
  from an older epoch are re-found by template match (score at least
  `REFIND_ACROSS_EPOCHS_SCORE`, 0.8) or shown as "re-finding"; they are never drawn at an
  unverified position.
- Places seen before (W5, `orchestrator/motion.py`): motion keeps one still view per 200 px of
  slide, per epoch (`bank`, ORB features computed once). For 30 s after continuity is lost, every
  5th steady frame is compared with the 6 newest views of other epochs; enough agreeing matches
  (30), spread over the view (40% of a 4x4 grid), on two checks in a row rejoin that epoch, so
  all its outlines, parts and cells come back by one verified offset (sim scenario `refind`).
  What was made on the epoch left is carried over by that offset, unverified until its own pixels
  match, also when it lands after the rejoin (`Hub._carry_over`, every frame: a click outlined while
  the rejoin was confirmed; sim `arena` after `footage-switch`). A new epoch always takes a number
  never used in the run (a rejoin goes back to an old one), since links, remembered views and slide
  maps are kept by it. The cells' layer and identity follow the same links by the same rule
  (`epochs.follow_links`), never past the epoch the view is in now; a highlight reuses only a "tracking" outline.
- The slide's move is what most of the view agrees on: phase correlation is checked every frame
  against a vote of ORB feature matches with the keyframe (`MotionTracker._consensus`), and
  overruled when they disagree, so cells swimming over a still slide do not drag outlines along
  (2026-10-06: outlines drawn hundreds of px off on a wet mount). `make tracking-bench` scores
  outlines against the truth of eyepiece tours (textured, sparse, with swimmers); its headline is
  how often a visible outline is in the wrong place.
- Outlines that follow their own thing (option TRACK "objects", the default; `orchestrator/objtrack.py`,
  `gpu_worker/objects.py`): EdgeTAM's video model on the heavy GPU worker follows the 4 newest
  outlines 10 times a second, started only from a verified position. It moves an outline only when
  its object moved on its own (more than 8 px from where the slide's motion puts it), the slide is
  calm (slower than `fadeStartPx`) on frames the motion tracker registered (a frame it could not
  register carries the last offset over and only looks still: in the first frames of a cut EdgeTAM
  latched on to the new view, sim `refind` 72 px), the view has been sharp at a steady scale for a second (never
  flagged blurry, the eyepiece disc's size within the coach's 0.3%: the phone refocusing by itself
  is not followed) and the object's template agrees; otherwise slide motion and re-finding carry
  on. EdgeTAM says roughly where the object went, and its template, searched within 6 px of there,
  exactly where (the outline then keeps its shape and moves by that). The shape the user chose stays: an
  outline another model drew (micro-sam, SAM 2.1, SAM 3.1, a promoted cell's mask; `made_by`) is only moved, by
  how far EdgeTAM's mask moved on the slide since the outline was last placed, never redrawn with EdgeTAM's
  mask (live 2026-10-11: a micro-sam outline showed EdgeTAM's shape 0.1 s later); only EdgeTAM's own outlines
  take its video model's shape. Each followed outline carries `followed_by` (the model, shape kept or redrawn,
  since, until) for how it was made ("drawn by micro-sam, followed by EdgeTAM"). The follower runs on its own
  worker (`gpu_follow`), and its decode and placing in a thread. `make object-tracking-bench`:
  a swimming cell on target 82-89% of frames (slide only: 0-3%), tissue unchanged; sim `follow-clicks` keeps click p95 under 300 ms (104 ms).
  From M1.1 until 2026-10-07 the bench gave the follower no view (it never published frames, so captures
  found none) and measured nothing; fixed, it is on target 57-61% of all frames, 89-91% of the frames
  the outline is shown (shown 63-68%, now faded by the slide's measured speed as the studio does).
- A followed outline's trail (`orchestrator/trail.py`, `apps/studio/src/trail.ts`; partner §2.1): each
  follower step says what it found per followed outline (seen: its place known on a calm, clear,
  registered view; unsure; missed; lost, also after the misses that dropped it and while the hub
  re-finds it; stopped, no longer followed), and only "seen" places are kept, in slide coordinates
  (the outline's centre minus its anchor's offset) with their frame's time and id, so the slide's own
  motion is taken out. Only the follower's own moves are the object's motion; a correction (the hub's
  re-anchoring or drift correction, or the follower's own move of at most 10 px while the slide travelled
  20 px or more since the outline's anchor) ends the segment ("corrected", not a gap) and the next starts
  where the outline is now, so earlier places stay where they were measured. Its cost: a slow swimmer's
  small moves during a pan give no trail there. Segments are within
  one epoch and broken at every other state and after 1 s nothing observed (a reset, following
  switched off); nothing joins two. The studio draws the current epoch's segments under the outlines,
  hidden and faded with them, only for outlines shown in the eyepiece, older pieces fainter over 60 s,
  a ring where it was found again and a cross where it was lost; the selected one (or the only one
  that went somewhere) says, while fresh, how far and how long ("about 15 µm (scale estimated from the
  objective) over the slide in 10 s", px without a scale), and with a break only over the time its
  place was known ("in the 10 s of the last 19 s its place was known (6 gaps)"). It says anything only
  past 12 px of path and 10 px from where it started (the hub's largest drift correction). The brain's
  scene table says the same for such an outline only. `make object-tracking-bench`: a swimmer's kept
  path 475-552 px against the truth's 495-562 px over the same spans, its kept places 0.7-0.8 px off
  (p50), and a cell fixed on the slide says nothing.
  Asked to keep or show where it went, `track_motion(of=...)` keeps the trail as a tracks item (above).
- Template re-anchoring only runs after half a second of stillness, never mid-pan, may
  only nudge an outline by 10 px within an epoch, and never re-learns the template.
- Outlines are drawn only inside the eyepiece (`inViewRadius`, 95% of its radius; the brain's
  `in_view` and the sim harness use the same) and fade while the slide moves faster than
  4 px/frame (`fadeStartPx`, gone at `fadeEndPx`, over the last `speedWindow` frames).
- **A population found on the Stage's still** (S5a; `orchestrator/still_fit.py`, `population.Layer._check_still`). A run
  on a still asset finds its members on the original's own pixels (`sourceImage`), while the bridge plays that still
  fitted into its frames: FFmpeg turns a JPEG by its EXIF orientation (a PNG by its eXIf only in FFmpeg after 7.1: `still_fit.ffmpeg_turns_png_exif` asks the FFmpeg in use once), then
  `scale=W:H:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=W:H:(ow-iw)/2:(oh-ih)/2`
  (`simulated_camera.py` `filter_chain`, the one path for the user's assets and test footage), the pad's offset rounded
  down to the decoded pixels' chroma subsampling. The layer draws such a population on the Stage only while the Stage
  shows that still:
  - **Each check** (every 0.3 s, off the event loop) reads the frame's intake tag (its asset), takes the still's fit from
    the played file's head (size, orientation, subsampling) and the frame's size, and matches the frame's picture with
    the still's own pixels fitted the same way (a quarter of the frame's size, normalised correlation at least
    `still_fit.STILL_SCORE`, 0.9). The bridge plays one picture, so a repeated frame is matched once, never decoded again.
  - **Matched, every member is verified at once** (a still never moves, so its members cannot move apart), and the
    layer wire carries `fit` (`frame`, `rect`: the picture's place inside the letterbox, `matrix`: original px to frame
    px, `orientation`), the frame's epoch as its `view` and no correction. The studio draws it only on frames of that size
    and epoch that the motion tracker registered (a cut to the camera goes unregistered first), by the matrix alone;
    clicks and hover map back through it (`member_hit`, `memberAt`), and a new result's outlines ramp in once.
  - **Otherwise it is kept on the original image** and nothing is drawn, as before: the Stage shows another input or the
    live camera ("kept on the original image; open the saved result to inspect its particles"), or the Stage's picture
    did not match ("does not match the original image they were found on").
  - **A Project opened again shows it again** when its landing puts that still on stage (`App.reopen_layer`; the
    session journals which population its layer showed: `services/orchestrator/day/AGENTS.md`, "What opening brings
    back"). It is the same layer and the same check: nothing is drawn until a frame of that still verifies it, and the
    live camera never gets it.
  - **No window of its own once drawn there** (Will's call, 2026-10-25, for a This Tab run): the run's window waits on
    the check, never on the asset's name alone. Shown on the Stage's still, the run journals `workspace-specimen`
    "checking" before the population's save is filed, so `workspace_views.reconcile` places nothing for it yet (and the
    action's reply says `placed: "specimen"`). The layer's first check that verifies its members on that still decides
    "drawn": no masks window (nor its Answer); it is kept in Data, listed under completed work with "Drawn on the
    specimen; kept in Data." (where Add to Tab… and Open in this Tab place it), so nothing is left pending. A check that
    does not verify them within `population.SPECIMEN_WAIT_S` (10 s), or the layer hidden (the Stage changed) or
    replaced (Run again) first, decides "placed": its masks window on its Tab, as for any other run. Either decision
    is made once, reconciled at once and sent to every studio as `action-placed` (`specimen` or `tab`), so the run's
    footer never says a Tab holds what it does not. A run left "checking" by an earlier start of the app is placed.
    A New Tab run keeps its masks window (the Tab it opened is for it: its `workspaceOrigin` says `destination: "new"`),
    a population with no members has nothing to draw, and a live camera run keeps its window, since whether its
    outlines stay on the specimen is known only as the slide moves.
  - **Its cost:** nothing per frame beyond drawing; the live camera's frame path and checks are unchanged. In vitest
    (node, not a browser) the studio's mapping of 500 members of 64 points each costs about 0.55 ms a frame and a hover
    hit test about 0.9 ms at worst; the `still-population` sim scenario checks the server's side through the real
    bridge.
    `tests/test_still_fit.py` checks the fit against FFmpeg itself through the bridge's chain (240 cases of size,
    format, subsampling and orientation measured within 0.4 px when it was written); `contract/overlay.json`'s numbers
    are unchanged.
