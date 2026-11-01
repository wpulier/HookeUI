"""Calendar admission for the real session scenario; no model/UI qualification."""
import datetime
import os
import time

import pytest

from sim.harness import next_local_midnight


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires local timezone selection")
@pytest.mark.parametrize("zone,day,hours", [
    ("America/New_York", "2026-11-01", 25),
    ("America/New_York", "2026-03-08", 23),
    ("America/New_York", "2026-10-30", 24),
    ("Australia/Lord_Howe", "2026-04-05", 24.5),
    ("Australia/Lord_Howe", "2026-10-04", 23.5),
    ("UTC", "2026-12-31", 24),
])
def test_session_scenario_reaches_next_calendar_day(zone, day, hours):
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = zone
        time.tzset()
        start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        boundary = next_local_midnight(start + 3600)
        tomorrow = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
        assert time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boundary)) == tomorrow + " 00:00:00"
        # These are the scenario's two observations, before and after its 3.5s wait.
        assert time.strftime("%Y-%m-%d", time.localtime(boundary - 2)) == day
        assert time.strftime("%Y-%m-%d", time.localtime(boundary + 1.5)) == tomorrow
        assert boundary - start == hours * 3600
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
