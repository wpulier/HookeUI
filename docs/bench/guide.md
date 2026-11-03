# Guide bench

Guiding the user's hands (`orchestrator/guide.py`, option GUIDE) in a closed loop: `services/bench/guide_bench.py`.

A synthetic eyepiece view of a slide (the bundled `assets/examples/human_mitosis.png`, CC0, as brightfield, mirrored
3 x 3), with the phone's sub-pixel shake, sensor noise and JPEG at the bridge's quality. Every frame goes through the
hub's own analysis (the eyepiece disc, the slide's motion and epoch, sharpness), as the live app does. A simulated
person follows each line: 0.6 s to react, then moves the slide the asked way by 60-140% of the asked amount at hand
speed, and turns the fine focus a step per focus line (half a step for "a touch"). Each run knows the truth: where the
target is and how far the focus is from sharp. A run is **right** when the guidance ended done with the target within
0.15 of the eyepiece radius of the centre and, when sharp was asked, the blur at most 0.9 px (about 85% of the
sharpest, as the hub measures it). Model arms run in real time, so a line takes as long as the model takes to answer
it; the rule's take no time.

The eight cases: the target off in each direction, near (0.45 of the radius) and far (0.8); starting out of focus
either way; the phone turned a quarter on the eyepiece (the directions must be learned from the user's moves); and
centred only (no focus).

## 2026-11-03

Gemini 3.8 Flash is the fast pass's default model (option BRAIN_FAST), which GUIDE "model" asks.

| Judge | Runs | Right | Median time to done | Median lines | Judgment p50 | Overrides |
|---|---|---|---|---|---|---|
| model, Gemini 3.8 Flash | 16 (8 cases x 2) | 16 | 23.3 s | 9.5 | 1.6 s | 2 (a "done" refused) |
| model, Gemini 3.5 Flash-Lite | 16 | 15 | 40.3 s | 15 | 0.8 s | 19 |
| rule | 24 (8 cases x 3) | 24 | 9.3 s | 8.5 | 0 | 0 |

- **Gemini 3.8 Flash** got every case right, including the turned phone. Its answers take 1.6 s at the median (spikes
  of 4-5 s). Two of its "done" claims were refused by the code's check (sharpest not yet bracketed) and it was asked
  again.
- **Gemini 3.5 Flash-Lite** answers twice as fast but needed half as many lines again. It missed one far case (it
  ran out of the bench's 90 s), and its "done" was refused 19 times.
- **The rule** is fastest because it answers at once; it is a non-default option. Its numbers are the bench's ceiling
  for a judge with no latency, not a measure of judgment on real pictures.
- Before three fixes to the facts and timing (the same day), Gemini 3.8 Flash got 1 of 4 right and Flash-Lite sent a
  target 11 radii away. The fixes: (1) the facts gave where the target is seen beside which way to move the slide,
  and the models moved by where it was seen (backwards on a compound microscope); now only `moveSlide` is given for
  moving, and the prompt says not to work out a direction from where it is seen. (2) The relation between the slide
  and the picture was learned from moves that overlapped and flipped; now a move counts only when the slide was still
  as its line was said and still again after. (3) A moment raised while the model was answering judged again before
  the hands could follow the line said; pending moments are cleared once a line is said. A fourth change made "stop"
  the app's own reflex: a hand crosses the centre faster than any model answers.

Runs: `MICROMIND_KEYS_FILE=... .venv/bin/python -m bench.guide_bench --judge model --model gemini-3.8-flash --seeds 2
--trace FILE` (each judgment's facts, answer and check in FILE); the rule with `--seeds 3`.

End to end on the simulated camera (`make sim-test SIM_ONLY=guide`, `services/sim/guided.py`): the camera bridge
plays a virtual microscope made of the qualified leaf recording's labelled frame, a stoma off to one side and out of
focus; the rule centred it to 0.03 of the radius with blur 0.83 px in about 30 s, every line short and plain.
