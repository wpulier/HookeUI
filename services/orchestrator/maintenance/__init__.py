"""Capability maintenance (H13; ADR 0025; ticket 59's MT1): an entry's later updates discovered, prepared beside the
active generation, proven, shown and promoted under the policy a person recorded for it, with pins that survive restarts
and rollback. AGENTS.md beside this file describes the part; the modules:

- policy.py: each entry's recorded policy (manual by default, review, auto, pin) and what it allows now;
- discover.py: a newer commit or generation from the entry's own source, with backoff;
- compat.py: what in the candidate's declared facts only a person can accept;
- prepare.py: one attempt (obtain, files, build, proof, and under auto promotion), run by worker.py's process;
- promote.py: promotion, its first check, rollback and Undo, each an appended decision;
- pins.py: durable holds on generations;
- status.py: the update state and its words, for every surface;
- worker.py: the app's Maintainer thread and its low-priority worker process;
- commands.py: the studio's maintenance-* commands.

Nothing in it runs on the event loop or imports a capability's code.
"""
