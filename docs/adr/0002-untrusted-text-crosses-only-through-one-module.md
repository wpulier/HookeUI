# 2. Untrusted text crosses only through one module

**Context.** Researchers' words come from web pages and model output. By Oct 5 the rule
"strip angle brackets, clip, wrap as untrusted" was written by hand in 14 places in 8 files,
with 7 sanitisers that disagreed (some kept quotes, some did not clip). Eight wrapper fixes in
two days each closed one path. The brain's and the fast pass's prompts were still open: a
researcher's result could close `<researchers>` and read as trusted context.

**Decision.** `services/orchestrator/untrusted.py` (`block`, `inline`, `strip`) is the only
place a wrapper is spelled or brackets are removed. A researcher's line and readable text are
made safe where they are produced (`Session.line`, `lines`, `readable`), and prompts still
wrap them. `tests/test_untrusted.py` runs a hostile researcher through every exit and fails
if any other module strips or wraps by hand.

**Cost.** Speech and chat lines lose quote marks inside researchers' words (`inline` removes
them, as the voice's lines already did).
