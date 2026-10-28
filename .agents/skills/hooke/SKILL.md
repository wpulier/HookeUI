---
name: hooke
description: Use the researcher's Hooke app (HookeUI, a local microscopy and scientific-imaging workspace on their Mac) through its `hooke` command - see what it can do here, run its guided example, start analyses and follow them, and open results in the Hooke window. Use when the researcher asks you to do something "in Hooke", with their microscope images or Hooke Projects, or to connect to Hooke.
license: MIT
compatibility: macOS 14+ on Apple silicon, with the Hooke app (HookeUI) installed from hookeui.org.
metadata:
  version: "1"
---

# Working with Hooke

Hooke is a local app on the researcher's Mac. You use it through `hooke`, a command inside the app. It talks only to
that app, through Hooke's door for agents, with no Hooke account. Some of what Hooke can do runs on this Mac and some
uses cloud models the researcher set up; `hooke tools` says what each uses. Everything you start is saved in a Hooke
Project, recorded as done by you, and visible to the researcher in the Hooke window.

## Find the command

- Stable: `/Applications/HookeUI.app/Contents/Helpers/hooke`
- Beta: `/Applications/HookeUI Beta.app/Contents/Helpers/hooke`

If both are installed, ask the researcher which one they use. Call it by its full path, and quote the path because it
may contain a space.

Pass `--agent <claude-code|codex|cursor|other>` on every command: it says which connection is yours. Below, `hooke`
means the path with your `--agent`.

## Start every session the same way

1. `hooke status --wait --json` opens Hooke if needed and says whether you're connected.
   - A first launch asks the researcher to click **Download and Open** in the Hooke window. Tell them, and wait.
2. If you aren't connected, run `hooke connect`.
   - It prints a code. Tell the researcher that Hooke shows a dialog with the same code, and to click **Connect** if it
     matches.
   - The connection is kept; you don't connect again in later sessions.
   - If the researcher disconnected you in Hooke, `hooke` says so. Connect again only if they ask.
3. `hooke tools --json` lists what Hooke can do here: each capability's inputs, outputs, parameters (`inputSchema`),
   exact version (`generation`) and state.
   - `available` runs now. `setup` names its next step. Read this list rather than assuming what Hooke can do; it
     changes as the researcher adds capabilities.

## Doing work

What Hooke can do differs on every Mac: it is whatever this researcher has set up, built in or added, local or cloud.
Never assume a capability exists. Read `hooke tools --json` and choose from it by its `task`, `inputs`, `outputs` and
`state`.

- **Bring in their file:** `hooke open '<full path to their image or video>'` adds it to Hooke's library and opens it
  in a Project of your own. You work there, never in the researcher's open Project.
- **Run a capability:** `hooke run <id> '<json args>' --key <request key>`.
  - Use the `id` from `hooke tools`, and make the arguments match its `inputSchema`.
  - It runs on what your Project has open, follows the work to its end and prints the outcome: what it found, a few
    preview rows when it measured something, and an **Open in Hooke** link.
  - `hooke example` runs the guided example (Hooke's own sample, in its own Project).
- **When Hooke says `hooke-busy`,** the researcher has their own work open in Hooke, or Talk is on. Hooke never
  changes their screen for you. Tell them, and ask them to open a new empty Project or close Hooke's window.
- **A capability whose `state` isn't `available`** says why and what its next step is (`next`). Pass that on; setting
  capabilities up is the researcher's choice, made in Hooke.
- **Request keys.** Use a new key for each new request, and the same key to find a request again after an
  interruption. The same key with different arguments is refused, never run twice.
- **If you get cut off,** run `hooke operations` and `hooke operation <id> --follow`. Don't start the work again.
- **Interrupted work.** An operation that says `interrupted` stopped when Hooke did. Say so, and what it left; start it
  again only with a new key and the researcher's OK.
- **Cancelling.** `hooke cancel <id>` asks work to stop. It has stopped only when the operation says `cancelled`.
- **Showing a result:** give the researcher the Open in Hooke link, or run `hooke open '<page>'`. `shownInHooke: false`
  means it is saved but nobody has seen it yet. Say that, not "it's on screen".

## Rules

- **Say only what Hooke confirmed.** Report counts, measurements and states as `hooke` returned them. A refusal comes
  with `says` and `next`: pass both on and don't work around it.
- **Little data comes back by default.** Outcomes carry ids, counts, columns and a few preview rows. What you read may go
  to your provider, so ask the researcher before requesting full tables or images.
- **Ask before changing their Mac.** Ask before installing or downloading anything Hooke offers. The door gives you no
  way to read their API keys or change Hooke's settings.
- **Names are best guesses.** Bright regions, outlines and counts are measurements, not identifications. When Hooke
  names something, say it is Hooke's best guess.
- **The researcher stays in charge in Hooke.** They can see, change or disconnect what you did in the Hooke window.

## Updating

`hooke skill update` refreshes this skill from the installed Hooke. It doesn't touch the connection, and it never
replaces a folder named `hooke` that Hooke didn't install. `hooke status` says when the skill is older than Hooke asks
for.
