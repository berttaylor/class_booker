import json
import os
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "logs"

_context = threading.local()
_run_id = None


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
    _ensure_logs()
    path = LOG_DIR / f"{log_name}.json"

    # If file doesn't exist or is not a valid JSON array, start a new one
    if not path.exists() or path.stat().st_size < 2:
        with open(path, "w") as f:
            json.dump([event], f, indent=2)
        return

    # Efficiently append to JSON array
    try:
        with open(path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell() - 1
            # Find the last ']' from the end
            while pos >= 0:
                f.seek(pos)
                if f.read(1) == b"]":
                    f.seek(pos)
                    # Prepare the new event string
                    event_json = json.dumps(event, indent=2)
                    # Indent the event_json to match the array style (2 spaces)
                    indented_event = "  " + event_json.replace("\n", "\n  ")
                    new_content = ",\n" + indented_event + "\n]"
                    f.write(new_content.encode("utf-8"))
                    f.truncate()
                    return
                pos -= 1

            # If we didn't find ']', it's invalid JSON, start fresh
            f.seek(0)
            f.truncate()
            json.dump([event], f, indent=2)
    except Exception:
        # Fallback to full read/write if something goes wrong
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            data = []
        data.append(event)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


def log(message, level="INFO", schedule=None, run_id=None, **kwargs):
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

    _append_to_log("main", event)


def info(message, schedule=None, run_id=None, **kwargs):
    log(message, "INFO", schedule, run_id, **kwargs)


def error(message, schedule=None, run_id=None, **kwargs):
    log(message, "ERROR", schedule, run_id, **kwargs)


def warning(message, schedule=None, run_id=None, **kwargs):
    log(message, "WARNING", schedule, run_id, **kwargs)
