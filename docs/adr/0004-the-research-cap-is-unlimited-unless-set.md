# 4. The daily research cap is unlimited unless the user sets one

**Context.** The cap defaulted to $15 a day and refused a new researcher only once spend had
already reached it, so one deep run could go about $5 over. Spend was held in three places,
and a run past midnight counted yesterday's spend.

**Decision (with the user, 2026-10-05).** By default there is no cap. Setting one is easy: a
field in the studio's settings, by voice or chat (`set_research_cap`), or
`MICROMIND_RESEARCH_CAP`; it is kept for the day. When a cap is set, a run reserves its
effort's maximum when it starts and gives back what it did not spend when it ends, so the day
never goes over. `research/ledger.py` keeps each day's spend apart. Per-run effort caps
(`research_worker/budget.py`) apply while a daily cap is set; with none (the default, at the user's request on
2026-10-10, `docs/finish-line.md`), the worker's per-run dollar gate is off too (`Budget(unlimited=True)`); pictures and concurrency stay bounded. A run's
time and rounds are where it is told to write up, never a cut (2026-10-11, "the clock never kills work"): with no
daily cap, the guard on money is the user's own "keep going?" when a run passes 3x its effort's usual cost (and after
an hour of running), with 15 minutes of silence writing it up (AGENTS.md, Researchers).

**Cost.** Near the cap, a deep run is refused while a quick one still fits.

**Amendment (2026-10-12): the cap's amount is the user's, kept until changed.** Sessions are leaving the calendar
day, so the user's settings left the day's journal for their own file, `sessions/preferences.jsonl`
(`day/prefs.py`). A cap set today still holds tomorrow and after a cold start, until the user changes it; "no cap"
is kept the same way (it was never recorded when no cap had been set before, so a restart went back to
`MICROMIND_RESEARCH_CAP`). What the cap limits is unchanged: spend still counts per calendar day
(`research/ledger.py`, `research/<date>`), so the amount applies afresh to each day's spend.

**Amendment (2026-10-12, sessions): spend counts on the calendar day a run happens on.** A session can span days and a
day can hold several (ADR 0017). A run's spend is charged to the day it started (`usageRuns[...]["day"]`), whatever
session it belongs to or date folder its researcher is filed in; a run filed in an earlier date's folder is listed in
`research/<day>/runs.jsonl` so that day's ledger finds it. Every session's spend on a day counts against that day's cap.
