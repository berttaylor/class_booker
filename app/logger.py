import json
import os
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "logs"

_context = threading.local()
_run_id = None
_enabled = "PYTEST_CURRENT_TEST" not in os.environ


def set_enabled(enabled):
    global _enabled
    _enabled = enabled


def set_schedule(schedule_name):
    _context.schedule = schedule_name


def get_schedule():
    return getattr(_context, "schedule", None)


def set_run_id(run_id):
    _context.run_id = run_id


def get_run_id():
    return getattr(_context, "run_id", None)


def _ensure_logs():
    if not LOG_DIR.exists():
        LOG_DIR.mkdir(parents=True)


def _append_to_log(log_name, event):
    """
    Append one event as a JSON line.

    One object per line rather than a JSON array: appending is a single write
    with no read-modify-write, so a killed process can at worst leave one torn
    line instead of corrupting the whole file (the reader skips bad lines).
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return

    _ensure_logs()
    with open(LOG_DIR / f"{log_name}.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")


def log(message, level="INFO", schedule=None, run_id=None, **kwargs):
    if not _enabled:
        return

    if schedule is None:
        schedule = get_schedule()

    if run_id is None:
        run_id = get_run_id()

    # Print to stdout for terminal feedback
    prefix = f"[{schedule}] " if schedule else ""
    run_prefix = f"[{run_id}] " if run_id else ""
    print(f"{prefix}{run_prefix}[{level}] {message}")

    event = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "run_id": run_id,
        "schedule": schedule,
        "message": message,
    }
    event.update(kwargs)

    # "events", not "main": logs/main.json is the pre-JSONL archive and stays
    # readable in the log viewer alongside this.
    _append_to_log("events", event)


def info(message, schedule=None, run_id=None, **kwargs):
    log(message, "INFO", schedule, run_id, **kwargs)


def error(message, schedule=None, run_id=None, **kwargs):
    log(message, "ERROR", schedule, run_id, **kwargs)


def warning(message, schedule=None, run_id=None, **kwargs):
    log(message, "WARNING", schedule, run_id, **kwargs)
