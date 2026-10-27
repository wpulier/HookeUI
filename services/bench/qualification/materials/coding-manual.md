# Coding manual: errors, interventions and H1b adjudication

Coders work from the recordings after the session. They know which app is on screen (that can't be hidden) but not the
analysis results (design §7.3).

## Error codes (reported, no decision)

| Code | Meaning | Example |
| --- | --- | --- |
| E1 slip | the right intention, executed wrongly, and noticed | a mistyped value, corrected |
| E2 mistake | a wrong function or approach for the goal | opens a filter when asked to set a display range |
| E3 wrong state left | a wrong scientific state the participant doesn't notice before "done" | labels changed outside the edit regions |

Code each occurrence with its time. An E3 the oracle catches makes the attempt incorrect anyway. The code is
descriptive.

## Reliability

Two trained coders independently double-code 20% of attempts, chosen at random by the reviewer and balanced across
apps, tasks and sites. Cohen's κ on the error codes must be at least 0.80. Below that, retrain and recode **all**
attempts, and report both κ values.

## Intervention levels

Check the moderator's log against the recording. A disagreement is resolved by the recording: the level of what was
actually said or done.

## H1b adjudication (an app-caused wrong accepted result)

For every attempt flagged "H1b?", every Hooke attempt the oracle scored incorrect after the participant said "done",
and a 5% random sample of the rest, two reviewers decide independently:

- **App-caused** if the app showed success or a state that differs from what it computed or saved, given the
  participant's recorded actions. Examples: the screen shows the edit but the saved file lacks it; a measurement table
  that doesn't match the labels shown; "saved" shown when nothing was written.
- **Not app-caused** if the participant's own actions explain the result.

Disagreement counts as app-caused (§6). A single app-caused Hooke attempt fails its task. Record each decision, the
evidence (recording times, log lines, file hashes) and the reviewer.
