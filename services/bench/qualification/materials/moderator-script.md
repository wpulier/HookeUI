# Moderator script (H14 study)

For moderators trained on this script who are not on the Hooke implementation team (design §10). Say the quoted lines
word for word. This script follows the frozen [study design](../h14-study-design.md); where they differ, the design
wins and the difference is reported to the reviewer. Its SHA-256 goes into the binding manifest
(`moderator_script_sha256`).

## Before each session

1. Check the participant's code and sequence on the sealed allocation sheet. Open the run sheet for that sequence
   ([session-run-sheets.md](session-run-sheets.md)).
2. Machine:
   - Session 1: a new macOS user account, made from the site's reset procedure, with neither app installed (T0 is
     cold).
   - Session 2: that participant's account from session 1.
   - Restart the Mac. Turn off notifications, Spotlight indexing of the data folder and Time Machine.
3. Copy the task packages for this session from the read-only master. Check them against `PACKAGE.sha256` with
   `shasum -a 256 -c PACKAGE.sha256`. Any mismatch stops the session.
4. Start the screen recording (30 fps, both apps' windows, audio) and the logging proxy (T0 and T13 only).
5. Have ready: the attempt log ([attempt-log.csv](attempt-log.csv)), a stopwatch (the timestamps that count come from
   the recording), and the Hooke-only checklists below.

## Opening (session 1)

"Thank you for coming. We're comparing two programs for looking at microscopy images. We're testing the programs, not
you. If something is hard, that tells us about the program. I'll give you one goal at a time on a card. Work the way
you normally would. Think aloud if you're comfortable doing so. I can't show you how to do things, but you can ask me
to repeat the goal. Say 'done' when you've finished a goal, or 'I give up' if you want to stop. Each goal has a time
limit; I'll tell you if you reach it."

Then play the orientation video for the first app ([orientation-scripts.md](orientation-scripts.md)). Play nothing
else about either app.

## Each attempt

1. Prepare the start state on the card's row of [task-cards.md](task-cards.md), from that variant's package. Do it
   before handing over the card, out of the participant's sight where possible.
2. T5: run `sudo purge` just before the attempt (cold OS file cache).
3. Hand over the card and say: **"Here is your next goal. Begin when you're ready."** The attempt starts at the first
   frame after the participant starts.
4. Intervene only as the levels allow, and log each one with its time:

| Level | What you may do | Log as |
| --- | --- | --- |
| L1 | At most twice: **"Please keep going."** or **"What are you trying to do now?"** | L1 |
| L2 | A directional hint, only if the participant has been stuck for 2 minutes and asks: **"Look at the controls for that data."** | L2 |
| L3 | Show or do a step | L3 |
| L4 | Anything outside the app's ordinary interface: a terminal, editing a file or setting, killing a process, reinstalling. In a Hooke attempt, the participant using a terminal or developer tool also counts as L4 | L4 |

   - A third neutral prompt is logged as L1 and makes the attempt not UCC (interpretation I5).
   - Never name a menu, a control or a shortcut at L1.
   - In a napari attempt, a terminal is ordinary only for T0, following napari's own installation page.
5. The attempt ends at **"done"**, **"I give up"**, or the cap (say **"That's the time for this goal."**).
6. **After it ends, never before**, collect the observation:
   - **napari:** run the state-dump script on the open viewer (`bench.h14_state_napari`, to be written at binding),
     then copy the saved files.
   - **Hooke:** copy the saved Project and its results.
   - **T11:** copy the save folder after Quit and before reopening (I2).
7. Ask the Single Ease Question (questionnaires.md) and log the answer.
8. Write the attempt-log row. Don't score anything: `bench.h14_scoring` scores later, before anyone looks at results.

## Lost attempts

Mark an attempt **lost** only for a reason unrelated to either app: power, hardware, fire alarm, or the participant
leaving for a reason of their own. If either app hung, crashed or needed repair, the attempt is a **failure** of that
app, never lost. Write what happened in the notes column.

## Possible app-caused wrong results (H1b)

If the app showed success, or showed a state, that seems to differ from what it computed or saved, write
**"H1b?"** and the time in the notes column. Don't tell the participant. Two reviewers adjudicate later from the
recording and logs, independently ([coding-manual.md](coding-manual.md)).

## Hooke-only tasks (session 2, block 3): moderator checklists

Tick each criterion only if it is true at the end of the attempt. `bench.h14_scoring.U_CRITERIA` uses these names.

| Task | Criteria |
| --- | --- |
| U1 | both_versions_kept · chosen_version_accepted · nothing_else_changed |
| U2 | refusal_understood (the participant says why, in their own words) · valid_input_used |
| U3 | outputs_match_reference · rollback_restores_earlier_generation |
| U4 | successful_outputs_kept · only_failed_items_rerun |
| U5 | candidate_reviewed · previous_generation_active · saved_work_unchanged |
| U6 | package_prepared_through_offered_path · data_reopen_exactly · nothing_deleted |
| U7 | status_reported_matches_record · missing_requirement_matches_record |

## Closing (session 2)

Give the SUS for each app and the raw NASA-TLX for each block (questionnaires.md). Say: **"Thank you. That's
everything. Please don't describe the tasks to anyone who might take part."** Stop the recording, and hash and upload
it as the site's procedure says.
