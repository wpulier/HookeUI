# Naming bench

Written by `python -m bench.naming.run`; with `--append` a run adds its own section here.

## Claude Opus 5.5, its fast mode and Gemini 4 Argon beside the Gemini defaults (2026-10-29) (run 20261029-043115)

180 pictures, conditions right, none, wrong, grey, magnifications x1, 1 repeat(s). `bench/results/naming-20261029-043115.json`.
The Anthropic credit ran out from row 6,702, so the Claude click rows have fewer than 180 answers; the comparison on the
pictures every option answered, with paired tests, is in `vision-2026-10-29.md`.

| Option / path | Condition | n | Exact | Coarse | Not sure | p50 ms | p90 ms | Asked closer | Rounds (mean) |
|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5-fast/identify | right | 161 | 56% | 74% | 22% | 1996 | 2715 | 0% | 1.00 |
| claude-opus-5-5-fast/identify | none | 160 | 53% | 74% | 21% | 1985 | 2731 | 0% | 1.00 |
| claude-opus-5-5-fast/identify | wrong | 161 | 55% | 74% | 19% | 2180 | 2772 | 0% | 1.00 |
| claude-opus-5-5-fast/identify | grey | 161 | 0% | 0% | 42% | 1518 | 1844 | 0% | 1.00 |
| claude-opus-5-5-fast/sight | right | 180 | 62% | 86% | 0% | 2132 | 2497 | 0% | 1.00 |
| claude-opus-5-5-fast/sight | none | 180 | 60% | 83% | 0% | 2096 | 2447 | 0% | 1.00 |
| claude-opus-5-5-fast/sight | wrong | 180 | 57% | 84% | 0% | 2081 | 2446 | 0% | 1.00 |
| claude-opus-5-5-fast/sight | grey | 180 | 0% | 0% | 0% | 953 | 1357 | 0% | 1.00 |
| claude-opus-5-5/identify | right | 150 | 55% | 79% | 19% | 5072 | 6778 | 0% | 1.00 |
| claude-opus-5-5/identify | none | 151 | 61% | 77% | 16% | 4998 | 6740 | 0% | 1.00 |
| claude-opus-5-5/identify | wrong | 151 | 55% | 77% | 20% | 5432 | 6778 | 0% | 1.00 |
| claude-opus-5-5/identify | grey | 151 | 0% | 0% | 50% | 3605 | 4167 | 0% | 1.00 |
| claude-opus-5-5/sight | right | 180 | 63% | 86% | 0% | 5218 | 5924 | 0% | 1.00 |
| claude-opus-5-5/sight | none | 180 | 62% | 87% | 0% | 5242 | 5879 | 0% | 1.00 |
| claude-opus-5-5/sight | wrong | 180 | 62% | 86% | 0% | 5190 | 5993 | 0% | 1.00 |
| claude-opus-5-5/sight | grey | 180 | 0% | 0% | 0% | 2186 | 2658 | 0% | 1.00 |
| gemini-3.5-flash/identify | right | 180 | 59% | 72% | 1% | 2289 | 2722 | 0% | 1.00 |
| gemini-3.5-flash/identify | none | 180 | 55% | 77% | 2% | 1868 | 2145 | 0% | 1.00 |
| gemini-3.5-flash/identify | wrong | 180 | 58% | 74% | 3% | 1848 | 2187 | 0% | 1.00 |
| gemini-3.5-flash/identify | grey | 180 | 0% | 0% | 32% | 2035 | 2494 | 0% | 1.00 |
| gemini-3.5-flash/sight | right | 180 | 51% | 83% | 0% | 2990 | 3440 | 0% | 1.00 |
| gemini-3.5-flash/sight | none | 180 | 54% | 86% | 0% | 2791 | 3246 | 0% | 1.00 |
| gemini-3.5-flash/sight | wrong | 180 | 56% | 83% | 0% | 2620 | 3158 | 0% | 1.00 |
| gemini-3.5-flash/sight | grey | 180 | 0% | 0% | 0% | 1657 | 2144 | 0% | 1.00 |
| gemini-3.8-flash/identify | right | 179 | 64% | 79% | 3% | 3534 | 7064 | 0% | 1.00 |
| gemini-3.8-flash/identify | none | 178 | 64% | 79% | 2% | 3446 | 6646 | 0% | 1.00 |
| gemini-3.8-flash/identify | wrong | 177 | 61% | 77% | 3% | 3376 | 7202 | 0% | 1.00 |
| gemini-3.8-flash/identify | grey | 180 | 0% | 0% | 0% | 3048 | 4700 | 0% | 1.00 |
| gemini-3.8-flash/sight | right | 180 | 55% | 87% | 0% | 5050 | 6666 | 0% | 1.00 |
| gemini-3.8-flash/sight | none | 180 | 55% | 83% | 0% | 5215 | 6500 | 0% | 1.00 |
| gemini-3.8-flash/sight | wrong | 180 | 53% | 84% | 0% | 5211 | 6944 | 0% | 1.00 |
| gemini-3.8-flash/sight | grey | 179 | 0% | 0% | 0% | 3169 | 4248 | 0% | 1.00 |
| gemini-4-argon/identify | right | 180 | 76% | 75% | 12% | 5338 | 12549 | 0% | 1.00 |
| gemini-4-argon/identify | none | 180 | 82% | 78% | 14% | 5021 | 9330 | 0% | 1.00 |
| gemini-4-argon/identify | wrong | 180 | 83% | 80% | 12% | 5167 | 10771 | 0% | 1.00 |
| gemini-4-argon/identify | grey | 180 | 0% | 0% | 19% | 4995 | 10667 | 0% | 1.00 |
| gemini-4-argon/sight | right | 180 | 73% | 83% | 0% | 10015 | 12724 | 0% | 1.00 |
| gemini-4-argon/sight | none | 180 | 72% | 84% | 0% | 9134 | 13652 | 0% | 1.00 |
| gemini-4-argon/sight | wrong | 180 | 63% | 84% | 0% | 8843 | 13611 | 0% | 1.00 |
| gemini-4-argon/sight | grey | 180 | 0% | 0% | 0% | 4238 | 9304 | 0% | 1.00 |
