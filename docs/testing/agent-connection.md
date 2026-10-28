# Testing the agent connection

How to try "Connect your agent to Hooke" from `dev`, and what to check. The decision is
[ADR 0026](../adr/0026-outside-agents-connect-through-one-door.md); how the door works is
[`services/orchestrator/door/AGENTS.md`](../../services/orchestrator/door/AGENTS.md).

## What it is

A researcher's own coding agent (Claude Code, Codex, Cursor) uses the Hooke app on their Mac, with no Hooke account.

- **The door.** The orchestrator serves `/api/agent/v1/` on loopback. It lists what Hooke can run here, starts work
  as operations, and reports their progress and results. It names no capability itself: the list comes from the
  app's capabilities at run time (`services/orchestrator/door/listing.py`). Only tools declared runnable for agents in
  `services/orchestrator/agent_runnable.py` are listed, and only work that stays on this Mac runs.
- **The connection.** `hooke connect` asks the running app to connect. The researcher confirms once in Hooke's own
  dialog, which shows a code to match. The command then receives a token and keeps it in the Keychain. Settings →
  Connected agents lists each connection, with Disconnect. A disconnected agent stays disconnected until it is
  confirmed again.
- **The `hooke` command.** A small Swift program (`packaging/macos/hooke/main.swift`) inside the app at
  `Contents/Helpers/hooke`. Every command takes `--agent claude-code|codex|cursor|other`. It opens Hooke when it isn't
  running, waits through a first launch or an update, and reconnects after a restart.
- **The skill.** `.agents/skills/hooke/SKILL.md`, installed by `hooke connect` into the agent's skills folder
  (`~/.claude/skills/hooke` for Claude Code), so later sessions know how to use Hooke.
- **The playbook.** The page an agent follows from the first paste: `hookeui.org/get-started-beta.md` for Beta. Its
  source is in the private site repository (hooke-web `site/get-started-beta.md`).
- **What an agent may do.** It works only in Projects it made: `hooke open <file>` adds a picture or video to the
  library and opens it in a new Project named for the agent, and `hooke run <capability> --project <id>` runs there.
  What it makes is recorded as made by the agent (Data's **Made by**). It can't work in the researcher's Projects,
  change settings, start researchers, or use cloud models or API keys. While the researcher's window shows their own
  work, or Talk is on, Hooke tells the agent to wait.

## Two ways to test

### A. The published Beta (the real path)

Beta 0.6.0 (build 4740, from `dev` at `ffa94d1`) has all of this. Install HookeUI Beta from hookeui.org (or let an
installed Beta update itself), open Claude Code in Terminal, and paste:

> Connect me to Hooke. Fetch https://hookeui.org/get-started-beta.md and follow it.

Run the agent in Terminal on the Mac itself, not over SSH. An SSH session can't reach the login Keychain, and
`hooke` says so.

### B. From a `dev` checkout

```sh
git fetch origin && git checkout dev && git pull
make setup
HOOKE_DEV_CONFIRM=1 make start
```

`HOOKE_DEV_CONFIRM=1` lets the browser studio's own confirm sheet answer a connection. A source checkout has no Mac
app to sign the answer, so without the flag every connection is refused. Open the Studio URL `make start` prints.

Build the command and point it at the checkout. It finds no app around it, so it uses `HOOKE_DOOR_URL` (loopback
only) and keeps its token under the Keychain service `org.hookeui.agent.development`:

```sh
swiftc -O -swift-version 5 packaging/macos/hooke/main.swift packaging/macos/HookeUI/AgentShared.swift -o /tmp/hooke-dev
export HOOKE_DOOR_URL=http://127.0.0.1:8770 HOOKE_SKILL_SOURCE="$PWD/.agents/skills/hooke"
/tmp/hooke-dev status --agent claude-code
/tmp/hooke-dev connect --agent claude-code     # confirm in the studio's sheet; the code must match
/tmp/hooke-dev example --agent claude-code
```

To hand it to an agent, tell it the command's path and to read `.agents/skills/hooke/SKILL.md`.

## What to check

1. **Connect.** One confirm, with the same code in the command and the dialog. The skill lands in the agent's
   skills folder.
2. **The example.** `hooke example` finds about 271 bright regions in the bundled image and prints an **Open in
   Hooke** link that opens that result. Data shows it made by the agent.
3. **Its own Project.** `hooke open <picture>` makes a Project named for the agent. `hooke run <capability>
   --project <id>` works there. `hooke tools` lists what can run.
4. **Refusals.** Running in the researcher's Project or the example's Project is refused. So are a cloud model and a
   tool the agent isn't allowed. Each refusal says what to do next.
5. **Request keys.** Running again with the same `--key` finds the same operation and never runs it twice. The same
   key for a different request is refused.
6. **Restart.** Quit Hooke while the agent waits: the command opens Hooke again and reports work cut off as
   `interrupted`. `hooke operations` still lists earlier work.
7. **Disconnect.** Settings → Connected agents → Disconnect. Every later command says "This agent was disconnected in
   Hooke". `hooke skill update` doesn't reconnect it.
8. **Mac privacy.** A file in Desktop, Documents or Downloads that macOS keeps from Hooke is refused in words that say
   what to do, never as a raw error.

## Known limits

- With no Hooke window open, `hooke open` switches the app to the agent's Project; the researcher sees it next time
  they open the window.
- Nothing an agent can run today lasts long enough to still be running during an update, so "work cut off by an
  update" has been checked only by quitting Hooke instead.
- Both channels installed: checked only against Stable 0.5.2, which predates agent connections. Beta's links and
  `hooke` don't reach Stable.
- After a restart, the walkthrough card can still say "Waiting for the outlines to appear" with the outlines drawn.
  Retry and Skip both work.


## Independently named connections (source qualification)

The CLI supports `--connection lead` and `--connection reviewer` on every command,
or `HOOKE_CONNECTION`. Omission preserves the existing unnamed connection. Names
are exact, 1–64 ASCII letters/digits/dots/underscores/hyphens, starting alphanumeric.
`--label` changes the displayed name at connection time, not the credential selector.

Connect each separately and confirm its own matching code:

```sh
/tmp/hooke-dev connect --agent claude-code --connection lead
/tmp/hooke-dev connect --agent claude-code --connection reviewer
/tmp/hooke-dev status --agent claude-code --connection lead --json
/tmp/hooke-dev status --agent claude-code --connection reviewer --json
```

Check distinct returned connection IDs, independent operations with the same request
key, and independent Disconnect. The unnamed connection must still work. A missing
named connection must refuse rather than use another credential. These selectors do
not grant collaboration or allow access to another connection's Project. Named
connection support in source does not establish that an installed Beta includes it.
