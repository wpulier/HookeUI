# 13. Outcomes are facts; the app recommends only what won

**Context.** The app keeps what happened:
- every Execution (scaffolding, contract 6);
- the corrections the user makes;
- arena votes;
- capability verdicts;
- datasets of whole sessions.

It is tempting to turn this into rules: “Cellpose needed merges here, so switch to micro-sam”, or
“the user corrected this, so learn from it now”. Rules like that override the model's judgment
with code that is sometimes wrong (`docs/design/2026-10-06-facts-not-verdicts.md`). Training from
clicks would also change the app's behaviour without anyone measuring whether it got better.

**Decision.** What happened informs choices only as facts the brain looks up, and the app recommends
a model or a default only when it won. Nobody's choice is limited by it.
- **Outcomes are facts, pulled, not pushed.**
  - They are built only from unambiguous signals: corrections, confirmed or corrected names, votes, verdicts, empty results and failures. Something the user did that could mean several things, such as removing an outline, is never counted.
  - They are aggregated by subject, operation, implementation and policy, today and on earlier days with their dates, never across subjects. Below a minimum n they read “too few runs to say”.
  - The brain looks them up with the `outcomes` tool when it chooses, or when the user asks.
  - Pushing them into prompts is an option, off by default, adopted only if a route-choice bench improves and the brain, tool-choice, fast-pass, acting and naming benches show nothing got worse.
  - Code never switches or hides a route by them.
- **The model's context comes first.** Nothing this plan keeps is added to what every request is told unless it measured no worse than without it. The fast paths (clicks, the fast pass, voice notes, Sight, naming) carry none of it.
- **The app recommends only what won.**
  - A new model, route, prompt or fine-tune is an implementation like any other, usable at once.
  - The app recommends it as a default only when it has won on evidence: benches, the arena on `route`, or held-out replayed sessions.
  - The user can choose any implementation or set any default at any time, and the brain can choose any for a request.
- **Nothing trains from clicks automatically.** Fine-tuning is an explicit job outside the app. Its weights carry the licence tier of the model they came from (ADR 0005).

**Cost.** Improvement is slower than learning on the fly: a better route waits for enough runs, or a
replay win, before the app recommends it. The brain has to ask for outcomes rather than having them in
view. The replay bench and the session bundles are work to build and keep. Outcomes across days
are a store the brain can look up, kept about routes, never about the user's conversations.

Design: `docs/design/2026-10-07-session-knowledge.md`.
