"""Every interchangeable way of doing a job, kept side by side so they can be switched live
and A/B tested. Nothing that works gets removed: new options are added here with the
measurements that justify their defaults (docs/bench/phase0.md)."""
from __future__ import annotations

import os
import threading
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from . import licences
from .learner import LEVELS


def _cap_from_env() -> float | None:
    """MICROMIND_RESEARCH_CAP, or no cap: a value that is not an amount never stops the app."""
    raw = os.environ.get("MICROMIND_RESEARCH_CAP")
    try:
        return parse_cap(raw)
    except ValueError:
        print(f"MICROMIND_RESEARCH_CAP={raw!r} is not an amount in dollars; no research cap", flush=True)
        return None


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    note: str  # what we measured, shown in the studio's picker
    spec: str = ""  # provider spec for vision models
    needs: str = ""  # key required, e.g. GEMINI_API_KEY
    uses: tuple[str, ...] = ()  # the models, weights and services it runs on (licences.py)
    weights: str = ""  # fine-tuned weights it cannot run without (finetuned.py): unavailable until made here
    # The catalog's model this option is a use of (catalog/models/<id>; ADR 0018): its key and licence components come
    # from the model's folder unless the option says otherwise. Not on the wire.
    model: str = ""
    # Other catalog models it runs (a method or a chain: Molmo's pointing runs Molmo 2, following runs EdgeTAM video,
    # "EdgeTAM then SAM 3.1" runs both): readiness adds their steps (a download, a key), so what it needs is said.
    also: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.also:
            from gpu_worker.manifests import get

            unknown = [m for m in self.also if get(m) is None]
            if unknown:
                raise ValueError(f"option {self.id}: no catalog model {unknown[0]!r}")
        if self.model:
            from gpu_worker.manifests import get

            entry = get(self.model)
            if entry is None:
                raise ValueError(f"option {self.id}: no catalog model {self.model!r}")
            if not self.needs:
                object.__setattr__(self, "needs", entry.needs)
            if not self.uses:
                object.__setattr__(self, "uses", entry.licence)

    @property
    def tier(self) -> str:
        return licences.tier(self.uses)

    @property
    def permitted(self) -> bool:
        return licences.permitted(self.uses)


VISION = {
    o.id: o
    for o in [
        Option("gemini-3.8-flash", "Gemini 3.8 Flash", "15/15 right object, ~2.0 s", "gemini:gemini-3.8-flash@low", "GEMINI_API_KEY", model="gemini-3.8-flash"),
        # Reads the picture at high resolution. Naming bench, 180 pictures (docs/bench/naming.md):
        # exact 56% vs 53%, coarse 80% vs 79%, ECE 0.37 vs 0.40, +0.17 s; within run-to-run
        # noise, so not the default. (Thinking "minimal" is rejected by this model: "low" is its lowest.)
        Option("gemini-3.8-flash-hires", "Gemini 3.8 Flash (high resolution)",
               "naming bench: exact 56% vs 53%, coarse 80% vs 79%, +0.17 s (within noise)",
               "gemini:gemini-3.8-flash@low+high", "GEMINI_API_KEY", model="gemini-3.8-flash"),
        Option("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "14/15, ~1.0 s", "gemini:gemini-3.5-flash-lite@minimal", "GEMINI_API_KEY", model="gemini-3.5-flash-lite"),
        # Fast-pass candidates, measured at the same hour (2026-10-06, 03:15, fast bench 40 cases
        # and tool choice 20; Gemini 3.8 was then ~2x its usual latency): see BRAIN_FAST.
        # The default for looks and naming (2026-10-06, same hour as 3.8 Flash): pointing 30/30 at
        # p50 1.09 s (3.8: 30/30 at 3.90 s); naming 180 pictures exact 62% / coarse 79% at 1.7 s
        # (3.8: 64% / 79% at 3.7 s; within run-to-run noise, docs/bench/naming.md).
        Option("gemini-3.5-flash", "Gemini 3.5 Flash", "pointing 30/30 at 1.1 s; naming 62% exact, 79% coarse at 1.7 s; fast bench 78/80 at 1.04 s",
               "gemini:gemini-3.5-flash@minimal", "GEMINI_API_KEY", model="gemini-3.5-flash"),
        Option("gpt-5.6-luna-priority", "GPT-5.6 Luna (priority)", "fast bench 78/80, p50 1.13 s / p90 1.66 s",
               "openai:gpt-5.6-luna@none+priority", "OPENAI_API_KEY", model="gpt-5.6-luna"),
        Option("gpt-6-luna", "GPT-6 Luna", "12/15, ~1.8 s", "openai:gpt-6-luna@none", "OPENAI_API_KEY", model="gpt-6-luna"),
        Option("gpt-6.1-sol", "GPT-6.1 Sol", "28/30 right with tools (spike S1); rejects reasoning effort none, so low", "openai:gpt-6.1-sol@low", "OPENAI_API_KEY", model="gpt-6.1-sol"),
    ]
}

POINTING = {
    o.id: o
    for o in [
        Option("box", "Model draws a box", "best on every model tested"),
        Option("marks", "Model picks a numbered spot", "8-13/15; snaps to detected spots"),
        Option("snap-first", "Instant guess then box", "amber ring in ~3 ms for small spots (4 of 6 right); the box replaces it"),
        # Segmenter-first Set-of-Mark (orchestrator/masks.py): EdgeTAM masks the settled view on the
        # heavy GPU worker (~1 s, off the click path); the model picks numbers, and a number's mask is
        # the outline (no second segmentation). Benches: docs/bench/finding.md ("Pointing masks").
        # Grounding bench (15 questions x 2, gemini-3.5-flash, 2026-10-06): 26/30 right against box's
        # 30/30 (the numbered outlines drew the model to an outlined thing near an unoutlined speck),
        # p50 1.34 s against 1.16 s; BCCD find bench: white cell 0.83 vs 0.92 recall, every red cell
        # 0.52 vs 0.57 (touching cells merge into one mask). So not the default.
        Option("masks", "Model picks a numbered outline",
               "grounding bench 26/30 (box 30/30), p50 1.34 s vs 1.16 s; masks made in ~1 s after the slide settles",
               uses=("edgetam",), also=("edgetam",)),
        # Molmo 2 4B (Ai2, Apache-2.0) on this Mac, in its own GPU worker process (orchestrator/molmo.py,
        # gpu_worker/molmo.py): the model answers and names its targets as "box"; beside it Molmo points
        # at what the user's words are about, and each point is outlined as a click. Grounding bench
        # (2026-10-06, gemini-3.5-flash): 26/30 right (box 30/30; the same two questions missed each
        # time), Molmo p50 3.6 s on a new view and 2.0 s on a view read ahead (box 1.3 s); BCCD find
        # bench worse on every class (a click on a white cell's point outlines its nucleus; at most ~14
        # points an answer). First use loads ~20 s, ~12 GB of GPU memory. So not the default.
        Option("molmo", "Molmo 2 points",
               "grounding bench 26/30 (box 30/30); ~3.6 s on a new view, ~2 s once the settled view is read (box 1.3 s); "
               "first use loads ~20 s and ~12 GB", uses=("molmo2-4b",), also=("molmo2-4b",)),
    ]
}

OUTLINE = {
    o.id: o
    for o in [
        Option("edgetam", "EdgeTAM", "~15-60 ms per outline", model="edgetam"),
        Option("sam2.1-tiny", "SAM 2.1 tiny", "~15-85 ms per outline", model="sam2.1-tiny"),
        Option("sam3.1-local", "SAM 3.1 (this Mac)", "~1.4 s per outline (its tracker: 41/54 fit on the outline-fit bench); first use loads 5.3 GB", model="sam3.1-local"),
        Option("sam3.1-fal", "SAM 3.1 (cloud)", "~1.4-1.9 s warm per outline", model="sam3.1-cloud"),
        Option("edgetam>sam3.1-fal", "EdgeTAM then SAM 3.1 (cloud)", "SAM 3.1 redraws EdgeTAM's box; whole objects from a click", needs="FAL_KEY", uses=("edgetam", "sam3.1", "fal-api"), also=("edgetam", "sam3.1-cloud")),
        Option("edgetam>sam3.1-local", "EdgeTAM then SAM 3.1 (this Mac)", "SAM 3.1 redraws EdgeTAM's box; ~2-3 s", uses=("edgetam", "sam3.1"), also=("edgetam", "sam3.1-local")),
        # micro-sam's light-microscopy SAM (vit_b_lm) from a click, in its own Python
        # (noncommercial/microsam_cells.py; research edition only). BCCD, a click at each labelled
        # cell's centre (bench/cells_bench.py, 2026-10-06): platelets 8/10 (EdgeTAM 2/10), white cells
        # 11/12 (10/12), red cells 68/100 (65/100); but a click on a new frame takes ~0.54 s against
        # EdgeTAM's ~0.08 s (over the 300 ms a click may take), so not the default.
        Option("micro-sam-lm", "micro-sam (light microscopy)",
               "BCCD clicks: platelets 8/10 vs EdgeTAM 2/10, white cells 11/12 vs 10/12; ~0.54 s on a new frame vs 0.08 s", model="micro-sam-lm"),
    ]
}

# The voice of the brain (docs/bench/live.md). GPT-Live hears and speaks and hands every
# request to the same brain you type to, so voice and chat share one mind and one set of
# tools. Gemini 3.8 Live (its own separate tools) was retired from the studio when the brain
# was unified; voice/gemini_live.py remains for bench/live_probe.py.
VOICE = {
    o.id: o
    for o in [
        Option("gpt-live-1", "GPT-Live (OpenAI)", "voice ~0.1 s, outline ~2-4 s; the brain does the looking", model="gpt-live-1"),
        # One model hears, sees the camera and Hooke's window each turn, calls the app's tools itself and points at what
        # it talks about (voice/openai_realtime.py, voice/partner.py). Not yet measured on the journey bench, so not the
        # default; WebRTC only.
        Option("gpt-realtime-2.1", "One brain (hears · sees · acts)",
               "GPT Realtime 2.1: sees each turn, acts with the app's tools, points; not yet measured against GPT-Live",
               model="gpt-realtime-2-1"),
    ]
}

# How the one-brain voice knows a turn (voice/openai_realtime.py). Hold: the talk key or the Talk button is held while
# the user speaks, and released to answer (turn detection off; nothing it says is cut by a cough). Open: it listens all
# the time and its own semantic turn detection decides, and the user can talk over it (needs headphones: otherwise it
# hears itself). GPT-Live ignores it.
TALK = {
    o.id: o
    for o in [
        Option("hold", "Hold to talk", "hold the talk key or the Talk button while you speak; release to hear it answer"),
        Option("open", "Open mic", "it listens all the time and you can talk over it; use headphones, or it hears itself"),
    ]
}

# The key held to talk with the one-brain voice (the studio's window-level key handler, also while typing). Control+
# Option is VoiceOver's modifier: with VoiceOver on, choose Right Option. Holding the Talk button always works.
TALK_KEY = {
    o.id: o
    for o in [
        Option("control-option", "Control+Option",
               "hold Control+Option with Hooke in front, also while typing; it is VoiceOver's modifier, so with VoiceOver "
               "on choose Right Option"),
        Option("right-option", "Right Option", "hold the right Option key alone with Hooke in front"),
    ]
}

# How the brain uses its tools (orchestrator/brain/loop.py, docs/bench/brain.md). Both send
# one model call for a plain question.
BRAIN = {
    o.id: o
    for o in [
        Option("json-actions", "Answer with actions", "30/30 right, p50 1.95 s with tools offered (same as before)"),
        Option("fc", "Function calls (answer first)", "S1: 30/30 on Flash, p50 2.07 s; gpt-6.1-sol 28/30"),
    ]
}

# How dissect finds the parts inside an object (orchestrator/dissect.py, spike S3).
DISSECT = {
    o.id: o
    for o in [
        Option("grid", "Point grid inside the object", "S3: plausible parts on 2 of 8 stomata (little visible structure); ~0.6 s + naming", uses=("edgetam",), also=("edgetam",)),
        Option("vlm-boxes", "Model boxes the parts it sees", "S3: found no parts at this magnification; better on detailed views", uses=("edgetam",),
               also=("edgetam",)),
    ]
}

# What researchers think with (research_worker/engines.py, spike S5).
RESEARCH_ENGINE = {
    o.id: o
    for o in [
        Option("gpt-6.1-sol", "GPT-6.1 Sol + web search", "S5: searched 3/3 with real sources, 9-12 s, ~$0.04-0.05 a lookup", model="gpt-6.1-sol"),
        Option("gemini-3.8-flash", "Gemini 3.8 Flash + Google Search", "S5: 3-6 s, ~$0.002, but often answers from memory without searching", model="gemini-3.8-flash"),
    ]
}

# What draws researchers' pictures (spike S4).
PRESENTATION = {o.id: o for o in [
    Option('off', 'Standard layout only', 'App-rendered findings; no presentation model call'),
    Option('auto', 'Auto', 'Choose an enabled producer for the retained report; findings remain available'),
    Option('claude-opus-5-5', 'Claude Opus 5.5', 'Arrange validated source blocks from an exact findings version', model="claude-opus-5-5"),
    Option('gpt-6.1-sol', 'GPT-6.1 Sol', 'Arrange validated source blocks from an exact findings version', model="gpt-6.1-sol"),
]}

IMAGE_MODEL = {
    o.id: o
    for o in [
        Option("gpt-image-2.5-flare", "GPT Image 2.5 Flare", "S4: specimen untouched, labels on the right parts, ~11 s, ~$0.02", model="gpt-image-2.5-flare"),
        Option("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", "S4: specimen untouched, ~17 s", model="gpt-image-2.5-sunburst"),
        Option("gemini-3.1-flash-image", "Gemini 3.1 Flash Image", "S4: invented a pore once and softened a crop; not for evidence", model="gemini-3.1-flash-image"),
    ]
}

# The app's running look at the view (orchestrator/sight.py), off the answer path.
SIGHT = {
    o.id: o
    for o in [
        Option("gemini-3.8-flash", "Gemini 3.8 Flash", "sight bench: 27/30 asked-about things boxed, p50 2.0 s, after the slide settles", model="gemini-3.8-flash"),
        Option("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "sight bench: 21/30 boxed, p50 1.5 s", model="gemini-3.5-flash-lite"),
        Option("off", "Off", "the voice only knows the view after it asks the app"),
    ]
}

# The brain's fast pass (orchestrator/brain/fast.py): act without a new look when it can.
# Default changed 2026-10-10 after a paired retained-frame comparison (9 requests per
# option, identical three-sequence harness): 3.5 proposed five stale-ID plans; 3.8
# proposed zero. This narrow correction sequence is not a general quality or latency
# benchmark. Earlier measurements below remain dated evidence; saved choices persist.
BRAIN_FAST = {
    o.id: o
    for o in [
        # Every basic command ("close that", "magnify that") waits on this call. Fast bench (80:
        # 40 cases x 2), all three at the same hour, 2026-10-06 ~03:40: Gemini 3.5 Flash 78/80 at
        # p50 1.04 s / p90 1.24 s; GPT-5.6 Luna on OpenAI's priority tier 78/80 at 1.13 / 1.66 s;
        # Gemini 3.8 Flash 79/80 at 2.01 / 3.40 s (live p50 1.95 s that night). Flash-Lite models
        # were 36/40 at 0.7 s and acted on cut-off words ("Can you close the"): not for voice.
        Option("gemini-3.5-flash", "Gemini 3.5 Flash", "fast bench 78/80, p50 1.04 s / p90 1.24 s", model="gemini-3.5-flash"),
        Option("gpt-5.6-luna-priority", "GPT-5.6 Luna (priority)", "fast bench 78/80, p50 1.13 s / p90 1.66 s", model="gpt-5.6-luna"),
        Option("gemini-3.8-flash", "Gemini 3.8 Flash", "fast bench 79/80, p50 2.01 s / p90 3.40 s (2026-10-06; 0.81 s on 10-04)", model="gemini-3.8-flash"),
        Option("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "fast bench: 21/24, p50 0.68 s; answered detail it could not see", model="gemini-3.5-flash-lite"),
        Option("off", "Always look first", "every request waits for the vision call (~2 s)"),
    ]
}

# What outlines every cell in the view at once (tools.outline_cells, find by="cells"; the studio's
# Cells button). Cellpose-SAM's and micro-sam's light-microscopy weights were both trained on data
# licensed for non-commercial use, so only the research edition offers them and the commercial
# edition uses EdgeTAM's automatic masks (orchestrator/licences.py, ADR 0005). BCCD smears, 12 (bench/cells_bench.py,
# docs/bench/finding.md "micro-sam", 2026-10-06): Cellpose-SAM outlines 113/126 labelled red cells,
# 9/12 white, 0/10 platelets; micro-sam 99/126, 9/12, 2/10 in ~0.7-1.1 s against Cellpose's 3.6-7.9 s
# (the same Mac, other work on the GPU). With the model picking (find by="cells", gemini-3.5-flash):
# every red cell 0.79 recall against 0.90, white cells 0.75 both. Cellpose stays the default.
# Cellpose-SAM fine-tuned on the user's corrected cells (K4; bench/finetune_cells.py, an explicit job, never
# from clicks): usable as soon as its weights exist here, recommended only if it wins on its held-out days in
# the replay bench (docs/bench/replay.md), and the user decides.
CELLS = {
    o.id: o
    for o in [
        Option("cellpose-sam", "Cellpose-SAM", "phase 0: ~350 cells in ~1.5 s; trained on cells 7.5-120 px across", model="cellpose-sam"),
        Option("cellpose-sam-tuned", "Cellpose-SAM fine-tuned on your corrections",
               "made from your corrected cells (make finetune-cells); recommended only if it wins on its held-out days "
               "(docs/bench/replay.md)",
               weights="cellpose-sam-tuned", model="cellpose-sam-tuned"),
        Option("micro-sam-lm", "micro-sam (light microscopy)",
               "BCCD: red cells 99/126 (Cellpose 113/126), white 9/12 (9/12), in ~0.7-1.1 s vs 3.6-7.9 s", model="micro-sam-lm"),
        # EdgeTAM's automatic masks (gpu_worker/automask.py): every distinct thing in the disc from a grid
        # of point prompts, as the pointing option "masks" makes them; open weights, so the commercial
        # edition has a population too. Not benchmarked on cells yet (overlapping masks; a stated
        # arbitration draws them, measurements use each mask as it is).
        Option("edgetam-automask", "EdgeTAM automatic masks",
               "every distinct thing from a grid of EdgeTAM prompts, ~1 s; not benchmarked on cells yet", model="edgetam"),
        # ImageJ's Analyze Particles in the managed Fiji (fiji_bindings.py): particles darker than its Default
        # threshold, not cells; proven against its macro on every Fiji generation. Not benchmarked on cells, and after
        # every cell finder, so it is never the one a fallback picks: only the user's choice.
        Option("fiji-analyze-particles", "Analyze Particles (Fiji)",
               "particles darker than ImageJ's Default threshold, not cells; proven against its macro; not benchmarked",
               model="fiji-analyze-particles"),
        Option("basic-bright-objects", "Bright objects",
               "bright regions using Gaussian smoothing and Otsu threshold; not biological cell identities",
               model="basic-bright-objects"),
        Option("off", "Off", "cells are outlined one click at a time"),
    ]
}

# How a poor view is handled (orchestrator/quality.py, roadmap W1-1). "tell" (default since
# 2026-10-06): the model gets what the app measured (sharpness against this slide's best,
# brightness, contrast, glare) as numbers and decides itself; code never refuses or advises.
# "gate": code decides (not named, and the coach's written advice); the live test of 2026-10-06
# felt the model "got dumber" with code deciding for it. "flag" only records it.
QUALITY = {
    o.id: o
    for o in [
        Option("tell", "The model judges it", "Sight and naming are told the measured sharpness and brightness, and decide"),
        Option("gate", "Don't name a bad view", "Sight and naming a click wait for a view that is in focus, lit and not empty; "
                                                 "they say what is wrong instead"),
        Option("flag", "Name it anyway and note it", "the quality is logged with each look"),
        Option("off", "Off", "every frame is looked at as it comes"),
    ]
}

# When the voice's spoken words need a look, start the vision call early (brain/speech.py,
# roadmap W2-1/W2-2). "needs-look": as soon as the fast pass on the heard words says so, about a
# second before GPT-Live hands the request over, and the hand-off's turn reuses it instead of
# running the fast pass and the look again. The look is input only until the hand-off confirms
# the same words; unused, it is dropped. Costs one vision call when the voice answers by itself.
SPECULATE = {
    o.id: o
    for o in [
        Option("needs-look", "Start the look when the words need one", "the hand-off reuses it; one extra call if the voice answers alone"),
        Option("off", "Wait for the hand-off", "the look starts when GPT-Live hands the request over (as before W2)"),
    ]
}

# How a spoken question's thing is pointed at (brain/speech.py `_point`, brain/fast.py LEAN_POINT; ADR 0016's Act
# loop). "after-voice" (as since W2): only when the voice answered without handing over, 1.5 s after the words, by
# the full fast pass told to only point (the 2026-10-11 demo: 1 of 8 such points acted; the full pass took 1.76 s at
# p50 on ~15k tokens). "lean": at once and whether or not the voice hands over, from the words, the eyes and the
# board only, with the fast model or a named one; the voice journeys bench decides (docs/bench/voice-journeys.md).
REFLEX = {
    o.id: o
    for o in [
        Option("after-voice", "Point when the voice answers alone", "the full fast pass, 1.5 s after the words, only without a hand-off; point bench 28/28 at p50 1.71 s / p90 3.71 s (as before 2026-10-11)"),
        Option("lean", "Point at once from the eyes", "the words, the eyes' things and the board only, with the fast model; point bench 28/28 at p50 1.61 s / p90 1.93 s on Gemini 3.8 Flash",
               needs="GEMINI_API_KEY", uses=("gemini-api",)),
        Option("lean-3.5-flash", "Point at once with Gemini 3.5 Flash", "point bench 26/28, p50 0.75 s / p90 0.97 s (both misses: pointed at the stomata for \"what does a guard cell do?\"); voice journeys 3 of 7 view questions pointed, p50 2.7 s after the words (main: 1 of 7 at 4.7 s)", model="gemini-3.5-flash"),
        Option("lean-lite", "Point at once with Gemini 3.5 Flash-Lite", "point bench 26/28, p50 0.56 s / p90 0.64 s (the same two misses)", model="gemini-3.5-flash-lite"),
    ]
}
REFLEX_MODELS = {o.id: o.model for o in REFLEX.values() if o.model}  # "lean": the fast model

# When a typed request's look starts (brain/loop.py `_turn`; Slice L). "sequential" (since ee959f12, 2026-10-08): the
# fast pass decides from the notes first, and the look starts only when it needs one. "alongside": the look starts
# beside the fast pass, as a spoken request's early look does (SPECULATE); when the fast pass acts from the notes, or
# its steps change what the look would plan on, the look is dropped before anything else is sent and refunded to the
# request once (admission.forgive, 2026-10-10), so the steps, the query rounds and the answer keep their calls.
# Measured on the acting bench's live cases (2026-10-11, 44 cases, 3 attempts, arms interleaved; docs/bench/acting.md):
# the fast pass never needed a look without steps in 141 typed requests, so every early look was dropped and refunded
# and none was used; time to answer did not improve, so "sequential" stays the default.
EARLY_LOOK_TYPED = {
    o.id: o
    for o in [
        Option("sequential", "After the fast pass", "acting bench live cases: 84/132 attempts, typed answers p50 4.03 s, "
               "p90 6.66 s; the look waits for the fast pass's decision (since 2026-10-08)"),
        Option("alongside", "Beside the fast pass", "83/132 attempts, typed answers p50 3.94 s, p90 6.95 s; 141 early looks "
               "sent, none used, all refunded: one more vision call per typed request"),
    ]
}

# When the answer is written again from what the request's tools did (brain/loop.py `_reconcile`). The answer is
# written before its tools run; "every-request" (since 2026-10-09, de058e77) has a model write it again after every
# request that did anything; "contradicted" only when what happened contradicts it: a step failed or is still
# running, an outline is not shown, nothing was outlined, a query's result was never answered, or the request did
# nothing (as before 2026-10-09, without queries and opened resources answered again). Either way the answer never
# says more than the receipts.
ANSWER_CHECK = {
    o.id: o
    for o in [
        Option("every-request", "After every request that did something", "acting bench live cases (with every-plan): 13/15 cases, 40/45 attempts, 7 said done too early, typed answers 7.2 s, 2 calls (docs/bench/acting.md)"),
        Option("contradicted", "Only when what happened contradicts it", "with completion unfinished: 14/15 cases, 44/45 attempts, none said done too early, typed answers 6.2 s, 1 call"),
    ]
}

# How the Gemini models sample (orchestrator/vision.py, every call through Agent.vision). Every call has been sent
# temperature 0.2 since 2026-10-03; Google's Gemini 3 developer guide says to keep the default (1.0), as below it
# their reasoning may loop or degrade, and its 3.5 notes say to send no temperature at all.
SAMPLING = {
    o.id: o
    for o in [
        Option("temperature-0.2", "Temperature 0.2", "every Gemini call since 2026-10-03; acting bench live cases 14/15 (with contradicted and unfinished)"),
        Option("provider-default", "The model's own default", "no temperature sent, as Google's Gemini 3 guide advises; acting bench live cases 11/15, so not the default"),
    ]
}

# When a plan the fast pass carried out goes back to a model before the answer (brain/loop.py `needs_completion`).
# A query's results and a failed step always do. "every-plan" (since 2026-10-09, 8b838aac: a plan that opened a
# picture but left out hiding its overlay) also asks after every plan that worked, so one it left unfinished
# can be finished; "unfinished" only after a failure or a query.
COMPLETION = {
    o.id: o
    for o in [
        Option("every-plan", "After every plan it carried out", "one more model call per request the fast pass acted on (since 2026-10-09)"),
        Option("unfinished", "Only after a failure or a query", "acting bench live cases: no multi-step case lost a step without the round; with answer_check contradicted 44/45"),
    ]
}

# How the studio draws outlines (apps/studio/src/view.ts, roadmap W3-4). LYNA (2018): outlines
# kept just outside the tissue edge, no fill, raised sensitivity 83% -> 91% and halved review
# time; "classic" is how it looked on 2026-10-04. Not yet compared blind here.
STYLE = {
    o.id: o
    for o in [
        Option("classic", "Filled on the edge", "a light fill and the line on the edge (as on 2026-10-04)"),
        Option("edge", "Line just outside (no fill)", "the thing's own edge stays visible (LYNA)"),
    ]
}

# How the brain and the fast pass are shown the tool menu (brain/menu.py; docs/design/
# 2026-10-06-capability-menu.md). Every tool is always shown; only the layout differs.
MENU = {
    o.id: o
    for o in [
        Option("flat", "Every tool in full", "as before the menu (2026-10-06)"),
        Option("families", "Grouped by family", "the same tools under a heading per family"),
        Option("compact", "Grouped (first sentences)", "each tool's arguments and first sentence; a smaller prompt"),
        Option("brief", "Every tool (the longest briefly)", "as flat, the seven longest in a few lines (brain/menu.py BRIEFS): tool choice 104/118 against flat's 110/118 (start_researcher 3/8), acting bench 12/15"),
    ]
}

# Checking the app's outlines after they are drawn, and outlining again when the model says one
# does not fit (orchestrator/outline_check.py; C2). Measured with bench/outline_check_bench.py.
OUTLINE_CHECK = {
    o.id: o
    for o in [
        Option("off", "No check", "outlines stand as drawn (as before)"),
        Option("check", "Check and log", "the fast model judges each outline the app made; logged, nothing changes"),
        Option("fix", "Check and fix", "an outline the model says does not fit is outlined again from its box, kept if that fits; "
               "bench (gemini-3.5-flash, 8 stoma shapes x 4): 30/32 verdicts right, no fitting outline called wrong, "
               "19/24 wrong ones recovered, 1.7 s a check (after it is drawn)"),
    ]
}

# Which model the outline check's redraw draws again with (orchestrator/outline_check.py, app.outline_again): the one
# that drew it, as before, or another, "try one, then another" (docs/design/2026-10-11-model-runtime.md). On the 19
# targets EdgeTAM missed on the outline-fit bench (docs/bench/outline-fit.md, from the look's box, judged by another
# family's model, a judge that did not stand against Will's labels), SAM 2.1 tiny fit 7, SAM 3.1's tracker 7, micro-sam
# 6; so the default stays the model that drew it.
# How every outline the app draws gets its name (tools.add_outline -> commands.name_drawn -> agent.identify): Will's
# decision of 2026-10-12, "via live voice, chat, or tapping, that system which identifies what it is, labels it, needs to
# be the same unified approach". Live that morning "highlight the ball in center" kept the running look's quick name
# ("round cell") while every tap was named by the naming model (close-up, calibration, what was measured).
NAMING = {
    o.id: o
    for o in [
        Option("one", "Named as a tap is", "every outline the app draws is named by the same step as a tapped one (the vision "
               "model with its close-up); the look's or the request's name shows only until it answers"),
        Option("per-path", "Named by what drew it", "a highlight keeps the running look's name and a find the request's "
               "words (as before 2026-10-12)"),
    ]
}

OUTLINE_REPAIR = {
    o.id: o
    for o in [
        Option("same", "The model that drew it", "the check's redraw uses the outline's own model (as before)"),
        Option("sam2.1-tiny", "SAM 2.1 tiny", "fit 7 of EdgeTAM's 19 misses on the outline-fit bench; ~20 ms", model="sam2.1-tiny"),
        Option("sam3.1-local", "SAM 3.1 (this Mac)", "fit 7 of EdgeTAM's 19 misses (its tracker); ~1.4 s a redraw, loads 5.3 GB", model="sam3.1-local"),
        Option("micro-sam-lm", "micro-sam (light microscopy)", "fit 6 of EdgeTAM's 19 misses; ~25 ms warm", model="micro-sam-lm"),
    ]
}

# What keeps an outline on its thing (orchestrator/objtrack.py). "slide": slide motion moves every
# outline (motion.py; tissue). "objects": EdgeTAM's video model also follows each of the newest
# outlines a few times a second on the heavy GPU worker, for things that move on their own.
# bench/object_tracking.py (20 s, seeds 7 and 8, the target saved as a click saves it): a swimming
# cell is on target 80-89% of frames with "objects" at 10 steps a second (wrong 0-9%; 62-73% at 5)
# against 0-3% with "slide" (wrong 89-99%); a fixed cell on a textured slide is on target 100%,
# p50 2.7-4.7 px, either way. Sim `follow-clicks`: click p95 104 ms while four outlines are followed.
TRACK = {
    o.id: o
    for o in [
        Option("objects", "With each thing", "swimming cell on target 80-89% (slide only: 0-3%); tissue 100% either way",
               uses=("edgetam",), also=("edgetam-video",)),
        Option("slide", "With the slide", "outlines move with the slide's motion only (as before 2026-10-06)"),
    ]
}

# How the voice's audio travels (orchestrator/voice/openai_live.py). "relay": browser -> our server ->
# OpenAI over WebSockets (as before). "webrtc": browser <-> OpenAI over WebRTC, OpenAI's recommended
# browser transport (its own echo cancelling, jitter buffer and interruptions), with our server on a
# trusted sideband for transcripts, hand-offs and context. Native media negotiated in ticket 64;
# direct WebRTC is the default. An explicitly saved relay choice remains available.
VOICELINK = {
    o.id: o
    for o in [
        Option("relay", "Through the app", "browser -> app -> OpenAI over WebSockets (as before)", uses=("openai-api",)),
        Option("webrtc", "Direct (WebRTC)", "browser <-> OpenAI over WebRTC; the app listens on a sideband", uses=("openai-api",)),
    ]
}

# How the app tells that the user asked for a big deliverable (a generated video, a lesson, deep research;
# orchestrator/research/policy.py). "quote" (default since 2026-10-06, facts not verdicts): the model judges and quotes
# the user's words, the app checks they were said. The keyword list that was its other option is gone (2026-10-29): a
# word list decided for the model (it refused "show me step by step how it works", and matched "animation" to a video).
ADMIT = {
    o.id: o
    for o in [
        Option("quote", "The model says and the app checks", "the model quotes the user's request; the quote must be in what was said"),
    ]
}

# Labels the user teaches (orchestrator/taught.py, roadmap W7): when the user names an outline, the
# app keeps its picture's DINOv2-small embedding; a new click or outline is compared with them, and
# naming a click is told the closest taught name as one line of data (the model decides the name;
# the studio shows "your label: ..." apart from it). bench/taught_bench.py (docs/bench/taught.md),
# 5 examples per kind taught: the closest taught name right 72% over 12 kinds of cell (blood 80%,
# Pap grades 55%); naming 80 untaught blood-cell clicks with Gemini 3.5 Flash, 52% right without
# the line and 69% with it (17 turned right, 4 turned wrong). Embedding: 13 ms on the heavy worker.
TAUGHT = {
    o.id: o
    for o in [
        Option("on", "Match the user's own labels", "naming bench: 52% -> 69% right with the line (17 helped, 4 hurt); 13 ms; "
               "the picture model is option EMBED"),
        Option("off", "Off", "names come from the model alone (as before W7); examples are still kept"),
    ]
}

# Lessons that need the picture (research_worker/lesson_check.py; W6-3, after RefineBot): a cheap
# model that sees no picture answers each quiz step, and the steps it gets right are rewritten
# once, with the photo, to need it. bench/lesson_check_bench.py, 2026-10-06, the three saved
# lessons with answers (9 steps; two real lessons and a fixed one on a real stoma photo): Gemini
# 3.8 Flash with no picture answered 26 of 27 steps right over three runs. "rewrite", two prompts:
# the first rewrote 8 steps and 6 of them were still answered right without the picture (two-way
# questions such as "taller or wider?" are guessed), and stomatal-mechanism questions became
# questions about a blurry patch's shape; the second (open questions that keep the idea, used now)
# kept two lessons unchanged (the photo showed too little) and rewrote 3 steps, 2 still answered.
# So "rewrite" does not beat the lesson as written, and the default only measures: "check".
LESSON_CHECK = {
    o.id: o
    for o in [
        Option("check", "Check and log", "bench: 26/27 quiz steps answered right without the picture; ~$0.001 and p50 4.6 s "
               "a lesson; logged, the lesson unchanged", uses=("gemini-api",)),
        Option("rewrite", "Check and rewrite", "bench: of 3-8 steps rewritten with the photo, 2/3-6/8 were still answered "
               "right without it; +10-25 s and ~$0.008 a lesson", uses=("gemini-api", "openai-api")),
        Option("off", "No check", "lessons are written as the researcher outlined them (as before)"),
    ]
}

# How a researcher's run is paced (research_worker/engines._final; the researcher repair, Phase 8): the app's rule
# makes it write up, or the model is told the facts and decides. docs/bench/research.md measures them (rubric frozen
# before any run); "told" becomes the default only if it regresses nothing on either engine and wins more pairs.
RESEARCH_PACING = {
    o.id: o
    for o in [
        Option("forced", "The app's rule", "made to finish after 3 rounds with nothing new (the rule since W6-4); told "
               "to write up at 75% of its time and past it, never cut (2026-10-11); measured against told in "
               "docs/bench/research.md"),
        Option("told", "Told the facts", "each round: time left, rounds left, rounds since it last found something; "
               "never made to finish by the stall rule; measured in docs/bench/research.md"),
    ]
}

# How a researcher's sourced points are checked against their pages (research_worker/claims.py; docs/bench/claims.md,
# 312 cases built from 47 pages the live researchers cited, truth known by construction). "words" stays the default:
# the model is far better on false "found" but worse on false "not found", and the rule frozen before the run takes no
# regression on either.
CLAIM_CHECK = {
    o.id: o
    for o in [
        Option("words", "Key words on the page", "bench: false found 70/118 (every negated point passed), false not "
               "found 0/194; no model call"),
        Option("model", "A model reads the page", "bench: false found 3/118, false not found 2/194, could not tell 5; "
               "word-for-word points need no call; ~1 s and a fraction of a cent a point", uses=("gemini-api",)),
    ]
}

# Where reference photos are searched for (orchestrator/reference.py; bench/reference_bench.py, 2026-10-11, 20
# subjects asked for live, each photo judged by GPT-6.1 Sol): the grounded web search alone found 20/20, 18 right, p50
# 10.7 s; Wikimedia Commons' catalogue found 11/20, all right, ~5 s; raced, 20/20 right, p50 6.2 s. Every candidate goes
# through the same pixel check, which sees the original request (a request for another site refuses Commons).
REFERENCE_SEARCH = {
    o.id: o
    for o in [
        Option("web+commons", "Web and Commons together", "bench: 20/20 right, p50 6.2 s (Commons first when it has one)",
               uses=("gemini-api", "wikimedia-commons")),
        Option("web", "Web search", "bench: 18/20 right, p50 10.7 s (as before 2026-10-11)", uses=("gemini-api",)),
    ]
}

# Seen one like this before today? (orchestrator/seen.py, roadmap W7): every outline named today is
# kept with its DINOv2-small embedding (shared with TAUGHT); naming a click is told the most alike
# one named earlier today, from a cosine of 0.93, as one line of data, and the seen_before tool
# lists them. bench/seen_bench.py (docs/bench/seen.md), 180 cells of 12 kinds in random order, each
# click against the earlier ones: from 0.93 the most alike earlier one was the same kind 92-94% of
# the time (recall 6-17%: the line is rare, and rarely about another kind); at 0.90, 60-76%.
# Different specimens never reached 0.91. Naming 32 such clicks with Gemini 3.5 Flash (2 runs): with
# the earlier one carrying the model's own name (right about half the time) the line was neutral
# (44% right without; 41% and 50% with: 7 helped, 6 hurt); had the user named the earlier one, 44% ->
# 69% (10 helped, 2 hurt). "off": naming is told nothing and seen_before compares whole views only.
SEEN = {
    o.id: o
    for o in [
        Option("on", "Seen before today", "from 0.93 the same kind 92-94% of the time (recall 6-17%); naming 44% -> 69% "
               "when the user named the earlier one, neutral when the model did; the picture model is option EMBED"),
        Option("off", "Off", "naming is not told earlier outlines; seen_before compares whole views only"),
    ]
}

# Which model turns an outline's picture into a vector for TAUGHT and SEEN (gpu_worker/embed.py, on the
# heavy GPU worker, never on a click's path; one vector per outline, shared by both). Vectors of two
# models are never compared: after a change, the day's taught examples and kept outlines are embedded
# again from their crops. Each model has its own thresholds and calibration (taught.CALIBRATIONS,
# seen.THRESHOLDS), because their similarities are on different scales. Measured 2026-10-06 on the
# naming bench's 180 cells of 12 kinds (docs/bench/seen.md, naming.md): DINOv2's nearest taught name right
# 72% (blood 80%) against BioCLIP 2's 68% (74%); naming 80 untaught blood cells with Gemini 3.5 Flash, two
# runs each, 55/53% -> 64/64% with DINOv2's line, 54/54% -> 66/63% with BioCLIP's (a tie); seen before
# today, the same kind 92-94% from 0.93 (recall 6-17%) against 91-92% from 0.96 (recall 3-15%); a crop on
# the heavy worker 13 ms against 72 ms (first call 9.6 s, 1.7 GB). BioCLIP 2's text tower naming the
# cells zero-shot was at chance (blood 11-15% of 8 names, Pap 27-32% of 4), so it gives naming no fact.
# DINOv2 stays the default.
EMBED = {
    o.id: o
    for o in [
        Option("dinov2-small", "DINOv2 small", "your labels: nearest name 72% (blood 80%); seen: from 0.93 the same kind 92-94%, "
               "recall 6-17%; 13 ms a crop", model="dinov2-small"),
        Option("bioclip-2", "BioCLIP 2", "your labels: nearest name 68% (blood 74%), naming with it a tie; seen: from 0.96 the "
               "same kind 91-92%, recall 3-15%; 72 ms a crop, first use loads 1.7 GB", model="bioclip-2"),
        Option("dinov3-small", "DINOv3 small", "not yet measured on our cells (M3: an implementation, judged on the taught and "
               "seen benches and the replay bench); until then seen-before matches nothing and your labels say it is "
               "uncalibrated; gated weights (accept Meta's licence on Hugging Face, HF_TOKEN)",
               # HF_TOKEN gets its weights (its folder: a need to get); a downloaded copy runs without it, and readiness
               # says the Download (and the key it takes) while they aren't here (make catalog-proof, 2026-10-13).
               model="dinov3-small"),
    ]
}

# Whose sweep the focus meter is following (orchestrator/coach.py). The iPhone's own autofocus makes
# the same sharpness swings as the knob when it hunts, and over Continuity Camera the phone tells the
# Mac nothing about its lens. "lens" (default): a sweep during which the eyepiece disc changed size
# (fov.edge_radius; the knob never changes it, the phone's lens does) is the phone's, and the meter
# says so instead of "turn back a little". Measured by running the coach over the real recordings
# (2026-10-06 demo, 38 min): 76 of 192 sweeps changed the disc by more than 0.3% (37 of 89 on a
# textured view), the knob's own turns by at most 0.05%; 5 of its 36 "past the sharpest" cues came
# during the phone's hunts. On the 2026-06-14 recording all 6 sweeps changed the disc (0.3-2.3%; the
# phone was hand-held), both "past the sharpest" cues among them. "off": every sweep is the knob's.
COACH = {
    o.id: o
    for o in [
        Option("lens", "Tell the phone from the knob", "a sweep that changed the eyepiece disc's size is the phone refocusing; "
                                                      "demo: 76/192 sweeps, the knob's own turns moved it at most 0.05%"),
        Option("off", "Every sweep is the knob", "the meter says 'turn back a little' whoever changed the focus (as before)"),
    ]
}

# Close-ups when naming a click (orchestrator/agent.py, roadmap W4-3). "single": the view with the
# thing outlined and one close-up of it (3x its size, small ones enlarged to 384 px). "naming": the
# model may also ask for part of the newest close-up closer, cut from the full-resolution frame and
# enlarged, at most 3 calls in all (agent.name_closer). "naming+answers": answers to questions may
# ask for a closer look too (agent.answer_closer; not benched on its own). Naming bench, 2026-10-06,
# Gemini 3.5 Flash, 180 pictures at x1, x0.25 and x0.15 (things down to ~20 px): it never asked for a
# closer look in 720 namings, so the arms differ only by noise and "single" stays the default.
CLOSEUP = {
    o.id: o
    for o in [
        Option("single", "One close-up", "the view plus one close-up of the thing (as before W4-3)"),
        Option("naming", "Closer looks when naming", "naming a click may ask for up to 2 closer crops of the full-resolution frame; "
               "naming bench: never asked in 720 namings, accuracy within noise"),
        Option("naming+answers", "Closer looks when naming and answering",
               "answers may ask for a closer crop too; each one costs a vision call"),
    ]
}

# Dust on the optics (orchestrator/dust.py, roadmap W4-3): the median of eyepiece-aligned views taken
# at different places on the slide is the flat field; dark spots in its fixed residue that most views
# show are dust. "tell": Sight, naming and answers are told where they are, as measured data, and the
# model decides. "filter": also, code drops the app's outlines that lie on a spot (a rule: it has to
# beat "tell"). "off": nothing is told. bench/dust_bench.py (docs/bench/dust.md): 28 of 32 synthetic
# spots 12-25% deep found, none falsely, none on the real June 14 footage; Gemini 3.5 Flash naming a
# click on dust called it part of the specimen 29/48 times told nothing ("Howell-Jolly body"), 0/48
# told; Sight boxed dust 2/93 vs 0/95; cells away from it unchanged; the filter added nothing.
DUST = {
    o.id: o
    for o in [
        Option("tell", "Tell the models", "where spots stay put while the slide moves, as data; dust clicks named as specimen "
               "29/48 -> 0/48"),
        Option("filter", "Tell and drop outlines on dust", "code leaves out the app's outlines that lie on a spot (logged); "
               "bench: no better than told"),
        Option("off", "Off", "nothing is said about dust (as before W4-3)"),
    ]
}

# MedSigLIP's zero-shot ranking when naming a click (orchestrator/zeroshot.py, gpu_worker/zeroshot.py;
# roadmap W7). MedSigLIP (Google, Health AI Developer Foundations terms) ranks a candidate list of names
# for the crop and naming is told the top three with their shares and what its first name has been
# worth, as one line of data; the model decides the name. "medsiglip-subject": only the list of the
# session's subject (blood smear, Pap smear), nothing for other specimens; "medsiglip-always": every list
# at once on every click. bench/zeroshot_bench.py (docs/bench/naming.md, "W7: MedSigLIP"), 2026-10-06, the
# naming bench's 180 cells, Gemini 3.5 Flash told the right specimen, 2 repeats: MedSigLIP alone named
# 32% of 8 kinds of blood cell right (chance 11%; never eosinophils, lymphocytes, monocytes, platelets)
# and called 90% of Pap smear cells "parabasal cell". Exact blood names 58% without the line, 59% with
# the subject's line, 61% with every list: within noise, but the line moved names both ways (17 turned
# right, 13 wrong; repeats alone flipped 6 of 115): monocytes 63 -> 90%, neutrophils 80 -> 53%. Coarse
# names rose (blood 91 -> 97%, Pap 51% unchanged; every list: 95% and 56%). A plain list of the names
# without the ranking did no better (exact 58%, Pap coarse 53 -> 43%). Costs ~0.3 s a click on the heavy
# worker, 1.8 GB and ~30 s to load. Not better on exact names, and it hurts some: "off" by default.
ZEROSHOT = {
    o.id: o
    for o in [
        Option("off", "Off", "naming is told nothing by MedSigLIP (as before W7); bench: exact blood names 58%"),
        Option("medsiglip-subject", "MedSigLIP for blood and Pap smears", "exact blood names 58% -> 59% (17 helped, 13 hurt; neutrophils 80% -> 53%), coarse 91% -> 97%; "
               "Pap unchanged; nothing for other specimens; ~0.3 s a click", model="medsiglip"),
        Option("medsiglip-always", "MedSigLIP on every click", "exact blood names 58% -> 61% (13 helped, 8 hurt), coarse 91% -> 95%; Pap coarse 51% -> 56%; "
               "not measured on other specimens; ~0.3 s every click", model="medsiglip"),
    ]
}

# Session knowledge K3 (docs/design/2026-10-07-session-knowledge.md, "Context earns its place"): what is pushed
# into the brain's prompt beyond what every request is told today. Both off: everything is pulled by tools (the
# `outcomes` and `resources` query tools). One becomes the default only when it measures no worse on the benches
# (brain_prompt, tool_choice, fast_bench, acting, naming) and its own bench improves; the fast paths never carry them.
OUTCOME_LINES = {
    o.id: o
    for o in [
        Option("off", "Off", "outcomes are looked up with the outcomes tool when the brain chooses (not measured on)"),
        Option("on", "When choosing", "the brain's prompt gets how each model has done on this specimen (past tense, n, too few "
               "runs said); not yet measured on a route-choice bench"),
    ]
}
SESSION_CONTEXT = {
    o.id: o
    for o in [
        Option("off", "Off", "the session's knowledge is pulled with resources and recall (as before K3)"),
        Option("on", "Referenced and capped", "the brain's prompt gets the index entries this request and the conversation refer to "
               "(at most 6 lines, 600 characters); not yet measured on the refer-back bench"),
    ]
}
# Which models made each outline (known.chain: found by, drawn by, checked by, named by, followed by) in the rows of
# the brain's own prompt (brain/context.py). Live 2026-10-11: asked "which model did you use?", the brain invented "our
# built-in vision model": nothing had told it. Off by default (context earns its place, docs/design/2026-10-07-
# session-knowledge.md): the same account is always one step away, resources(id=<outline>); on only if the brain,
# tool-choice, acting and naming benches show the longer rows lose nothing. The voice's rows always carry it; the fast
# pass's never.
ROW_PROVENANCE = {
    o.id: o
    for o in [
        Option("off", "Pulled when asked", "which models made an outline is read with resources(id=...) when asked"),
        Option("on", "In every row", "each outline's row in the brain's prompt names the models that found, drew, checked, "
               "named and followed it; not yet measured on the benches"),
    ]
}

# How often feature matches vote on the slide's move (orchestrator/motion.py MotionTracker._consensus: ORB features of
# the frame against the keyframe's, outvoting things that swim over a still slide). ORB is most of the frame path's
# time (2026-10-11: 2.6 of 8.9 ms a frame on one thread); voting less often leaves more for the live view under load.
# A frame phase correlation could not place is always voted on. Measured by `make tracking-bench` (outlines in the
# wrong place, the headline) and sim model-load (docs/bench/priority.md, "Voting less often"). The default stays 1: at 2
# and 3 the swimmers seed 7 tour lost (shown 96 -> 94%, wrong 0.4 -> 0.7%); every other condition was the same.
MOTION_ORB_EVERY = {
    o.id: o
    for o in [
        Option("1", "Every frame", "the slide's move is voted on every frame; tracking bench analysis 8.1-9.7 ms a frame, "
                                  "the fewest outlines in the wrong place (swimmers 0.0-0.4%)"),
        Option("2", "Every other frame", "half the ORB work, between votes phase correlation stands; analysis 6.9-7.9 ms, "
                                       "swimmers wrong 0.0-0.7% (worse on one tour), the rest the same"),
        Option("3", "Every third frame", "a third of the ORB work, between votes phase correlation stands; analysis 4.7-5.7 ms, "
                                       "30 fps under 1 busy process per core (every frame 25.5), swimmers wrong 0.0-0.7%"),
    ]
}

# The library's looks (library_looks.py): whether each new asset's small picture is sent to the fast vision model so it
# is filed by what it shows. Off by default (Will, 2026-10-13: what is best for open source): an app people download
# sends none of their pictures anywhere until they choose to; with it off, assets are filed by what the user names them.
LIBRARY_LOOKS = {
    o.id: o
    for o in [
        Option("off", "Off", "nothing is sent: each asset is filed by what you name it"),
        Option("on", "On", "each new asset's small picture goes to your fast vision model, up to 12 a minute, to file it"),
    ]
}

GROUPS = {"vision": VISION, "pointing": POINTING, "outline": OUTLINE, "voice": VOICE, "brain": BRAIN, "dissect": DISSECT,
          "research": RESEARCH_ENGINE, "presentation": PRESENTATION, "image": IMAGE_MODEL, "sight": SIGHT, "fast": BRAIN_FAST, "cells": CELLS, "quality": QUALITY,
          "speculate": SPECULATE, "reflex": REFLEX, "early_look_typed": EARLY_LOOK_TYPED, "answer_check": ANSWER_CHECK, "completion": COMPLETION, "sampling": SAMPLING, "style": STYLE, "menu": MENU, "outline_check": OUTLINE_CHECK, "track": TRACK,
          "voicelink": VOICELINK, "talk": TALK, "talk_key": TALK_KEY, "admit": ADMIT, "taught": TAUGHT, "lesson_check": LESSON_CHECK, "research_pacing": RESEARCH_PACING, "claim_check": CLAIM_CHECK, "reference_search": REFERENCE_SEARCH, "outline_repair": OUTLINE_REPAIR, "naming": NAMING, "seen": SEEN, "coach": COACH,
          "closeup": CLOSEUP, "dust": DUST, "embed": EMBED, "zeroshot": ZEROSHOT,
          "outcome_lines": OUTCOME_LINES, "session_context": SESSION_CONTEXT, "row_provenance": ROW_PROVENANCE,
          "orb_every": MOTION_ORB_EVERY, "library_looks": LIBRARY_LOOKS}

# A group's choices change while the app runs (your models turned on or off: mine.register; contributed entries proven
# or withdrawn: kinds.register), on worker threads, while the loop reads them (catalogue, resolve, the shelf). So a
# published group is never changed in place: its writer builds a new dict and swaps it in (change_choices), and a reader
# iterating one keeps a stable object. The module name a group was defined under follows the swap (CELLS is
# GROUPS["cells"]). CI 2026-10-21: catalogue's `options.values()` raised "dictionary changed size during iteration".
_CHOICES_WRITE = threading.Lock()  # writers only: two at once would each copy the old group and lose the other's change
_DEFINED_AS = {group: name for name, value in list(globals().items()) if name.isupper()
               for group, options in GROUPS.items() if value is options}


def change_choices(group: str, put: Mapping[str, Option] | None = None, drop: Iterable[str] = ()) -> bool:
    """Add or replace (`put`) and take away (`drop`) choices of `group`, copy-on-write: a reader iterating the group it
    read keeps that whole object. Returns whether anything changed."""
    with _CHOICES_WRITE:
        old = GROUPS[group]
        gone = set(drop)
        new = {k: v for k, v in old.items() if k not in gone}
        new.update(put or {})
        if new == old:
            return False
        GROUPS[group] = new  # an existing key's value: GROUPS itself never changes size
        name = _DEFINED_AS.get(group)
        if name is not None and globals().get(name) is old:
            globals()[name] = new
        return True


def resolve(group: str, wanted: str, fallback: str | None = None) -> tuple[str, str]:
    """The option of `group` to use for `wanted` in this edition: `wanted` itself when it is
    permitted (reason ""), else `fallback` (the group's default) when that is, else the first
    permitted option in the group's order, with the reason in words."""
    options = GROUPS[group]
    if wanted in options and options[wanted].permitted:
        return wanted, ""
    reason = licences.why_not(options[wanted].uses) if wanted in options else f"unknown {group} option {wanted!r}"
    for candidate in [fallback, *options]:
        if candidate in options and options[candidate].permitted:
            return candidate, reason
    raise LookupError(f"no {group} option is permitted in the {licences.edition()} edition")


# Which option groups the work in progress has read (Settings.__getattribute__): a tool's run is
# recorded (brain/registry.py), so its interaction log line says which settings it really used,
# and the acting bench checks the capability list names them (tests/test_capabilities.py).
_reads: ContextVar[set | None] = ContextVar("settings_reads", default=None)


@contextmanager
def recording_reads():
    """Collect the option groups read inside this block, in its tasks and the tasks they start."""
    seen: set[str] = set()
    token = _reads.set(seen)
    try:
        yield seen
    finally:
        _reads.reset(token)


@contextmanager
def unrecorded_reads():
    """Reads inside this block, and in the tasks it starts, are not the work in progress's: a watcher that
    runs beside a tool (the outline check, its own capability) reads for itself. The request's own choices
    still hold (contract 7)."""
    token = _reads.set(None)
    try:
        yield
    finally:
        _reads.reset(token)


@contextmanager
def _not_recorded():
    token, chosen = _reads.set(None), _overriding.set(None)
    try:
        yield
    finally:
        _reads.reset(token)
        _overriding.reset(chosen)


# A request's own choices (contract 7, `use`): option group -> option, seen by every settings read made for
# that request, in its tasks and the tasks they start (the same mechanism as recording_reads). The ticket
# holds the dict; ticket.using sets it. Work started inside a request that is not *for* it (seen-before
# embeddings, descriptions) resets only this variable (`unrelated`).
_overriding: ContextVar[dict | None] = ContextVar("overriding", default=None)


def overriding() -> dict | None:
    return _overriding.get()


@contextmanager
def choices_of(overrides: dict | None):
    token = _overriding.set(overrides)
    try:
        yield overrides
    finally:
        _overriding.reset(token)


def unrelated(coro):
    """Start `coro` as a task that is not for the request around it: it sees none of the request's
    choices (contract 7), whatever else of its context it keeps (tracing, the ticket's holds)."""
    import asyncio

    with choices_of(None):
        return asyncio.ensure_future(coro)


@dataclass
class Settings:
    def __getattribute__(self, name: str):
        if name in GROUPS:
            seen = _reads.get()
            if seen is not None:
                seen.add(name)
            chosen = _overriding.get()
            if chosen and name in chosen:
                return chosen[name]  # this request's own choice (contract 7)
        return object.__getattribute__(self, name)

    vision: str = "gemini-3.8-flash"  # camera vision; the fast notes/action path stays separate
    pointing: str = "box"
    outline: str = "edgetam"
    voice: str = "gpt-live-1"
    brain: str = "json-actions"
    dissect: str = "grid"
    research: str = "gpt-6.1-sol"
    presentation: str = 'auto'  # qualified retained-input producer; Standard layout only remains an explicit preference
    sight: str = "gemini-3.8-flash"
    fast: str = "gemini-3.8-flash"  # correctness comparison and prior timing evidence: BRAIN_FAST
    image: str = "gpt-image-2.5-flare"
    cells: str = "cellpose-sam"  # EdgeTAM automatic masks in the explicit commercial edition (__post_init__)
    quality: str = "tell"  # how a poor view is handled (QUALITY)
    speculate: str = "needs-look"  # start the look before the voice hands over (SPECULATE)
    reflex: str = "lean-3.5-flash"  # how a spoken question's thing is pointed at (REFLEX; point bench and voice journeys, 2026-10-11)
    early_look_typed: str = "sequential"  # when a typed request's look starts (EARLY_LOOK_TYPED)
    answer_check: str = "every-request"  # when the answer is written again from what its tools did (ANSWER_CHECK)
    completion: str = "every-plan"  # when a carried-out plan goes back to a model (COMPLETION)
    sampling: str = "temperature-0.2"  # how the Gemini models sample (SAMPLING)
    style: str = "classic"  # how the studio draws outlines (STYLE)
    outline_color: str = "pink"  # the colour the studio draws the app's outlines in (OUTLINE_COLORS)
    menu: str = "flat"  # how the tool menu is shown to the model (MENU)
    outline_check: str = "fix"  # checking the app's outlines after they are drawn (OUTLINE_CHECK; measured)
    track: str = "objects"  # what keeps an outline on its thing (TRACK)
    voicelink: str = "webrtc"  # native browser media; explicit relay preferences remain supported (VOICELINK)
    talk: str = "hold"  # how the one-brain voice knows a turn (TALK)
    talk_key: str = "control-option"  # the key held to talk with it (TALK_KEY)
    admit: str = "quote"  # how a request for a big deliverable is told apart (ADMIT)
    taught: str = "on"  # matching the labels the user teaches (TAUGHT; measured)
    lesson_check: str = "check"  # are lessons' steps answerable without the picture? (LESSON_CHECK; measured)
    research_pacing: str = "forced"  # what makes a researcher write up (RESEARCH_PACING; docs/bench/research.md)
    # How sourced points are checked on their pages (CLAIM_CHECK; docs/bench/claims.md). "model" since 2026-10-11, the
    # user's call on the bench's trade: "words" passed every negated point as found (57/57).
    claim_check: str = "model"
    reference_search: str = "web+commons"  # where reference photos are searched for (REFERENCE_SEARCH)
    outline_repair: str = "same"  # what the outline check's redraw draws with (OUTLINE_REPAIR)
    naming: str = "one"  # how every outline the app draws is named (NAMING)
    seen: str = "on"  # telling naming the outline named earlier today most like this one (SEEN; measured)
    coach: str = "lens"  # the focus meter tells the phone refocusing from the knob (COACH; measured)
    closeup: str = "single"  # close-ups when naming a click (CLOSEUP; measured)
    dust: str = "tell"  # telling the models where dust on the optics is (DUST; measured)
    embed: str = "dinov2-small"  # the picture model of TAUGHT and SEEN (EMBED; measured)
    zeroshot: str = "off"  # MedSigLIP's zero-shot ranking told to naming a click (ZEROSHOT; measured)
    outcome_lines: str = "off"  # outcomes pushed into the brain's prompt (OUTCOME_LINES, K3; off until measured)
    session_context: str = "off"  # a capped section of referenced index entries (SESSION_CONTEXT, K3; off until measured)
    row_provenance: str = "off"  # which models made each outline, in the brain's rows (ROW_PROVENANCE; off until measured)
    orb_every: str = "1"  # how often feature matches vote on the slide's move (MOTION_ORB_EVERY; measured)
    library_looks: str = "off"  # whether new assets' pictures are sent to be filed by look (LIBRARY_LOOKS; off by default)
    # USD a day for all researchers, or None: unlimited (the default; ADR 0004). Set in the
    # studio, by voice or chat (set_research_cap), or MICROMIND_RESEARCH_CAP; kept until changed.
    research_cap: float | None = field(default_factory=lambda: _cap_from_env())
    quieter: bool = False  # only results the user asked for are spoken (brain/narrator.py)
    # The user leads: the app points, highlights and names what to look for only when asked
    # in so many words (for learning to observe; live session 2026-10-04, 15:07). Off by default.
    user_leads: bool = False
    # How to explain (learner.py): "simple" or "technical", and hints before answers. Day
    # preferences, set in the studio or by voice or chat (set_level); told to the models as one line.
    level: str = "simple"
    hints_first: bool = False
    compare: str = ""  # a second vision model answering the same question, or "" for none
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A default this edition may not use becomes the first one it may (licences.py). Making settings is not
        # reading them for the work in progress: never recorded as its reads, never given its request's choices.
        with _not_recorded():
            for group in GROUPS:
                setattr(self, group, resolve(group, getattr(self, group))[0])

    def update(self, changes: dict) -> list[str]:
        """Apply valid changes; returns problems for any rejected ones."""
        problems = []
        for key, value in changes.items():
            if key in GROUPS:
                if value not in GROUPS[key]:
                    problems.append(f"unknown {key} option {value!r}")
                    continue
                if not GROUPS[key][value].permitted:
                    problems.append(f"{GROUPS[key][value].label} is {licences.why_not(GROUPS[key][value].uses)}")
                    continue
                why = unmade(GROUPS[key][value]) or not_installed(key, value)
                if why:
                    problems.append(f"{GROUPS[key][value].label} can't be used: {why}")
                    continue
                setattr(self, key, value)
            elif key == "compare":
                if value and value not in VISION:
                    problems.append(f"unknown vision model {value!r} to compare with")
                    continue
                if value and not VISION[value].permitted:
                    problems.append(f"{VISION[value].label} is {licences.why_not(VISION[value].uses)}")
                    continue
                self.compare = value or ""
            elif key == "outlineColor":
                if value not in OUTLINE_COLORS:
                    problems.append(f"unknown outline colour {value!r}")
                    continue
                self.outline_color = value
            elif key == "quieter":
                self.quieter = bool(value)
            elif key == "userLeads":
                self.user_leads = bool(value)
            elif key == "level":
                if value not in LEVELS:
                    problems.append(f"the level is {' or '.join(LEVELS)}, not {value!r}")
                    continue
                self.level = value
            elif key == "hintsFirst":
                self.hints_first = bool(value)
            elif key == "researchCap":
                try:
                    self.research_cap = parse_cap(value)
                except ValueError:
                    problems.append(f"the research cap is an amount in dollars or none, not {value!r}")
        return problems

    def to_wire(self) -> dict:
        with _not_recorded():  # every setting, for the studio: not what the work in progress used
            return {"vision": self.vision, "pointing": self.pointing, "outline": self.outline, "voice": self.voice,
                "brain": self.brain, "dissect": self.dissect, "research": self.research, "presentation": self.presentation, "image": self.image, "sight": self.sight, "fast": self.fast, "cells": self.cells, "quality": self.quality, "speculate": self.speculate, "reflex": self.reflex, "early_look_typed": self.early_look_typed, "answer_check": self.answer_check, "completion": self.completion, "sampling": self.sampling, "style": self.style, "track": self.track, "voicelink": self.voicelink, "talk": self.talk, "talk_key": self.talk_key, "admit": self.admit, "taught": self.taught, "lesson_check": self.lesson_check, "research_pacing": self.research_pacing, "claim_check": self.claim_check, "reference_search": self.reference_search, "outline_repair": self.outline_repair, "naming": self.naming, "seen": self.seen, "coach": self.coach, "closeup": self.closeup, "dust": self.dust, "embed": self.embed, "zeroshot": self.zeroshot, "outcome_lines": self.outcome_lines, "session_context": self.session_context, "row_provenance": self.row_provenance, "orb_every": self.orb_every, "library_looks": self.library_looks,
                "outlineColor": self.outline_color, "menu": self.menu, "outline_check": self.outline_check, "researchCap": self.research_cap, "quieter": self.quieter, "userLeads": self.user_leads,
                "level": self.level, "hintsFirst": self.hints_first, "compare": self.compare}


# Colours the user can ask for ("make the outlines easier to see"; asked live 2026-10-06 and
# refused for want of this). The studio maps each to its own shade (apps/studio/src/view.ts).
OUTLINE_COLORS = ("pink", "yellow", "cyan", "green", "white")


def unmade(option: Option) -> str:
    """Why an option that needs fine-tuned weights cannot run here ("" when it needs none, or they exist)."""
    if not option.weights:
        return ""
    from . import finetuned

    return finetuned.missing(option.weights)


def unavailable(group: str, option_id: str, environ=None) -> str:
    """Why an option can't run here now ("" when it can): this edition, its key, fine-tuned weights not made here,
    its code not installed here (the same reasons the Models menu shows)."""
    from . import readiness

    return readiness.option(group, option_id, environ=os.environ if environ is None else environ).why


def not_installed(group: str, option_id: str) -> str:
    """What is missing here for an option that selects an implementation (operations.INSTALLED), with how to install it
    ("" when nothing is): available means it can run (live 2026-10-11)."""
    from .operations import option_not_installed

    return option_not_installed(group, option_id)


def parse_cap(value) -> float | None:
    """A daily research cap: dollars (0-1000), or None for unlimited ("", "none", "unlimited",
    "off", "no limit"). Raises ValueError for anything else."""
    if value is None or str(value).strip().lower().lstrip("$") in ("", "none", "unlimited", "off", "no limit", "no cap"):
        return None
    amount = float(str(value).strip().lstrip("$"))
    if amount != amount or amount < 0:
        raise ValueError(value)
    return min(amount, 1000.0)


def catalogue(available_keys: dict[str, bool], *, choices: dict | None = None) -> dict:
    """Everything the studio can offer, with whether each option is usable right now and, when
    it is not, why (a missing key, or an option this edition may not use).
    Counts may request just the current choices; the same readiness resolver applies."""

    from . import readiness

    def entry(group: str, o: Option) -> dict:
        ready = readiness.option(group, o.id, keys=available_keys, given=o)  # one resolver (ADR 0019): `why` as before
        return {**{k: v for k, v in asdict(o).items() if k != "model"}, "tier": o.tier, "licence": licences.licence_text(o.uses),
                "available": ready.available, "why": ready.why, "readiness": ready.wire()}

    with readiness.batch():  # each group as it is now (change_choices swaps, never edits, the one being read)
        return {group: [entry(group, o) for o in options.values()
                        if choices is None or choices.get(group) == o.id]
                for group, options in list(GROUPS.items())}
