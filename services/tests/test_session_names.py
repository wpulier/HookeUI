"""The app reads the session store's `sid` or `date`, never its old `day` (the plan "sessions are the project,
not the day", P2).

`SessionStore.day` stays only as a read-only alias for tests: a session's identity (routing, `Asked`, the wire,
the journal) is `store.sid`, and the calendar folder new per-day files go to is `store.date`. Which one a site
means is what many sessions per day (P3) and sessions across days (P4) rely on, so production code must say.

The patterns, over production source with comments blanked out (strings and docstrings are kept, since
`getattr(store, "day")` names the attribute in a string):
- `day.day`: a store held as `day` (`day.day`, `self.day.day`, `services.day.day`, `app.day.day`, `built.day.day`);
- `store.day`: a store held as `store` (`store.day`, `self.store.day`, `conversation.store.day`);
- `execution_day.day`, `day_store.day`, `day_owner.day`, `owner.day`: the other names a store is held by;
- `getattr(<a store>, "day"...)`, where the store is any of the names above, or `getattr(<x>, "day"/"store"/
  "execution_day", ...)` itself (`getattr(getattr(hub, "execution_day", None), "day", None)`);
- `.journal.day`: the journal's own old name for its session id.
Unrelated `day` attributes are not matched: `Asked.day` (`asked.day`), a researcher's `session.day`, an index
entry's `entry.day`, a population's `population.day`, taught and seen's own `self.day` strings, or a dict's
"day" key.
"""
import io
import re
import tokenize
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[1]
ROOTS = ("orchestrator", "research_worker", "sim", "bench")
ALIAS = SERVICES / "orchestrator" / "day" / "store.py"  # where the read-only alias is defined
_HOLDER = r"(?:\bday|\bstore|\bexecution_day|\bday_store|\bday_owner|\bowner)"
STORE_DAY = re.compile(
    rf"{_HOLDER}\.day\b(?!\s*\()"
    rf"|getattr\(\s*(?:[\w.]*{_HOLDER}|getattr\([^()]*[\"'](?:day|store|execution_day)[\"'][^()]*\))\s*,\s*[\"']day[\"']"
    r"|\.journal\.day\b")


def without_comments(text: str) -> str:
    """The source with every comment blanked out (same lines and columns), so a comment never trips the check."""
    lines = text.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    for token in tokens:
        if token.type == tokenize.COMMENT:
            (row, start), (_, end) = token.start, token.end
            line = lines[row - 1]
            lines[row - 1] = line[:start] + " " * (end - start) + line[end:]
    return "".join(lines)


def offenders_in(text: str) -> list[int]:
    clean = without_comments(text)
    return [clean.count("\n", 0, m.start()) + 1 for m in STORE_DAY.finditer(clean)]


def test_the_patterns_find_reads_of_the_stores_day_and_nothing_else():
    flagged = [
        "x = day.day", "self.day.day", "services.day.day", "app.day.day", "self.app.day.day", "built.day.day",
        "store.day", "self.store.day", "conversation.store.day", "hub.execution_day.day", "day_store.day != day",
        'getattr(self.day, "day", None)', "getattr(day, 'day', '')", 'getattr(services.day, "day", None)',
        'getattr(getattr(self.hub, "execution_day", None), "day", None)',
        'getattr(getattr(found.dialogue, "day", None), "day", "")', "self.journal.day", "lambda: day.day",
        "f'{store.day}/x'", "owner.day != day",
    ]
    for code in flagged:
        assert offenders_in(code), code
    clean = [
        "asked.day", "original.day", "session.day", "entry.day", "population.day", "item.day", "self.day = day",
        "self.day.sid", "self.day.date", "day.sid", "store.date", 'getattr(asked, "day", None)',
        'getattr(population, "day", day)', 'getattr(services, "day", None)', 'getattr(self.dialogue, "day", None)',
        'getattr(results, "day", None)', '{"day": day}', "record.get('day')", "self.day.here()", "day.day_digest()",
        "x = 1  # store.day in a comment", "yesterday.day_count", "today.day",
    ]
    for code in clean:
        assert not offenders_in(code), code


def test_production_code_reads_the_store_sid_or_date_never_day():
    offenders = []
    for root in ROOTS:
        for path in sorted((SERVICES / root).rglob("*.py")):
            if path == ALIAS or "/." in str(path.relative_to(SERVICES)):
                continue
            text = path.read_text(encoding="utf-8")
            for n in offenders_in(text):
                offenders.append(f"{path.relative_to(SERVICES)}:{n}: {text.splitlines()[n - 1].strip()}")
    assert not offenders, ("read the store's `sid` (its identity: routing, the wire, the journal) or `date` (the "
                           "calendar folder), not `day`:\n" + "\n".join(offenders))
