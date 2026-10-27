# 17. A session is the project, not the day

**Context.** Until Oct 12 a session was a calendar day:
- the mind's memory, its conversations, its researchers and its work hung off `sessions/<day>/journal.jsonl`;
- midnight and "start today afresh" ended a session;
- yesterday's researchers became read-only;
- the header's control was labelled "Session (one per day)".

That broke three ways the user works (2026-10-11):
- one thing in the morning and something else at night are two projects, but they shared one day;
- a prepared slide that keeps comes back the next day, but yesterday's session could not be continued;
- two projects on the same specimen are still two projects, so the specimen can't be the session either.

**Decision.**
- **A session is the project.** It has:
  - a name;
  - its conversations (topics, as before);
  - what was seen in it (subjects);
  - its memory (summaries, settled facts);
  - its work: researchers, cell populations, motion tracks, bookmarks, snapshots and results.
- **A day is only a calendar.** The research spending cap and the performance logs stay per calendar day.
- **A run** (one start of the app) keeps what it always kept: live outlines, positions and following.
- **Ids:** a session's id is `YYYY-MM-DD`, or `YYYY-MM-DD.N` for the Nth started that date (`day/sid.py`, the only parser).
  - Every day journal from before this decision *is* a session whose id is its date. No file moves.
  - A record with no `session` belongs to the session of its date (`sid.session_of`).
- **Its journal is the only store read back**, as ADR 0001 said of the day's: `sessions/<sid>/journal.jsonl`. The list of
  sessions is read from those journals (`day/catalogue.py`), never kept as a second store.
- **New sessions start only when the user asks:** the header, the sessions rail, or by voice or chat (`new_session`).
  - The app may suggest one when the running look has confirmed a different specimen, it is not one the session has
    seen, and the session has been idle for 3 hours. It never starts one itself.
  - Midnight does nothing.
  - "Start today afresh" becomes "New session".
- **On open,** the app is in the last session used (the pointer in `preferences.jsonl`).
- **Switching** clears the live view as starting afresh did. The session's conversations, memory, researchers
  (continuable), evidence, bookmarks and snapshots come back as history; its live outlines do not, and the app says so in
  one line.
- **What is yours across sessions:** your settings (`preferences.jsonl`, kept until changed) and your taught labels.
  Seen-before looks across every session and says which one.
- **What is the session's:** its settled facts. Recall searches this session, or another one you name.
- **Work started in one session finishes in it.** A researcher keeps working after a switch; its result is filed in its
  own session's journal (`SessionStore.elsewhere`), and the session you are in gets one line.

**Cost.**
- Every per-day store (evidence, tracks, results, bookmarks, identity, taught, seen, research) now tags its records with
  their session and is read across the dates a session touched. Ids are new both for the session and for the date
  folder (`day/ids.py`), so two sessions on one date never share an `e`, `c`, `m`, `d`, `b` or `i` id.
- Research spend is charged to the calendar day a run happens on, wherever its researcher's folder is.
- Prompts say "this session" where they said "today"; their goldens changed.
- Amends ADR 0001 (the journal per session, not per day) and ADR 0004 (the cap's amount is kept until changed; spend
  counts per calendar day).

## Vocabulary amendment — 2026-10-16

The user approved **Project** as the visible name for the existing session and **Tab** for a named workspace arrangement inside it. A project owns its Data, work, notes, conversations and tabs. Top navigation says open projects; the workspace arrangement strip says tabs. Closing a placement or removing a tab preserves retained Data; closing an open project does not delete it.

This is a presentation-language amendment. Session IDs, journal paths, command names, resource ownership, `scope: session`, layout `views` fields and existing `#sessions` links remain compatible. Existing user-written titles and retained content are not rewritten. “View” still describes the specimen image or field of view; browser and voice sessions retain their technical meaning. No data migration or additional ownership layer is introduced.
