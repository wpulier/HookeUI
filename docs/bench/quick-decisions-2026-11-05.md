# Quick decisions: Claude Opus 5.5 fast beside Gemini 3.8 Flash and Claude Sonnet 5.5 (2026-11-05)

The numbers behind the `claude-opus-5-5-fast` option of Quick decisions (`Settings.fast`, options `BRAIN_FAST`) and the
default moving to it from `gemini-3.8-flash`. Quick decisions is the fast pass (`brain/fast.py`): it reads the workspace
notes, with no picture, and decides whether a request can be answered and acted on now or needs a camera look. It plans
every spoken hand-off and answers every typed question that needs no look. It also serves the day's memory summaries,
the quiz judge, library looks, the outline check and the guide's and the pointing reflex's fallbacks.

Why it was measured: on 29 Oct the live partner's stack became the default ("dont default to anything old"), and the
5 Nov Beta audit found answers "from the workspace notes" still coming from Gemini 3.8 Flash while the rest of the
stack was Claude. A default changes only on measurements (`services/orchestrator/AGENTS.md`, "Options, not
replacements"), so the candidates were run side by side, at the same time, because provider latency drifts by the hour.
Claude ran at effort `low`, fast mode is Anthropic's `speed: "fast"` beta at twice the price, and Gemini 3.8 Flash at
thinking `low`. These are their `VISION` specs.

## The fast bench

`python -m bench.fast_bench --models <one> --repeats 2`: 58 requests against fixed notes (a leaf with two stomata and a
speck, one outlined and selected, a finished researcher), each with its right call: act now with a given tool, answer
from the notes, wait for a look, or do nothing (remarks, fillers, cut-off words). Level "simple". The three models ran
at once, in three processes, 14:48–14:55 EST. Tokens and cost are the provider's reported usage at list price (a
wrapper around the bench, since the bench records no spend).

| Option | Right | p50 | p90 | p99 | Slowest | Over 3 s | Over the 15 s planner timeout | Input tokens a call | Cost a decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Gemini 3.8 Flash | 104/116 | 2.62 s | 3.84 s | 12.6 s | 30.7 s | 33 | 1 | 26.0k | $0.020 |
| Claude Opus 5.5 (fast mode) | 103/116 | 1.05 s | 2.05 s | 2.77 s | 3.09 s | 1 | 0 | 35.6k | $0.291 |
| Claude Sonnet 5.5 | 106/116 | 2.19 s | 2.79 s | 3.95 s | 4.14 s | 7 | 0 | 35.6k | $0.072 |

No model had an error, an unreadable reply or an action where none was asked for. A call over the 15 s planner timeout
is dropped in the app and the full look answers instead (`brain/loop.py` `PLANNER_TIMEOUT_S`).

**Misses every model shares (6 each).** Three requests, 2 tries each: "Start a new session." (`new_project`; the bench
expects `new_session`), "Back to the leaf session: what did we conclude?" (`recall`; expects `switch_session`) and
"Rename the label on the picture to guard cell." (`name_outline`; expects `revise_work`). The fast prompt now routes
the first two the way all three chose. The bench's expectations are older than the prompt.

**The cases that tell the models apart**, rerun 4 more times each for Opus 5.5 fast and Gemini 3.8 Flash (6 tries per
case in all):

| Request | Expected | Opus 5.5 fast | Gemini 3.8 Flash | What the miss was |
| --- | --- | --- | --- | --- |
| "These two are one cell." (one cell clicked) | `correct_cells` | 0/6 | 6/6 | Opus asks which other cell is meant |
| "Make me another picture, of a closed stoma this time." | `start_researcher` | 6/6 | 0/6 | Gemini fetches a reference photo: a wrong action |
| "Show me what the researcher found." | `show_result` | 0/6 | 4/6 | Opus always uses `show_me`, whose own description is "Show me what it found" |
| "Can you teach me more about that?" | `start_researcher` | 0/6 | 1/6 | both answer it themselves |
| "Clear the outlines and find the guard cell nuclei." | look + `clear` | 3/6 | 3/6 | a `find` without the look |

With `show_me` counted right for "Show me what the researcher found", the full bench is 105 against 105. Sonnet 5.5's
own misses were "I'm a biology student, so be technical with me." (no `set_level`, 2/2) and "Can you teach me more
about that?" (`zoom`, 2/2).

## The will-drive journey

`python -m bench.voice_journeys.run --voice gpt-realtime-2.1 --talk hold --plan will-drive --settings '{"fast": ...}'`
(`docs/bench/voice-journeys.md`). There are 13 spoken requests driving Hooke on the real June 14 leaf recording:
- Tabs made, used and renamed.
- Every cell outlined.
- A capability turned on.
- A researcher started, asked about, steered and stopped.
- A request spoken over a running one.
- A multi-step request.
- An Escape takeover.

The voice hands each request to Hooke's brain, and Quick decisions planned 12 of 14 (Gemini) and 12 of 13 (Opus) of
those hand-offs. There was one run per arm, on the same commit, in parallel on two second stacks (ports 28870 and
28970), 15:13–15:23 EST. The judge was gpt-6.1-sol.

| Line | `fast: gemini-3.8-flash` | `fast: claude-opus-5-5-fast` |
| --- | --- | --- |
| Steps played | 13 of 13 | 13 of 13 |
| Hand-off planning (`thinkMs`), p50 / max | 4.9 s / 6.8 s | 2.7 s / 5.0 s |
| Whole hand-off (`totalMs`), p50 / p90 / max | 6.7 / 10.7 / 15.0 s | 4.3 / 7.4 / 7.6 s |
| Single step, visible and spoken: p50 / p95 | 10.7 / 25.9 s | 6.5 / 21.7 s |
| Researcher started | 4.2 s | 2.9 s |
| Failed steps | 5 | 3 |
| Partner quality, judged 0–3 (steps under 2) | 1.46 (6) | 1.85 (4) |
| False "done" | 0 | 0 |
| Receipts verified / failed | 7 / 1 | 6 / 3 |
| Right tool on the first try | 12 of 13 | 12 of 13 |
| Cost of the run (the stack's Spending, plus the judge) | $4.66 | $12.73 |

How the steps differed:
- In the Gemini arm, "What's the researcher doing?" was answered with the researcher finished while its record said
  it was working. The judge found that untrue.
- In the Gemini arm, "Actually, stop the researcher" was only narrated through `do_in_hooke`.
- The Opus arm used `research_status` and `cancel_researcher` for those two.
- Both arms failed the Escape takeover ("no takeover reached the server"), the close-up asked for while the researcher
  ran, and the plan's expectation for "Turn on Spot look-alikes" (it expects `do_in_hooke` or `models` first;
  both used `turn_on`).
- Neither arm meets the journey standard.

## Cost

Claude's tokenizer reads the fast prompt as 35.6k tokens (Gemini's as 26.0k). Most of it is the tool list. Nothing is
cached: `AnthropicVision` sends no `cache_control`, and the request's notes come before the tool list. So Opus 5.5 fast
costs about 14 times as much a decision as Gemini 3.8 Flash ($0.29 against $0.02), and about $8 more over the 13
requests of the journey. Making the prompt cacheable is a separate change.

## What it means

The default moves to Claude Opus 5.5 fast:
- **Correctness:** the fast bench is a tie. It is one call apart as the bench stands, and level once `show_me` counts.
  Each model has one systematic miss the other does not. Gemini's is a wrong action; Opus's is a question back.
- **Latency:** Opus is 2.5 times as fast on the bench and has no tail.
- **The journey:** it planned the hand-offs in about half the time, failed fewer steps, was judged better, and did not
  tell a working researcher as finished.

Against it: the cost, and one run per arm on a Mac other work was loading heavily (load average 58–124 on 14 cores at
15:07). So the journey's timings are indicative, not settled.

Gemini 3.8 Flash, Sonnet 5.5 and the other options stay, with these numbers in their notes, and any saved choice
persists.

## How it was run, and what it cost

- **Commit:** dev 9f282873 plus this change; the journeys also needed the launcher fix (#203). The local check before
  the PR: `make capability-check`, 25 models, 16 works, 9 not installed here, none broken.
- **Keys:** benches read the keys file named by `MICROMIND_KEYS_FILE`; no key appears in any output.
- **Spend, $68.67 in all:**
  - fast bench $44.43 (Opus fast $33.76, Sonnet $8.37, Gemini $2.30);
  - resample $6.34;
  - smoke checks $0.51;
  - will-drive $4.66 and $12.73;
  - two journey attempts that stopped at "Talk did not turn on" before the fix, $0.
- **Raw runs** are kept outside the repository, with the owner's bench data:
  - every call's row and the cost files of the fast bench and its resample;
  - both journey arms' scoreboards, judged verdicts and the stacks' interaction and conversation logs;
  - the smoke and failed runs;
  - the cost wrapper and the analysis script;
  - the spend ledger.
