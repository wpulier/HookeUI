# Benchmarks

The measurements behind Hooke's choices: which model or method does each job by default, and why. Every comparison
behind a default is kept twice:
- as a **write-up** here (`<topic>-<date>.md`): what was compared, how, the tables, and what it means;
- as **data** in `sets/`, in one shape anyone's results can take.

A website's Benchmarks page can be built from `sets/` alone.

## The benchmark-set shape

`contract/benchmarks/benchmark-set.schema.json` (`benchmark-set/1`). A set is one comparison of ways to do one job, run
at one time. It holds:
- **What it measures:** `measures`, and the Hooke job it compares (`job`: its name and its setting, e.g. `fast` for Quick
  decisions).
- **Who ran it and where:** `runBy` (`maintainer`, `user` or `agent`, with an optional name and contributor id),
  `environment` (Hooke's version or commit, the machine, the conditions such as load or parallel runs), and `ran` (when;
  `sameTime` when every arm ran together, since provider latency drifts by the hour).
- **The benchmark:** its name, method, code and command, how many cases and repeats, and its inputs, with whether they
  are public and under what licence.
- **The arms:** each model, option or method compared, with its option id, model, provider, where it runs, and its role
  (the default before or after, a candidate, a reference).
- **The metrics:** each with its unit and which way is better.
- **Each arm's results**, and optionally each case's: right of tries, what an arm did when it missed, and per-case values.
- **What it led to:** what it cost (`spend`), its `caveats`, the `decision` it informed (the setting, from and to, and
  the change), and `links` to the write-up, the pull request and the code. Raw data the maintainers keep is linked as
  not public.
- **Its terms:** the `licence` the numbers are published under, and `privacy`: a set holds numbers and words only, never
  pictures or personal data.

A set says who ran it; how far it is trusted is for whoever publishes it to decide, never claimed in the set.
`python -m bench.benchmark_sets check [FILE ...]` (from `services/`) checks a set against the shape and itself: every
arm and metric it names exists, and nothing is right more often than it was tried. It checks every set here by default,
and `tests/test_benchmark_sets.py` does the same in `make check`. The checker is `orchestrator/door/schema.py`: small,
with no dependency, so another service can check sets with the same files and code.

Adding a set: write it in this shape as `sets/<date>-<topic>.json`, run the check, and list it below. For the fast bench,
`bench.benchmark_sets.fast_bench_set` turns its rows into the set's results and cases. A user's Hooke or an agent writes
the same shape for its own comparisons.

## The sets

| Set | Job | What it measures | Write-up |
| --- | --- | --- | --- |
| `2026-11-05-quick-decisions-fast-bench` | Quick decisions | From the workspace notes alone, whether Opus 5.5 fast, Gemini 3.8 Flash and Sonnet 5.5 know when to act, answer, wait for a look or do nothing; time and cost a decision | `quick-decisions-2026-11-05.md` |
| `2026-11-05-quick-decisions-fast-bench-resample` | Quick decisions | Whether Opus 5.5 fast's and Gemini 3.8 Flash's misses on five requests are systematic (4 more tries each) | `quick-decisions-2026-11-05.md` |
| `2026-11-05-quick-decisions-will-drive` | Quick decisions | Driving Hooke by voice with each model planning the hand-offs: steps done, judged quality, false claims, receipts, speed, cost | `quick-decisions-2026-11-05.md` |
| `2026-11-03-guide` | Guiding your hands | Whether three models and the rule centre and focus a simulated slide, how fast, in how many lines | `guide.md` |
| `2026-10-29-vision-grounding` | Seeing | Whether five vision models box the thing a question is about on real leaf frames, and how fast | `vision-2026-10-29.md` |
| `2026-10-29-vision-find-bccd` | Seeing | Recall and precision of five vision models outlining blood cells on BCCD smears | `vision-2026-10-29.md` |
| `2026-10-29-vision-naming` | Seeing | How often five vision models name microscopy pictures exactly and by kind, on a click and on the running look; calibration, speed, blank discs | `vision-2026-10-29.md`, `naming.md` |

Not yet sets: the voice journeys before 5 Nov (`voice-journeys.md` describes the method; their runs are kept by the
maintainers), and the older numbers in the option notes of `services/orchestrator/options.py` (for example the
2026-10-06 fast bench of 40 cases), whose rows were not kept.

The sets' numbers are published under CC-BY-4.0. That is the maintainers' choice for these sets, and a set from someone
else states its own.
