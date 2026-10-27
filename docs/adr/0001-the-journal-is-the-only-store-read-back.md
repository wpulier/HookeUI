# 1. The day's journal is the only store read back

**Context.** Until Oct 5, a conversation lived in two stores, cleared separately. The day's
journal held the turns. The hub held what the chat showed, in memory (`chat_history`,
`chat_by_thread`) and in `services/logs/conversation.jsonl`. Each caller worked out the
conversation of the lines it wrote in its own way: a scan of turn ids, a variable in the voice
route, the researcher's origin, or none at all. Narrated results, the user's own lines and the
voice's echo landed in whichever conversation was open.

**Decision.** Every chat line goes through `orchestrator/dialogue.py`. A line is filed with one
conversation: the one its request was asked in (`Asked`, fixed when the request starts), else
the one its researcher was started in, else the active one. It is journaled as a `line` event
next to the turns. A reload or restart replays the journal. `conversation.jsonl` is still
written for `make save-logs`, but it is read only once, to import a day from before lines were
journaled.

**Cost.** The journal is larger, since a line repeats its turn's text with how it was shown.
