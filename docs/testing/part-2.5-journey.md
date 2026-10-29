# Part 2.5 journey: one Project, every entrance

This is the acceptance check for Part 2.5 ("get the core right"). It is a check, not a claim of qualification. A run
counts only on the candidate it names, with its evidence kept.

The journey proves one thing end to end: a real Project feels coherent. You ask naturally, Hooke uses the right
capability on the right retained input, shows the actual result, and you, a researcher or another agent continue from it
later without being told what happened.

It comes from a live voice session on 2026-10-28 that went wrong in ten ways:
- "Nothing's outlined" after almost every reply;
- one outcome said three times;
- "the table is up" when it wasn't, then "quit and reopen the app";
- "we only have the original image" while a measurements table and findings existed;
- an empty tab;
- two researchers from one request;
- a stopped researcher's status instead of the running one, and talking over the speaker;
- a tab when a Project was asked for;
- a video model for an animation of a table;
- "enabled" heard as "chosen".

Each of those is a case on the benches beside this journey.

## Setup

- **A Project that already has history:** the nuclei sample (`assets/examples/human_mitosis.png`) on the stage, with at least one earlier result (an outline layer or a measurements table) and one finished researcher.
- **Models:** the default choices, and the default voice.
- **A studio window** showing the Project, in front.

## The journey, in the words to say

| # | Say or do | What must happen |
|---|---|---|
| 1 | Open the Project. | It opens where it was left. Its earlier results and researchers are listed in Data and Researchers. |
| 2 | "What am I looking at?" | It describes the actual specimen (stained nuclei, some dividing) and mentions the earlier findings it has. |
| 3 | "Outline the nuclei." Then "Highlight the biggest one." | Outlines appear with a count, said once. The biggest one is marked where you can see it, or shown in the magnifier with a plain reason. |
| 4 | "What data do we have on these?" | It names the measurements table(s) and the earlier findings, with where each came from. Never "only the original image" when more exists. |
| 5 | "Put the table in a new tab called Measurements." | A Tab called Measurements opens, in front, with the table fitted in it. It says so once, after the screen shows it. |
| 6 | "Make a researcher that explains these measurements and animates them." (a pause in the middle is fine) | One researcher starts. It reads that table, writes its findings, and makes a chart or animation derived from the measurements. The output is kept in the Project. No video model unless asked for by name. |
| 7 | "What's our researcher doing?" | It answers about the running researcher (or the one just discussed), by number, without talking over you. |
| 8 | Quit and reopen Hooke. | The Project, the table, the Tab and the researcher's output are all there. |
| 9 | From Claude Code or Codex: "Open my Hooke Project. What's in it? Run one follow-up on the measurements." | The agent finds the same table and findings through the door without being told, runs one follow-up through the saved workflow, and the result appears in the Project with the agent named as its author. |
| 10 | "What models are powering you right now?" | It names each role's model (brain, voice, eyes, quick decisions, outline, cells, research), says local or cloud, and where to change it. |

## The second pass

Run it again in a different order and different words (for example "show me the numbers" for 4, "spin up an analyst
for this" for 6). Add:
- one interruption mid-answer;
- one tool that fails, where the failure is said once with its cause and the next useful thing to do;
- one switch to another Project while the researcher runs, then back.

A memorised happy path doesn't pass.

## What blocks acceptance at any score

- **A false "done":** a claim that something is on screen, saved or finished when it isn't.
- **An outcome said twice** for one request.
- **A second researcher** started by one request.
- **The wrong Project, Tab or source.**
- **Telling the user to restart the app** to see a result.
- **Lost work** after the restart in step 8.

## Evidence, kept as three separate kinds

1. **Recorded audio** (`services/bench/voice_journeys`, macOS voices): repeatable regression, run on every candidate.
2. **Acoustic replay:** the same recordings played through this Mac's speakers into its microphone with the app live. It checks echo and self-interruption.
3. **A person speaking in the room:** the only evidence for how it feels.

For each kind, report observed counts: false "done" claims, duplicate outcomes, interruptions and talk-overs, failures,
time to first audio, and steps passed. Never report "zero" as a promise; report what was observed and how many times it
ran. Step 9 on an installed Beta needs a confirmed named connection, which only the person can confirm; on a source
instance it can run through the door's development connection on an agent-owned Project.

Say which runs used test footage or a still image, and which used the live camera.
