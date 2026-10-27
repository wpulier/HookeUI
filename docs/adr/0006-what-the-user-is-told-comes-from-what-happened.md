# 6. What the user is told comes from what happened

**Context.** Answers are written before tools run, and the voice speaks before the brain
acts. On 2026-10-04 the app said a highlight was done four times while nothing was outlined
(14:06:56 "Done", 14:09:55 "I've highlighted them", 14:10:13 "Here they are highlighted
now!", 15:36:56 "Here is the pink-stained tissue outlined"). The causes were in the acting
code, not the models:
- the voice was told only the steps that worked ("cleared 2 outlines");
- the look's outlines were made before the request's other steps, so a clear in the same reply
  removed them;
- a highlight that needed a new look dropped the steps beside it.

`Brain._reconcile` asks a model to rewrite an answer after a failure; that catches failed
steps, but not steps still running or outlines removed later in the same request.

**Decision.** A request is a plan of ordered steps on the board; what the chat shows and what
the voice is told about a step comes from that step's outcome, in code:
- **done** only once the board shows it;
- **in progress** while it runs (the voice says it is being done);
- **failed**, always said.

The model's draft keeps what it teaches and loses any sentence claiming an action. When a step
the voice was told was in progress lands after the voice has replied, the app says so ("The
xylem is outlined now"). The acting bench (`services/bench/acting/`) counts every "done"
said before the board showed it; it must stay at zero.

**Cost.** Steps on the board run one after another, so the last effect of a long request can
come later than when everything ran at once. The voice may speak once more for a step that
lands late.

**Amendment (2026-10-11): failures are said in the answer, in the model's words.** "Failed, always said" means said
*in the answer*, never as a card of its own. The user's rule, from that night's live test, was "the ai model
responds, not a error message". The rewrite was told two steps had failed and still said "I switched to micro-sam
and outlined the pith", and a raw "Use incomplete · …" card sat beside it.

What the user sees now:
- **The answer's mark:** the studio draws no failure card. The answer's ⓘ becomes a "!" that opens what happened
  (Activity). A step tried again successfully is settled, not a failure (`claims.unresolved`, `chatlog.ts
  unresolvedFailures`).
- **Problem ids:** each problem passed to the rewrite has an id, and the reply lists the ids it tells the user
  about (`stated`). A passed-in problem missing from that list is a confirmed omission. The answer is then written
  once more, told exactly what it left out, under its own one-time allowance (`RequestAdmission.reserve_recheck`).
  No judge runs on every request, and a reply without the list is logged as unchecked, never guessed at. If the
  omission is still there, the "!" marks the answer.
- **Choice scope:** each model choice reaches the writer with its scope as a fact (`claims.choice_scopes`): a
  `use` is "this request only; the default setting is unchanged". A rule about wording is never added.
- **Logging:** every decision is an `answer-recheck` record.
- **The completion round states them too** (2026-10-11, journeys round 2): it is given the same problem and choice
  ids and the `stated` contract, from one helper (`brain/loop.py stated_ids`, `stated_block`), and its answer stands
  without another call only when it states every one. The completion round's answer had stood unchecked, and the
  typed "try the other model" was answered 0 of 3 without its scope.
- **Its answer is kept if the rewrite cannot be had:** after a completion round answered these same settled
  receipts, a final call that fails or times out keeps that answer, the model's own grounded words, logged as
  `answer-recheck` with `unchecked` and the reason. Live, 2 of 18 choice turns had ended with no answer when the final
  call timed out at 15 s; the user's rule is that the model answers, with no blank or error card. Everywhere else an
  unavailable final model still leaves no prose, and the "!" still marks an answer whose steps hold an unresolved
  failure.
- **A request whose steps ran never ends with no words** (2026-10-11 demo, T5): when its receipts leave nothing to tell
  beyond its own answer, as ANSWER_CHECK "contradicted" judges it (no problem or choice id, nothing still running, no
  limitation, no query result to interpret, no saved resource to check), that answer is kept the same way if the
  rewrite cannot be had. At 16:49:38 the eye was outlined and shown, the rewrite was dropped for declaring
  an input it was not given, and nothing was said. With something to tell and no completion answer, no prose stays.
- **Receipt-level truth** (the same demo, T1, T2, T4): a completion round's answer that leaves an id out is written
  again even when it was the third call, on the answer's own last call; a model an answer names that no receipt names
  is a problem with an id; an answer written while a step ran is written again when that step finished during it, so
  "still in progress" is never said of a finished step; a clear keeps what a later step of the same request names; and
  a close of what the request already closed is done, not failed.
