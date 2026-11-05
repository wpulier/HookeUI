# Quick decisions: a cacheable prompt order, Claude Opus 5.5 fast and Gemini 3.8 Flash (2026-11-05)

The numbers behind `fast.ORDER = "static-first"` (`services/orchestrator/brain/fast.py`) and Anthropic prompt caching
for the quick decision (`vision.Prefixed`, `AnthropicVision`). Quick decisions is the fast pass: it reads the workspace
notes, with no picture, and decides whether a request can be answered and acted on now or needs a camera look
(`docs/bench/quick-decisions-2026-11-05.md`, in wpulier/HookeUI#204, has what it serves and why its default moved to
Claude Opus 5.5 fast).

Why it was measured: Claude Opus 5.5 fast cost about 14 times as much a decision as Gemini 3.8 Flash ($0.29 against
$0.02). Claude's tokenizer reads the prompt as 35.6k tokens and none of it was cached. Anthropic caches by prefix, and
the prompt began with the request's notes, so nothing after them could be reused. Most of the prompt is the tool list,
which is the same on every call. Reordering the prompt changes what every model reads, so both orders were run side by
side on both models, at the same time, before the default changed (`services/orchestrator/AGENTS.md`, "Options, not
replacements").

## The two orders

Both are built from the same pieces (`fast.prompt`, `fast.ORDERS`). "notes-first" is the prompt as it was, byte for
byte. "static-first" moves this call's notes and words to the end, and adds one sentence to say so ("Your notes and the
user's words come after these instructions"):

| notes-first (as it was) | static-first |
| --- | --- |
| action contract, "You are the quick mind…" | action contract, "You are the quick mind…" |
| **notes**: running look, outlined, screen, researchers, session, conversation | intent rules, "Can you answer…" rules |
| **User: "…"** | tool list (~80 KB) |
| intent rules, the user-leads line, "Can you answer…" rules | JSON reply, measurement, resource and dependency rules |
| tool list (~80 KB) | ← Claude's cache breakpoint |
| JSON reply, measurement, resource and dependency rules | **notes**, the user-leads line, **User: "…"** |

The follow-up round (`FOLLOWUP`) and the point-only prompt are split the same way. Claude is sent the static part as its
own text block with `cache_control: {"type": "ephemeral"}` (5 minutes from its last use). Gemini and OpenAI are sent
the same whole string as before.

## The fast bench

`python -m bench.fast_bench --models <model> --orders <order> --out fast-bench-<model>-<order>.json`: 58 requests
against fixed notes (a leaf with two stomata and a speck, one outlined and selected, a finished researcher), each with
its right call, 2 tries each, level "simple", prompt as-is. All four arms ran at once, in four processes, 15:51–15:59 EST.
The models are their `VISION` specs: Claude Opus 5.5 at effort `low` with fast mode (`anthropic:claude-opus-5-5@low+fast`),
and Gemini 3.8 Flash. Tokens, cached tokens and cost per call are each provider's reported usage at list price, recorded
by the bench itself (`usage`, `usd` on every row).

| Arm | Right | p50 | p90 | p99 | Slowest | Over 3 s | Over 15 s | Input tokens a call | Read from cache a call | Cost a decision | Arm cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Opus 5.5 fast, notes-first | 103/116 | 1.16 s | 2.04 s | 2.31 s | 2.33 s | 0 | 0 | 35.6k | 0 | $0.291 | $33.71 |
| **Opus 5.5 fast, static-first** | **107/116** | **0.90 s** | **1.50 s** | 1.83 s | 2.08 s | 0 | 0 | 35.6k | 35.2k | **$0.022** | $2.56 |
| Gemini 3.8 Flash, notes-first | 106/116 | 2.83 s | 5.72 s | 8.81 s | 21.1 s | 47 | 1 | 26.0k | 0 (first pass) | $0.020 | $1.39 |
| Gemini 3.8 Flash, static-first | 109/116 | 2.81 s | 5.66 s | 12.7 s | 37.3 s | 50 | 1 | 26.0k | 0.8k (first pass) | $0.019 | $1.41 |

- **Claude's cache.** A cold call writes the 35,192-token static part: $0.359 in the smoke check at 15:51:13, against
  $0.291 uncached. Every call within 5 minutes then reads it: 116 of 116 in the static-first arm, which the smoke had
  just warmed, so the arm wrote nothing. A warm decision pays for about 400 uncached tokens, the cache read and the
  output. Caching pays from the second decision.
- **Gemini's cache.** Gemini caches on its own, with no marker. Here it matched only exact repeats of a whole prompt.
  The first pass of 58 hit 0 times notes-first and 2 times static-first. The second pass, which repeats the first
  word for word, hit 55 and 53 times. Live requests are not repeated word for word, so the first pass is Gemini's real
  cost: $0.020 and $0.019. The order leaves it unchanged. Over both passes the rows average $0.012.
- **Gemini's speed** was in a slow patch for both of its arms (calls of 5–7 s for a few minutes, the same in both
  orders since they ran side by side). Its p50 matches the 14:48 run (2.62 s), and its tails are the provider's. One
  notes-first call failed with Google's "503 UNAVAILABLE … high demand" ("Can you explain things more simply?").
- **Errors and unasked actions.** There were no errors besides that 503, and no unreadable replies. One unasked
  action: Gemini static-first used `compare` for "Is the stoma on the right bigger than the one in the middle?" once,
  where the case expects a look.

Same afternoon, before this change (14:48, the default-change bench): Opus 5.5 fast 103/116, p50 1.05 s, $0.291.
Gemini 3.8 Flash 104/116, p50 2.62 s, $0.020.

### Misses, every arm

| Request (expected) | Opus notes-first | Opus static-first | Gemini notes-first | Gemini static-first |
| --- | --- | --- | --- | --- |
| "Start a new session." (`new_session`) | 0/2 `new_project` | 0/2 `new_project` | 0/2 `new_project` | 0/2 `new_project` |
| "Can you teach me more about that?" (`start_researcher`) | 0/2 answered | 0/2 answered | 0/2 answered | 1/2 |
| "Show me what the researcher found." (`show_result`) | 0/2 `show_me` | 0/2 `show_me` | 2/2 | 2/2 |
| "Back to the leaf session: what did we conclude?" (`switch_session`) | 0/2 `recall` | **2/2** | 0/2 `recall` | 0/2 `recall` |
| "Rename the label on the picture to guard cell." (`revise_work`) | 0/2 `name_outline` | **2/2** | 0/2 `name_outline` | **2/2** |
| "Bring the lesson back up." (`show_result`) | 1/2 (`resources` once) | 2/2 | 2/2 | 2/2 |
| "These two are one cell." (`correct_cells`) | 0/2 asks which | 1/2 | 2/2 | 2/2 |
| "Make me another picture, of a closed stoma this time." (`start_researcher`) | 2/2 | **0/2** `researcher_followup` | 1/2 (`reference_photo` once) | 2/2 |
| "Can you explain things more simply? I'm new to this." (`set_level`) | 2/2 | 2/2 | 1/2 (the 503) | 2/2 |
| "Is the stoma on the right bigger than the one in the middle?" (look) | 2/2 | 2/2 | 2/2 | 1/2 (`compare` once) |
| "Clear the outlines and find the guard cell nuclei." (look + `clear`) | 2/2 | 2/2 | 2/2 | 1/2 (`clear`, `find` once) |

As the earlier write-up says, the bench's expectations for "Start a new session" (`new_project` now) and for "Back to
the leaf session" and "Rename the label" are older than the prompt. Static-first moved Claude to the bench's answer on
the last two.

### The cases where Opus's orders differ, resampled

`--repeats 4 --only "Bring the lesson back up|Rename the label on the picture|Make me another picture|These two are one
cell|Back to the leaf session"`, both orders at once, 15:57–15:59 EST. With the bench's 2 tries that is 6 per case:

| Request (expected) | notes-first | static-first |
| --- | --- | --- |
| "Bring the lesson back up." (`show_result`) | 5/6 | 6/6 |
| "Rename the label on the picture to guard cell." (`revise_work`) | 0/6 | 6/6 |
| "These two are one cell." (`correct_cells`) | 0/6 | 3/6 |
| "Back to the leaf session: what did we conclude?" (`switch_session`) | 1/6 | 6/6 |
| "Make me another picture, of a closed stoma this time." (`start_researcher`) | 6/6 | 0/6 |
| The resample alone | 9/20, p50 1.74 s, $0.298 a call | 14/20, p50 1.74 s, $0.030 a call |

The one case static-first lost is a judgment call. Two more tries printed the whole decision. Both times it was
`researcher_followup(researcher "1", mode "continue", question "Make another picture, this time of a closed stoma: …the
pore is shut.")`, saying "Asking the guard-cell researcher to make another picture, this time of a closed stoma…". The
user still gets the picture, from the researcher that already did that work, and the prompt describes `continue` as
"more of the same".

## The point-only prompt

`python -m bench.point_bench --arms "full@notes-first:gemini-3.8-flash,full@static-first:gemini-3.8-flash,full@static-first:claude-opus-5-5-fast"`,
14 questions and remarks × 2, 15:59–16:05 EST. This is the full fast pass told only to point (REFLEX "after-voice"; the
default reflex is the lean prompt, which this change does not touch).

| Arm | Right | p50 | p90 |
| --- | --- | --- | --- |
| Gemini 3.8 Flash, notes-first | 28/28 | 2.51 s | 3.43 s |
| Gemini 3.8 Flash, static-first | 28/28 | 2.40 s | 4.03 s |
| Opus 5.5 fast, static-first | 28/28 | 0.84 s | 1.22 s |

Opus notes-first was not run here (about $8). Gemini gives the same-model comparison.

## The follow-up round

No bench covers the follow-up round. As a smoke check, two Opus 5.5 fast calls in static-first order were run at
16:05. The request was "Magnify that and tell me what the guard cells are doing.", with the receipt of a done zoom.

| Call | Input | Read from cache | Written to cache | Cost | Time |
| --- | --- | --- | --- | --- | --- |
| 1 | 34,158 | 0 | 33,707 | $0.354 | 2.0 s |
| 2 | 34,158 | 33,707 | 0 | $0.032 | 1.9 s |

Both replies said the zoom in the past tense and added only `inspect_camera` for the part not yet done.

## What it means

The order moves to static-first, for every model:
- **Cost:** Claude Opus 5.5 fast's quick decision falls from $0.291 to $0.022 once warm, 13 times cheaper and about
  1.1 times Gemini 3.8 Flash. A cold call (the first in 5 minutes, or the first after the tool list changes) costs
  about $0.36. Gemini's cost does not change.
- **Correctness:** no worse on either model. Opus went 103 to 107 and Gemini 106 to 109. On the resampled cases Opus
  was 14/20 against 9/20, with one judgment-call miss. The point-only prompt held 28/28.
- **Latency:** Opus is faster, p50 0.90 s against 1.16 s and p90 1.50 s against 2.04 s, since it reads 35k tokens from
  its cache instead of processing them. Gemini is the same.

Caveats:
- One run per arm.
- The Mac was loaded by other work (load average 12–25 on 14 cores at 16:02).
- Gemini was in a slow patch.
- The follow-up round has a smoke check, not a bench.
- The live hit rate depends on the tool list staying byte-identical between calls and on decisions coming within
  5 minutes of each other. Each call's record keeps `cachedTokens` and `cacheWriteTokens`, so the live rate can be
  read there.

"notes-first" stays in `fast.ORDERS` for the benches, and `fast.ORDER = "notes-first"` puts back the old prompt.

## How it was run, and what it cost

- **Commit:** dev 9f282873 plus this change (branch `claude/fast-prompt-cache`).
- **Prices** (Anthropic's and Google's pricing pages, read 2026-11-05):
  - Opus 5.5: $4 in, $20 out, a 5-minute cache write 1.25× the input price, a cache read $0.20.
  - Fast mode doubles all of these, since caching's multipliers apply on top of it.
  - Gemini 3.8 Flash: $0.75 in, $3.75 out, a cached read $0.075.
  - These are `PRICES` in `research_worker/budget.py` and `vision.anthropic_usd`.
- **Keys:** benches read the keys file named by `MICROMIND_KEYS_FILE`; no key appears in any output.
- **Spend, $46.49 metered and about $2 estimated:**
  - fast bench $39.07 (Opus $33.71 and $2.56; Gemini $1.39 and $1.41);
  - resample $6.57;
  - smoke checks $0.85 (static-first smoke $0.40, two whole-decision prints $0.06, follow-up round $0.39);
  - point bench about $2. It records no usage, so this is estimated from the fast bench's per-call costs.
- **Raw runs** are kept outside the repository, with the owner's bench data, in
  `~/MicroscopeMind/bench/quick-decisions-prompt-cache-2026-11-05/`:
  - every call's row (with usage and cost) for the four arms;
  - the resample and point-bench consoles;
  - the smoke outputs;
  - the analysis script;
  - the spend ledger;
  - the checks on this change's tree.
