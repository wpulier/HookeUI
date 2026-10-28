"""Researcher efforts, list prices and budgets.

Every provider call a researcher makes is priced here (list prices, October 2026; sources in
docs/bench/spike-s5-research.md) and checked against its effort's cap when a spending limit is enabled.
Time limits are enforced with asyncio.timeout in the worker and a watchdog in the
orchestrator (+30 s).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# USD per million tokens (input, output), per call, per image, per second of video.
PRICES = {
    "gpt-6.1-sol": {"in": 2.00, "out": 10.00},
    "gemini-3.5-flash": {"in": 1.50, "out": 9.00},
    "gemini-3.5-flash-lite": {"in": 0.30, "out": 2.50},
    "gpt-6-luna": {"in": 0.10, "out": 0.50},
    "gemini-3.8-flash": {"in": 0.75, "out": 3.75},
    "gpt-image-2.5-flare": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    "gpt-image-2.5-sunburst": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    # Not from a price list: assumed equal to gpt-image-2.5's until its list price is checked (the S4
    # spike's pictures used about 110 text, 260 image and 1120 picture-out tokens: ~$0.04 each).
    "gemini-3.1-flash-image": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    "web_search": 0.010,      # OpenAI, per call (search content tokens are billed as input)
    "google_search": 0.014,   # Gemini, per query beyond the monthly free tier (counted as paid)
    "veo-3.1-fast": 0.10,     # per second at 720p
    "claude-sonnet-5-5": {"in": 3.00, "out": 15.00},  # lessons, when ANTHROPIC_API_KEY is set
    "claude-opus-5-5": {"in": 4.00, "out": 20.00},  # presentations (research_worker/presentation.py)
}


_UNPRICED: set[str] = set()  # models with no list price here, already said in the log


def _price(model: str, instead: str) -> dict:
    """A model's list price. One with none here is priced as `instead`, so its spending is a guess: said once in
    the log per model, never silently."""
    price = PRICES.get(model)
    if price is None:
        if model not in _UNPRICED:
            _UNPRICED.add(model)
            log.warning("no list price for %s in budget.PRICES: priced as %s, so its spending is a guess", model, instead)
        price = PRICES[instead]
    return price


def tokens_cost(model: str, input_tokens: int = 0, output_tokens: int = 0) -> float:
    price = _price(model, "gpt-6.1-sol")
    return (input_tokens * price["in"] + output_tokens * price["out"]) / 1e6


def image_cost(model: str, text_in: int = 0, image_in: int = 0, image_out: int = 0) -> float:
    price = _price(model, "gpt-image-2.5-flare")
    return (text_in * price["text_in"] + image_in * price["image_in"] + image_out * price["image_out"]) / 1e6


@dataclass(frozen=True)
class Effort:
    name: str
    seconds: float
    cap_usd: float
    pool: str                  # "instant" or "big"
    tools: frozenset[str]
    max_rounds: int
    reasoning: str             # reasoning effort for gpt-6.1-sol
    max_images: int = 0        # annotate + generate


BASE_TOOLS = frozenset({"web_search", "snapshot", "session", "request_evidence", "fetch_reference_image", "record_finding",
                        "propose_correction", "finish"})
EFFORTS = {
    # Spike S5: one lookup with search results is $0.04-0.05 and 9-12 s, so instant has
    # $0.08 and no image tools (an annotated image takes ~11 s, spike S4).
    "instant": Effort("instant", 15, 0.08, "instant", BASE_TOOLS, 3, "low"),
    # Descriptions under labels (orchestrator/enrich.py): the first live session (2026-10-04)
    # saw finished ones take 8-14 s and 7 of 39 time out at 15 s, so they get 25 s at the same
    # cap and tools, in the instant pool.
    "describe": Effort("describe", 25, 0.08, "instant", BASE_TOOLS, 3, "low"),
    # The app's own operations (run, orchestrator/research/hands.py) only from quick up: they take seconds of the
    # researcher's time, never money, and an instant lookup or a description has none to spare.
    "quick": Effort("quick", 75, 0.25, "big", BASE_TOOLS | {"annotate_image", "generate_image", "make_lesson", "run"}, 8, "low",
                    max_images=3),
    "standard": Effort("standard", 360, 1.00, "big",
                       BASE_TOOLS | {"annotate_image", "generate_image", "make_video", "make_lesson", "run"}, 16, "medium",
                       max_images=5),
    "deep": Effort("deep", 1080, 5.00, "big", BASE_TOOLS | {"annotate_image", "generate_image", "make_video", "make_lesson", "run"},
                   32, "high", max_images=8),
    # A new version of one thing a researcher made (ResearchManager.followup mode "revise"): a
    # lesson is one edit call plus the page written again (the page alone is checked at $0.10,
    # as in make_lesson), labels at most one edit call and one annotation (retried once if the
    # photo was redrawn), an illustration one image edit. Never chosen for a new researcher.
    # A video revision runs at standard effort instead.
    "revise": Effort("revise", 150, 0.20, "big", frozenset({"annotate_image", "finish"}), 2, "low", max_images=2),
    # The builder (C4, orchestrator/drafts.py): an adapter drafted for a link no template runs, read from its pages and
    # tried by the app inside macOS's sandbox until it runs. Started only from the studio's own press, never chosen.
    "build": Effort("build", 900, 2.00, "big",
                    frozenset({"web_search", "read_source", "draft", "pin_file", "try_draft", "finish"}), 30, "medium"),
}
REVISE_ONLY = "revise"  # an effort that never starts a researcher of its own
# The app's own operations a researcher can run for its reading (run; orchestrator/research/hands.py), here
# so the research process and the app read them without importing each other.
# What each does, said with the view it runs on ("the picture on the Stage", or a kept sample's id).
RUN_OPERATIONS = {"segment": "segment every cell in {view} and measure them",
                  "enhance": "make a clearer picture of {view} from real frames",
                  "measure_saved_objects": "measure the explicitly selected saved labels",
                  "motion": "measure what moves on the Stage over its last seconds"}
# The cell finders a researcher can name for segment (id = name), as the app names them (orchestrator/names.py; a test
# keeps the two the same): the research process does not import the app (Slice 5, the user's Q24).
CELL_FINDERS = {"cellpose-sam": "Cellpose-SAM", "cellpose-sam-tuned": "Cellpose-SAM fine-tuned on your corrections",
                "micro-sam-lm": "micro-sam (light microscopy)", "edgetam-automask": "EdgeTAM automatic masks",
                "fiji-analyze-particles": "Analyze Particles (Fiji)",
                "basic-bright-objects": "Bright objects"}


def operation_limit(effort: str | None) -> int:
    """Operations within the selected effort's existing finite round bound.

    Operations use the app's research allowance; this does not grant additional model rounds, money or
    scheduled mission slots.
    """
    selected = EFFORTS.get(effort) if isinstance(effort, str) else None
    return selected.max_rounds if selected is not None and "run" in selected.tools else 0


RUN_WAIT_MAX_S = 185.0  # the cell finder's own limit (180 s) and a little: longer than that, it is not coming
WRAP_UP_AT = 0.75  # of the effort's time: it is told its time is nearly up (never made to stop by it)
# The clock never kills work (the researcher repair, 2026-10-11): an effort's seconds and rounds are where a researcher is
# told to write up, never a cut-off. What makes it write up is the stall rule (below), a daily cap the user set, or the
# user's answer to "keep going?", asked after CHECK_IN_S of running and when its spend passes CHECK_IN_COST_X times its
# effort's usual cost (no daily cap set); no answer in CHECK_IN_WAIT_S makes it write up (Researcher.keep_going).
CHECK_IN_S = 3600.0
CHECK_IN_COST_X = 3.0
CHECK_IN_WAIT_S = 900.0
# A working researcher says it is alive this often, even while one model call or a wait runs long; the app stops only
# one it has not heard from in WATCHDOG_SILENT_S (a process stuck or gone), never one that is merely slow.
ALIVE_EVERY_S = 30.0
WATCHDOG_SILENT_S = 300.0
# One model call's own limit: one that has not answered in this long is cut and sent again (engines._model_call).
CALL_TIMEOUT_S = 600.0
# A researcher whose last STALL_ROUNDS rounds brought no new page, finding, reference picture or
# output is told to write up what it has (W6-4b; logged as a "stall" step). A round is one model
# call (a search round brings back pages); three empty ones in a row is going in circles.
STALL_ROUNDS = 3

# A call cancelled (the user's stop) is billed but reports no usage. Its cost is estimated
# from what was sent: ~4 characters a token, IMAGE_TOKENS per attached picture, OUTPUT_TOKENS
# of reasoning and text, and one search when the search tool was offered (slow calls are the
# ones that search). Kept apart as spentEstimated and counted against the daily cap.
IMAGE_TOKENS = 1100
OUTPUT_TOKENS = 400
# What one call of each kind can cost at most, checked against what is left before it is made (a check, never a
# charge: the call's own usage is what is spent).
CALL_MOST = {"picture": 0.05, "lesson": 0.10, "lesson edit": 0.03, "label edit": 0.02}


def estimate_cancelled(model: str, text_chars: int, images: int = 0, search: str | None = None) -> float:
    tokens_in = text_chars / 4 + images * IMAGE_TOKENS
    usd = tokens_cost(model, int(tokens_in), OUTPUT_TOKENS)
    if search:
        usd += PRICES[search]
    return usd


class OverBudget(Exception):
    pass


# A limited run's calls (Budget.unlimited False: a daily cap or the monthly cap), each bounded to what is left before it is
# sent (engines._limit for its rounds, Researcher.bound for its lesson, edit and check calls): its input by its UTF-8 bytes
# (no token is shorter than a byte), a ceiling per picture and the request's framing; its output by the provider-enforced
# limit it is sent with (thinking included), sized to what is left and never above its own ceiling. A call that may have
# been billed and reported no usage (cut off, failed) is counted at that most, never at a guess, through the run's
# settlement (Codex's recheck of 73626a0d).
MIN_OUTPUT_TOKENS = 2_000
PER_PICTURE_TOKENS = 2_000
OVERHEAD_TOKENS = 1_000


def bounded_call(budget: "Budget", committed: float, model: str, in_tokens: int, ceiling: int,
                 extra_usd: float = 0.0) -> tuple[int, float]:
    """(output tokens, most) for one call of a limited run: its output sized to what is left of `budget` after
    `committed`, at most `ceiling`; OverBudget when not even MIN_OUTPUT_TOKENS fit, or its price isn't known."""
    price = PRICES.get(model)
    if not isinstance(price, dict) or "in" not in price:
        raise OverBudget(f"{model}'s price isn't known here, so a call can't be bounded within this run's limit")
    left = budget.effort.cap_usd - committed
    fixed = in_tokens * price["in"] / 1e6 + extra_usd
    out = min(int(ceiling), int((left - fixed) * 1e6 / price["out"]))
    if out < MIN_OUTPUT_TOKENS:
        raise OverBudget(f"spent ${committed:.3f} of ${budget.effort.cap_usd:.2f}, and its next call could cost up to "
                         f"${fixed + MIN_OUTPUT_TOKENS * price['out'] / 1e6:.3f}")
    return out, fixed + out * price["out"] / 1e6


@dataclass
class Budget:
    effort: Effort
    spent: float = 0.0
    images: int = 0
    calls: list[dict] = field(default_factory=list)
    unlimited: bool = False

    def check(self, estimate: float = 0.0) -> None:
        """Before a call: refuse it if it would go over the cap."""
        if not self.unlimited and self.spent + estimate > self.effort.cap_usd:
            raise OverBudget(f"spent ${self.spent:.3f} of ${self.effort.cap_usd:.2f}")

    def add(self, what: str, usd: float) -> float:
        self.spent += usd
        self.calls.append({"what": what, "usd": round(usd, 5)})
        return self.spent

    def image_allowed(self) -> bool:
        return self.images < self.effort.max_images
