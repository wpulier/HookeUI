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
- **Settings.** `--settings` (JSON, over a plan's own `"settings"`) reaches the stack as `MICROMIND_SETTINGS`, e.g. the
  best version's `{"vision": "claude-opus-5-5-fast", "think": "claude-opus-5-5"}`; it is kept in `about.json` and shown
  on the scoreboard.
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
| `part-2.5` | The journey's steps 1–8 and 10 in Will's words, voice Samantha, plus one multi-step request ("Outline the nuclei, then put their measurements in a new tab called Sizes."). Step 8 restarts the orchestrator on the same home and reloads the page, then checks that the Project, its Measurements Tab and the researcher are there. An idle wait before it lets a researcher finish, so its event can be told. Step 9 (a connected agent) is a person's step. |
| `part-2.5-second` | The second pass: another order, other words, voice Karen. It adds one interruption (the key pressed 2.5 s into the voice's answer), one request that cannot succeed (a table that does not exist), one forced reconnect (`fault: close-provider`) and a switch to another Project while the researcher runs, then back. |
| `part-2.5-broad` | The broad journey (Will, 29 Oct: "incredible dynamic assistant epicness at the level of Clicky"): one continuous session of 30 casual requests. It covers seeing, pointing ("circle the cluster on the left"), acting, references to earlier turns ("that one", "the second one"), two researchers from one request and "what's researcher 2 doing?", capabilities (BioCLIP, the models in use, "switch your brain to Claude"), comparing three outline models, a reference picture and a web search, a new Project and back, one request that can't succeed, one interruption and small talk that must use no tool. Nobody talks while the slide is moved once (the watcher must raise it; with the default WATCH quiet it is told silently). An idle wait at the end lets the last researcher finish and its event be told, and counts the watcher's false alarms. Run it on a fresh stack, so researcher numbers start from the setup's Researcher 1. One run's worst case is about $24 for the one brain. |
| `part-2.5-watch` | The watcher (`orchestrator/view_watch.py`) with WATCH chatty, the plan's own `settings`: "keep an eye on the slide with me", then nobody talks while the slide is moved once (a panning clip the stack makes from the still: held 14 s, moved over 2 s, held), "what just changed?", then 5 minutes where nothing happens. Worst case about $1.30 for the one brain; a run cost $0.39-0.46. |
| `smoke` | Two steps, no setup. |
| `demo-2026-10-11` | The 11 Oct demo journey plus the 28 Oct log's failures on test footage. It is scored by `score.py`, the experience contract of the GPT-Live era. |

- **Setup.** It is typed in the chat once per stack before Talk. It makes a Nuclei Project with outlines, measurements
  and one finished researcher, then a Scratch Project, as the journey's setup asks. Each new Project opens on the empty
  stage, so a `stage` step puts the specimen on it, as a person does: Use live camera, then the bridge plays the still.
- **The specimen.** The still is played as a 20 s loop with sensor-like noise (`stack.py` `noisy_loop`). The bridge
  heals a camera whose frames are byte-identical (a frozen iPhone), and that stopped a plain still's live view about
  17 s after a Project's Use live camera. On this second stack the live camera does not come back by itself when a
  Project is reopened ("the live view starts fresh"), so after any step that leaves the stage empty the player puts the
  specimen back and records it (`staged`; the scoreboard says how many times).
- **Where the journey is.** `ensure: "project:Nuclei"` on a step: if the voice left the journey elsewhere (step 1's
  "Open the Nuclei project." not done), the player opens it through the sessions rail's own message and records it
  (`recovered`). The step is scored as it was; the steps after it are not scored again for it.
- **The restart** (step 8) checks that what was there before it is there after it: the Project open before the restart
  (also read from the journal: the Project its lines were filed in just before and just after), each named Tab that
  existed, and the newest researcher (`/api/research/<rid>`). A Tab or researcher that an earlier step failed to make
  is "not applicable" there, never a second failure.
- **Kinds.**
  - `act`, `view`, `ask`, `status`: spoken requests.
  - `idle`: no words. It waits for the newest researcher to finish.
  - `restart`: quit and reopen Hooke.
  - `typed`: a request typed in the chat.
  - `stage`: the specimen put on the stage (setup).
- **Expectations.** Each step's `expect` says:
  - `tools`: the acceptable first acting tool (a `mark` doesn't count);
  - `toolRequired`: whether a tool must be used;
  - `visible`: the change the studio must report;
  - `researchers`: how many researchers this request starts (0 when absent);
  - `marks`: the target prefixes a mark may use;
  - `status`: the answer must name the researcher this run started last (`"latest"`), the one the user named (a
    number: "what's researcher 2 doing?"), or the i-th one a named step started, by the number the app gave it
    (`{"from": "two-researchers", "index": 0}`: "the size one");
  - `multi`: several steps, held to 8 s;
  - `fails`: the request cannot succeed, so the failure is its outcome;
  - `judge`: what a good answer does, for the judge;
  - `watch` (idle steps): the watcher's events that must be raised within the step (`["slide-moved"]`), or `[]` for a
    steady stretch where every event is a false alarm. A step's `footage` is switched before it starts, so the cut
    that brings it is not counted in it.

## What is measured, and from where

Everything is on one clock (epoch seconds). Three logs are joined:
- **The player's log** (`harness.js`, loaded before the studio's own code):
  - the voice's audio as heard in the page (the remote WebRTC track's level, and its audio element not muted);
  - `/ws/voice` both ways;
  - the voice's lines as they grow on `/ws`;
  - the live view's frames per second and delay (from each `MMF1` header);
  - each mark the studio drew (`svg#voice-marks .voice-mark`, with its rect and its target's rect), and when it
    cleared;
  - the Project's researchers, from the orchestrator (`/api/research`: number, created, finished, status), the
    researchers board only when that does not answer;
  - whether the stage had a source each second (the empty stage is no live view, not a slow one);
  - the restart checks, `recovered` and `staged`.
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
| `do_in_hooke` share | The fallback hand-off ("no tool for this") over the one brain's own acting calls. Under 5%. GPT-Live has none: not applicable. |
| `think` share (reported) | `think`, the frontier mind behind the voice, is the intended route for hard and multi-step work, not a fallback: it is the right tool on the first-try line wherever a step lists it, and the wrong one where it does not (a quick step such as "switch back to the main tab"). Its share is reported by kind of step, with the steps that used it where not expected, never capped. A think call returns at once; its task (`partner-think`, with the frontier mind's own tool calls inside) is done at `doneT`, confirmed on screen when one of those calls was, and its result, said later (`partner-think-said`, then a `think` response), is the asking step's outcome wherever it falls. Calls that run in the background the same way (`reference_photo`, `retrieve_page`: the call's record names its task, `partner-background` says when it landed) are joined and credited alike; a picture counts as shown when it lands. |
| False "done" | The judge names each claim that something is on screen or done, with its words. It is false when the page showed those words before any studio confirmation (0.3 s race allowed). In a step whose tools change nothing on screen (a note to a researcher, a setting), no studio report can come: there the claim is backed by the app's own state (the researcher's notes rising in `/api/research`, a setting changing in what the studios are sent), else, for runs without that state, by the call's success. A hand-off (`think`, `do_in_hooke`) never backs a claim by its own success. 0. Without the judge: not measured. |
| One researcher | Researchers the orchestrator lists as created in the step (else the successful `start_researcher` calls) equal `expect.researchers`; ≤ 2 s from release to its creation. |
| Status | The researcher numbers said ("researcher 2", "researcher two") are exactly the one the plan's researcher step started (its number from the orchestrator, else its card's "Researcher N"). When that step started none, the status step is not checked (that failure is counted once, there). |
| Marks | Shown minus the start of the speech it sits in: ≤ 200 ms. The target matches `expect.marks` and resolved (`marks-shown`). For page targets (tabs, researchers, windows, documents), it follows its target (the mark's rect over the target's, IoU ≥ 0.8). It clears ≤ 2 s after the speech ends. Outline and frame marks follow by construction: the studio resolves them every frame. |
| Drive | The spec's Drive line (hooke-web `part-2.5-live-partner-spec.md`, section 1; claude/p25-best, `docs/architecture/on-screen.md` "Hands"): every action shown with a cue before its effect (`partner-drive` shown within 0.5 s of sending), each takeover's delay from the studio's input to the server knowing (`partner-takeover` `atServer − atStudio`) ≤ 150 ms, and no action after a takeover planned on an older control version (`partner-hands`). Actions verified and cues the page saw without a `drive-end` are details within the line. A run with no takeover leaves it not measured: `part-2.5-broad`'s `take-over` step has the bench move the mouse at the partner's first cue (`"takeover"` in the plan; the step is scored on stopping). Not applicable to GPT-Live. |
| Events | `partner-proactive`: the event to its first audio ≤ 2 s; never while the key is held or within 1.5 s of release; one line per event; pointing at its card. |
| Watch: an event said when idle | Only in plans with watch steps, when something was to be said or an expected event never came. Each `watch-event` the watcher told the voice to say (rule idle or interrupt), from its `eventT` (the frame at which it was established) to the first audio of its `partner-proactive` line: ≤ 2 s, never over the user, none missed, none raised in its watch step and left unsaid. One brain only. |
| Watch: false alarms per hour | Every `watch-event` raised in a steady watch step (`expect.watch: []`), per hour of those steps: ≤ 1 an hour, provisional until Will accepts a rate. The watcher is the app's, so this applies to every voice. |
| Reconnect | `partner-reconnect` `ms` (the bench fault closes the provider's call): ≤ 2 s. |
| Live view | Frames per second and delay p90 while the journey runs, against the 10 s before Talk (else the setup's): at least 90% of the frame rate, no more than 50 ms added delay. Only seconds with a source on the stage count, and a freeze with a source on it fails; a run with nothing on the stage says "not measured: no live view on the stage". |
| Robustness | Runs with no failed step. A step fails on any of the above, no answer, or a missing restart check. |
| Elegance | Lines of one or two sentences: at least 90% (the bench's reading of "one or two plain sentences"). No confirmed screen change by a tool outside `expect.tools`. |

**The judge** (`judge.py`, the same model as `score.py`) is shown the specimen picture, the two turns before, the
user's words, the lines and the tool calls with their confirmations. It judges claims, outcome mentions and whether
the request was answered. Times, counts and identities are code. `--no-judge` leaves false "done" unmeasured.

**Which judge.** `--judge` chooses it: `gpt-6.1-sol` (the default, so new runs compare with old ones),
`claude-opus-5-5` (the Messages API over plain httpx, effort low, server-side fallbacks on) or `gemini-3.8-flash`. All
three get the same prompt; each reply's JSON is read from its text. With `--judge auto` (the default) the default judge
is used unless its key is missing or a tiny call to it fails (credit run out), then Claude, then Gemini. The judge used
is kept with each run's verdicts (`_judge`), shown on the scoreboard ("Judged by …"), and in `result.json` with any
judge passed over and why. `compare.py` warns when runs were judged by different judges: the judged lines are not
comparable then. A re-score judges again only runs with no verdicts or with a step the judge could not answer
(`--rejudge` for all); that is paid, so it needs `--budget` and reserves the judged steps' worst case first.

**A planned interruption.** The key goes down `interrupt.afterMs` (4.5 s) after the previous answer's first audio,
while it still speaks. The judge is told the cut was the user's on purpose: the cut answer is judged on what it said
before it, and stopping at once and giving what was asked next are judged on the interrupting request. A re-score
judges again only the steps without an answer: delete one step's verdict from `verdicts.json` to judge just that step.

**Overall: partner quality.** The judge also gives every step a `partner_quality` from 0 to 3: did the reply feel like a
sharp, dynamic colleague? That means specific to what is on screen and in the Project, pointing at what it talks about,
using earlier turns, brief and natural, with no filler or hedging. The prompt calibrates it with one good and one bad
answer. The scoreboard's Overall line reports the mean and the steps under 2. It is reported only, never a gate: the
standard is met or not on its own lines.

**GPT-Live** has no `do_in_hooke`, marks, events told unprompted or reconnect of its own: those lines say "not
applicable to this voice" for it. Barge-in applies to both voices (GPT-Live's open microphone is interrupted by
speaking over it; the second pass has the interruption). "Not applicable" is not a pass: a voice without a feature the
standard asks for does not meet it.

## The watcher on footage

`bench/watch_bench.py` runs the watcher (`orchestrator/view_watch.py`) with no voice, no model and no cost: every frame
goes through the hub's own per-frame analysis (the eyepiece disc, the slide's motion and epoch, sharpness and light, the
disc's edge, JPEG at the bridge's quality) and the coach, on the footage's own clock. Its synthetic footage is the
bundled example as a brightfield-like slide through an eyepiece, with what a hand-held phone adds: sensor noise,
sub-pixel shake, a jolt every 25 s, the focus breathing (sharpness swinging 13% median, 15% p90 within a second, near the
June 14 footage's 6%, 15% and 29%) and light flicker. `--video` runs it over a recording and lists its events.

Measured 2026-10-29 (30 fps, `--minutes 60`):

| Scenario | Truth | Raised | After it happened |
|---|---|---|---|
| pan (most of the eyepiece, then still) | one slide-moved | one | 0.73 s (settled 0.6 s, then the tracker's steady frames) |
| cut (continuity lost, then still) | one slide-moved | one | 1.87 s (still 1.2 s first, for a rejoin) |
| defocus (a blur that stays) | one focus-drifted | one | 2.97 s (under 60% for 3 s) |
| light halved | nothing | nothing | |
| knob turned and left blurred | nothing | nothing (one focus-drifted without the coach's sweep) | |
| steady, 60 minutes | nothing | **0 false alarms** | |

The watcher's own cost on the frame path over that hour: p50 8.4 µs, p99 36.6 µs, at most 2.3 ms once in 108,000
frames. Again on the final code (the rest and same-picture rules): 0 false alarms in the hour, the other scenarios
unchanged, cost p50 10.4 µs and p99 42.8 µs (one frame of 108,000 took 16.9 ms on a loaded machine, with nice 10).
The stack's panning clip (`stack.panning_clip`) gives one slide-moved, 0.8 s after its move ends, and four
plays of the noisy loop none.

**Real footage** (the June 14 leaf epidermis recording, 3m43s at 1280 x 720, iPhone on the eyepiece, read from the assets
store): the slide is moved around for the first 40 s, then left (a 46 px nudge at 147-152 s). The watcher raised 7
slide-moved: 6 after real moves (10.2, 16.9, 24.3, 25.7, 34.0 and 39.1 s, each after 230-520 px of travel, seen in the
frames and the motion trace) and 1 false, at 152.4 s: an 80 px adjustment just after the 39.1 s move and the nudge 108 s
later added up past a third of the radius. Since then a place the view rests at for `REST_S` (3 s) is the field's, so
a later nudge is measured from there: the clip gives the 6 real moves and **0 false alarms** (0 an hour, from 3.7
minutes, 3 of them still) and no focus-drifted. Frame cost p50 16 µs, p99 42 µs.

**With the voice** (`part-2.5-watch`, `gpt-realtime-2.1`, hold, one run each, 2026-10-29):
- **The first run** (load 14-22 on 14 cores, frame analysis at p50 up to 2.9 s, event-loop stalls of about 1 s in other
  code): the cut and the pan were heard 1.8 s and 1.2 s after their events, two later events 8.4 s and 2.2 s during the
  stalls, and one false alarm on the steady stretch (continuity lost on the unchanged still: "the slide moved again").
  Since then a cut whose picture matches the field's last still one is no move (`same_place`).
- **The final code** (load 4-6): "Watch: an event said when idle" passes, p50 1.40 s and max 1.45 s from the event to
  the first audio heard (n 3: two cuts to other footage and the pan), never over the user, none missed or unsaid;
  "Watch: false alarms per hour" passes, 0 in 5.1 minutes. It said each one in its own words ("The slide moved about
  one field of view toward the left."). Asked "what just changed?" 25 s after saying that, it described the new view
  and not the move (the judge: not answered, partner quality 1; the first run's answer said the pattern had shifted
  left). The run's live view line failed narrowly (24 vs 27 fps): the hub's frame analysis
  slowed from p50 14 / p90 20 ms to 21 / 67 ms after the cut to the steady still and stayed so, in the analysis thread,
  where the watcher does not run (fps 28 while the pan was watched).

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
  not applicable to this voice, so GPT-Live can never "meet" the standard. Its numbers are the baseline.
- **An event's time.** The one brain's `partner-proactive` record is written when its line is sent, after any wait for
  quiet. A researcher's event is timed by when the researcher finished, from the orchestrator's list. Other events use
  the record's own time, which leaves out that wait.
- **Faults.** Only a second stack started with faults on answers them (`MICROMIND_BENCH_FAULTS=1`, never in a
  packaged build).
