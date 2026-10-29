# Voice journeys: the live partner against its standard

`services/bench/voice_journeys` plays a spoken journey through a real voice on a second stack and scores it. It
tells, in numbers, whether the live research partner meets the acceptance standard of 29 Oct
(`PARTNER-CONTRACT.md`, the Part 2.5 plan), for both voices:
- `gpt-live-1` (GPT-Live, the default today): open microphone, every request handed to the brain;
- `gpt-realtime-2.1` (the one brain): hold to talk (or open), sees the window and the frame, calls tools itself.

The default switches to the one brain with hold to talk only when it meets every line. A line that was not measured
is not met.

This is evidence of the first kind in `docs/testing/part-2.5-journey.md`: recorded audio (macOS voices), repeatable,
on a candidate. Acoustic replay is the second kind (below). Neither is Will in a room, which is the only evidence of
how it feels. A run says whether the camera was a still image (the Part 2.5 plans use the bundled nuclei sample) or
test footage.

## Run it

Everything runs from `services/`, on a second stack with its own home, ports and research cap. It never touches the
live app. A paid run first reserves its worst case with the Part 2.5 budget tool and refuses if the reservation is
refused. It settles afterwards from what the stack recorded: its Spending (`/api/spending`), plus the one brain's
provider-reported tokens when Spending leaves them unpriced, plus the judge. If none of that is available, it settles
at the worst case.

```sh
# One voice, N repetitions of a plan, in a row on one stack (the plan's setup runs once per stack)
.venv/bin/python -m bench.voice_journeys.run --voice gpt-realtime-2.1 --talk hold --plan part-2.5 --reps 10 \
    --out /path/p25-rt --budget /path/budget.py --keys <keys file>
.venv/bin/python -m bench.voice_journeys.run --voice gpt-live-1 --plan part-2.5 --reps 10 --out /path/p25-live \
    --budget /path/budget.py --keys <keys file>
# The second pass, on the same stack folder after the first (its setup is already done there)
.venv/bin/python -m bench.voice_journeys.run --voice gpt-realtime-2.1 --plan part-2.5-second --reps 10 --out /path/p25-rt-2 ...
# Both voices side by side, and whether the default may switch
.venv/bin/python -m bench.voice_journeys.compare /path/p25-live /path/p25-rt
# Score kept runs again (a scorer change, a new threshold): nothing started, nothing spent
.venv/bin/python -m bench.voice_journeys.run --score-only --plan part-2.5 --out /path/p25-rt
```

- **Voices and talk modes.** `--voice` and `--talk` set the stack's `voice` and `talk` settings
  (`MICROMIND_SETTINGS`). GPT-Live listens with an open microphone only, so `--talk hold` is refused for it. Hold mode
  presses Playwright's trusted Control+Option around each step's words, the studio's talk key. The studio then sends
  `{type:"talk", down:true, n}` with its key-down snapshot, and `{type:"talk", down:false}` on release. Without the
  driver, the player holds the Talk button instead (pointerdown and pointerup).
- **What it needs.** `node`, Playwright (`HOOKE_PLAYWRIGHT`, else the npx cache) and Chrome or Chromium
  (`HOOKE_CHROMIUM`, else `/Applications/Google Chrome.app`). A fresh browser profile is made per pass. The studio
  build in the checkout (`npm run build` once).
- **Its own ports.** `--port` and `--bridge-port` (defaults 28870 and 28865) keep it apart from other stacks.
- **What a run folder holds.**
  - `home/`: the stack's home and its logs.
  - `setup/` and `run-N/`: each pass's `bench.json` (the player's state) and the judge's `verdicts.json`.
  - `result.json`: every step, every run, the summary and the scoreboard.
  - `scoreboard.md`: the short table and the failed steps.
- **Exit codes.** 0 means the standard is met; 1 means it is not; 3 means the budget refused the reservation.
- **Cheap check.** The `smoke` plan has two steps (about $0.15 with GPT-Live). Use it to see that the harness drives a
  voice end to end before a long run.

### Acoustic replay

The recorded words go out of this Mac's speakers and into its microphone. This checks what injected audio can't: echo,
the voice hearing itself, and the room. It needs a real microphone and speakers (not headphones), the volume up, and
the studio in front.
- **On a second stack:** `run.py --acoustic`. It uses a visible browser and the real microphone, and still hears the
  voice in the page.
- **On the live app (the packaged build):** run the command below. The terminal running it needs Accessibility
  permission, because System Events presses Control+Option. Hooke must be in front, with the Project open and Talk
  connected. A restart step asks you to quit and reopen Hooke and press Return. Nothing is read from the page, so audio
  timing comes from the one brain's own records (`partner-response`, `partner-audio`). Marks, frames and the
  researchers board are not measured there. It never starts or stops the app, and it only reads the home's logs. Run it
  only when the person running the app agrees.

```sh
.venv/bin/python -m bench.voice_journeys.acoustic --plan part-2.5 --home ~/MicroscopeMind --out /path/acoustic \
    --talk hold --budget /path/budget.py
```

## The plans

The plans live in `plans/<id>.json`. The stack writes each step's words as 24 kHz WAV with `say`, and keeps them while
the words are unchanged. `[[slnc 1500]]` in the words is a 1.5 s pause.

| Plan | What it is |
|---|---|
| `part-2.5` | The journey's steps 1–8 and 10 in Will's words, voice Samantha. Step 8 restarts the orchestrator on the same home and reloads the page, then checks that the Project, its Measurements Tab and the researcher are there. An idle wait before it lets a researcher finish, so its event can be told. Step 9 (a connected agent) is a person's step. |
| `part-2.5-second` | The second pass: another order, other words, voice Karen. It adds one interruption (the key pressed 2.5 s into the voice's answer), one request that cannot succeed (a table that does not exist), one forced reconnect (`fault: close-provider`) and a switch to another Project while the researcher runs, then back. |
| `smoke` | Two steps, no setup. |
| `demo-2026-10-11` | The 11 Oct demo journey plus the 28 Oct log's failures on test footage. It is scored by `score.py`, the experience contract of the GPT-Live era. |

- **Setup.** It is typed in the chat once per stack before Talk. It makes a Nuclei Project with outlines, measurements
  and one finished researcher, then a Scratch Project, as the journey's setup asks.
- **Kinds.**
  - `act`, `view`, `ask`, `status`: spoken requests.
  - `idle`: no words. It waits for the newest researcher to finish.
  - `restart`: quit and reopen Hooke.
  - `typed`: a request typed in the chat.
- **Expectations.** Each step's `expect` says:
  - `tools`: the acceptable first acting tool (a `mark` doesn't count);
  - `toolRequired`: whether a tool must be used;
  - `visible`: the change the studio must report;
  - `researchers`: how many researchers this request starts (0 when absent);
  - `marks`: the target prefixes a mark may use;
  - `status`: the answer must name the researcher this run started last;
  - `multi`: several steps, held to 8 s;
  - `fails`: the request cannot succeed, so the failure is its outcome;
  - `judge`: what a good answer does, for the judge.

## What is measured, and from where

Everything is on one clock (epoch seconds). Three logs are joined:
- **The player's log** (`harness.js`, loaded before the studio's own code):
  - the voice's audio as heard in the page (the remote WebRTC track's level, and its audio element not muted);
  - `/ws/voice` both ways;
  - the voice's lines as they grow on `/ws`;
  - the live view's frames per second and delay (from each `MMF1` header);
  - each mark the studio drew (`svg#voice-marks .voice-mark`, with its rect and its target's rect), and when it
    cleared;
  - the researchers board;
  - the restart checks.
- **The stack's conversation log:** each spoken line and each tool card.
- **The stack's interaction log:** the one brain's records (`partner-talk`, `-turn`, `-response`, `-audio`, `-barge`,
  `-tool`, `-marks`, `-proactive`, `-reconnect`), and GPT-Live's hand-offs.

| Line | How it is measured |
|---|---|
| Release → first audio | Key up (hold), or the end of the words (open), to the first audible audio. Median ≤ 0.8 s, p95 ≤ 1.5 s. |
| Barge-in → silent | Key down while audible, to silence: track level and the element muted. ≤ 150 ms, and it must stay silent while the key is held. |
| Visible and spoken | Release to the later of the studio's confirmation (`partner-tool` `reportT`, or GPT-Live's tool card first shown ok) and the first words said after it (as the page saw them arrive). A researcher step's change is its start. Single steps: median ≤ 3 s, p95 ≤ 5 s. Multi-step: ≤ 8 s, at the last confirmation. |
| Longest silence while working | The longest gap without audio from release until the outcome is said. ≤ 1.5 s. |
| Said once | The lines with words after the confirmation (the judge's `outcome_mentions` when it ran). Exactly 1. |
| Meant for the model | Spoken lines checked for the known leaks ("Nothing was outlined", "Context only", the notes' own wording, tool names), JSON and ids. This is a measurement list, never product logic. 0. |
| Seeing | "I can't see your screen" and its variants. 0. |
| Right tool first | The first acting call in the step is in `expect.tools`. Every step. |
| `do_in_hooke` share | Over the one brain's own acting calls. Under 5%. GPT-Live has no such tool: not measured. |
| False "done" | The judge names each claim that something is on screen or done, with its words. It is false when the page showed those words before any studio confirmation (0.3 s race allowed). 0. Without the judge: not measured. |
| One researcher | Researchers started in the step (`start_researcher` with its id) equal `expect.researchers`; ≤ 2 s from release to its start. |
| Status | The researcher numbers said ("researcher 2", "researcher two") are exactly the one this run started last (the board's number). |
| Marks | Shown minus the start of the speech it sits in: ≤ 200 ms. The target matches `expect.marks` and resolved (`marks-shown`). For page targets (tabs, researchers, windows, documents), it follows its target (the mark's rect over the target's, IoU ≥ 0.8). It clears ≤ 2 s after the speech ends. Outline and frame marks follow by construction: the studio resolves them every frame. |
| Events | `partner-proactive`: the event to its first audio ≤ 2 s; never while the key is held or within 1.5 s of release; one line per event; pointing at its card. |
| Reconnect | `partner-reconnect` `ms` (the bench fault closes the provider's call): ≤ 2 s. |
| Live view | Frames per second and delay p90 while the journey runs, against the 10 s before Talk: at least 90% of the frame rate, no more than 50 ms added delay. |
| Robustness | Runs with no failed step. A step fails on any of the above, no answer, or a missing restart check. |
| Elegance | Lines of one or two sentences: at least 90% (the bench's reading of "one or two plain sentences"). No confirmed screen change by a tool outside `expect.tools`. |

**The judge** (`judge.py`, the same model as `score.py`) is shown the specimen picture, the user's words, the lines
and the tool calls with their confirmations. It judges claims, outcome mentions and whether the request was answered.
Times, counts and identities are code. `--no-judge` leaves false "done" unmeasured.

## Read the scoreboard

`scoreboard.md` has one row per line: area, line, what was measured (with how many samples), the standard, and
`pass`, `FAIL` or `not measured`. "Standard met: yes" only when every row passes. Below the table, each failed step
says why (for example, "outcome said 2 times", or "first tool tabs, expected show_measurements"). `compare.py` puts the
voices in columns and ends with whether the default may switch.

Report what was observed and how many times it ran, never a promise. The scoreboard's first line says whether the
audio was injected or played acoustically, and whether the camera was a still image.

## Limits

- **The relay link.** It plays the voice through the studio's own audio graph, which the player does not hear. The
  default WebRTC link is measured in the page. Without page audio, the one brain's records give the audio. For GPT-Live
  heard acoustically, only its lines' first words are available, as instants.
- **A line's journal record** keeps only when the line began. Claims and outcomes are timed by when their words arrived
  in the page. Runs without the player (acoustic on the live app) fall back to when the line began.
- **Proactive events, marks, `do_in_hooke` and reconnect** are the one brain's. GPT-Live's scoreboard shows them as
  not measured, so GPT-Live can never "meet" the standard. Its numbers are the baseline.
- **Faults.** Only a second stack started with faults on answers them (`MICROMIND_BENCH_FAULTS=1`, never in a
  packaged build).
