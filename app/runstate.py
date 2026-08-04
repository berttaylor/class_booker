"""
Run outcome history for the status panel.

One JSON object per line in logs/runs.jsonl, appended by the scheduler and read
by the web UI. These are separate containers sharing a volume, so the file is
the only channel between them.

Readers never raise: the web page must not 500 over a missing or torn status
file. Unlike logs/, this file is disposable — dropping a bad line is correct.
"""

import json
import os
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "logs"
RUNS_FILE = LOG_DIR / "runs.jsonl"

# Minutes the scheduler is expected to run apart. Anything older than this plus
# a grace margin means the cron container is dead — see is_stale().
CRON_MINUTES = (29, 59)
STALE_AFTER_MINUTES = 35

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def append(record: dict) -> None:
    """Append one run record. Silent no-op under pytest (mirrors logger)."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUNS_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def recent(limit: int = 50) -> list[dict]:
    """Most recent runs, newest first. Returns [] if unreadable."""
    try:
        with open(RUNS_FILE) as f:
            lines = deque(f, maxlen=limit)
    except (OSError, ValueError):
        return []

    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # torn write — skip the line, keep the rest
    out.reverse()
    return out


def last() -> dict:
    """The most recent run, or {} if there are none."""
    runs = recent(1)
    return runs[0] if runs else {}


def last_booking(search: int = 2000) -> dict | None:
    """
    Most recent run that actually booked something.

    Scans the tail rather than keeping a sticky field, so it stays correct if
    runs.jsonl is edited or truncated. Bookings are rare (a couple a week
    against ~48 runs a day), so the window has to be generous.
    """
    for run in recent(search):
        if run.get("outcome") == "booked":
            return run
    return None


def next_cron_run(now: datetime | None = None) -> datetime:
    """
    Next scheduled run-due, from the crontab's `29,59 * * * *`.

    Arithmetic rather than a cron parser to avoid the dependency. If the
    crontab schedule changes, change CRON_MINUTES to match.
    """
    now = now or datetime.now()
    for minute in CRON_MINUTES:
        candidate = now.replace(minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    return (now + timedelta(hours=1)).replace(
        minute=CRON_MINUTES[0], second=0, microsecond=0
    )


def parse_time(value: str | None) -> datetime | None:
    """Parse a record timestamp, or None if absent/malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(value, TIME_FMT)
    except ValueError:
        return None


def is_stale(run: dict, now: datetime | None = None) -> bool:
    """
    True when the last run is too old, i.e. the cron container is not running.

    This is the one failure the panel can catch that a push notification
    cannot: a dead container sends nothing, so silence has to be the signal.
    """
    if not run:
        return False
    finished = parse_time(run.get("finished_at"))
    if not finished:
        return False
    now = now or datetime.now()
    return (now - finished) > timedelta(minutes=STALE_AFTER_MINUTES)
