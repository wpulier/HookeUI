# 7. The capability catalogue is the one list of what the app can do

**Context.** By 2026-10-06 the app had about 50 agent tools, 25 things the user does directly,
15 background processes, 6 kinds of researcher work and 26 option groups, plus models fixed in
the code. Nothing showed them together. The user missed abilities (motion, watching), could not
tell a bug from something the app cannot do, and saw a different list from the agent's. Each
place that named things kept its own copy (the tool list, the models popup's titles), and the
copies drifted.

**Decision.** `contract/capabilities.json` is the one list. A capability is one thing one actor
can do (you, the agent, on its own, a researcher). Its entry holds what people read (a title,
one line of what it does, an example to say) and references into the code: a tool, a studio
command or control, a background module, a researcher deliverable, and the model roles it
depends on. Everything else is worked out from the code, never copied: a role's options, tiers
and licences (`options.catalogue`), what the agent can change (`Tool.sets`), a fixed model's
name (read from its constant), and availability.
- `orchestrator/capabilities.py` checks the list against the code, and builds the one `wire`
  that both the Capabilities page draws and the agent's `capabilities` tool writes as text.
- `services/tests/test_capabilities.py` fails when a tool, command, deliverable or option group
  is in no capability, or when the list names something that does not exist.
- The user's verdicts (works, broken, not yet) are events, kept apart from availability.

**Cost.** Adding a capability means adding an entry by hand; the test says which one is missing.
Background processes have no registry, so only their modules' existence is checked.

**Amendment (2026-10-12, ADR 0018).** This list stays the curated list of what the app can do. What powers each
capability's roles now comes from the catalog of models, tools and services (`catalog/`): the options' names, needs,
licences and readiness are read from their manifests. The capabilities stay hand-written; the models beneath them are
one folder each.
