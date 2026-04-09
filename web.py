"""
Schedule editor web UI.
Run with: python web.py
Then open http://localhost:8008/schedules/bert in your browser.
"""

import re
import socket
import time
from flask import Flask, abort, request, jsonify, render_template, Response
import json
from pathlib import Path
import yaml
import subprocess
import plistlib
from datetime import datetime, timedelta


from app.teachers import load_teacher_cache, validate_rules_against_cache

BASE_DIR = Path(__file__).parent
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

SERVICE_LABELS = {
    "com.berttaylor.class_booker.web": "Web Server",
    "com.berttaylor.class_booker": "Class Scheduler",
    "com.berttaylor.class_booker.teachers": "Teacher Sync",
}


class IndentDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(IndentDumper, self).increase_indent(flow, False)


def _extract_header_comments(content: str) -> str:
    lines = content.splitlines()
    header = []
    for line in lines:
        if line.strip().startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    if not header:
        return ""
    return "\n".join(header) + "\n"


def _get_next_run(label: str) -> str:
    try:
        plist_path = BASE_DIR / "runners" / f"{label}.plist"
        if not plist_path.exists():
            return ""

        with open(plist_path, "rb") as f:
            data = plistlib.load(f)

        intervals = data.get("StartCalendarInterval")
        if not intervals:
            return ""

        if isinstance(intervals, dict):
            intervals = [intervals]

        now = datetime.now()
        next_runs = []

        for interval in intervals:
            # interval might have Minute, Hour, Day, Month, Weekday
            minute = interval.get("Minute", 0)
            hour = interval.get("Hour")

            # Simple logic for Minute/Hour based schedules
            if hour is None:
                # Runs every hour at 'minute'
                run_time = now.replace(minute=minute, second=0, microsecond=0)
                if run_time <= now:
                    run_time += timedelta(hours=1)
                next_runs.append(run_time)
            else:
                # Runs every day at 'hour:minute'
                run_time = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if run_time <= now:
                    run_time += timedelta(days=1)
                next_runs.append(run_time)

        if next_runs:
            next_run = min(next_runs)
            return f"next run at {next_run.strftime('%H:%M')}"

        return ""
    except Exception:
        return ""


def _get_service_status(label: str) -> dict:
    friendly_name = SERVICE_LABELS.get(label, label)
    try:
        res = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True
        )
        if res.returncode != 0:
            return {
                "label": label,
                "name": friendly_name,
                "status": "Not loaded",
                "pid": None,
            }

        # Check if it has a PID
        for line in res.stdout.splitlines():
            if '"PID"' in line:
                try:
                    # Line looks like: "PID" = 43909;
                    pid_str = line.split("=")[1].strip().rstrip(";")
                    return {
                        "label": label,
                        "name": friendly_name,
                        "status": "Running",
                        "pid": int(pid_str),
                    }
                except (IndexError, ValueError):
                    pass

        next_run_str = _get_next_run(label)
        status = f"{next_run_str}" if next_run_str else "Loaded (Waiting)"
        return {
            "label": label,
            "name": friendly_name,
            "status": status,
            "pid": None,
        }
    except Exception as e:
        return {
            "label": label,
            "name": friendly_name,
            "status": f"Error: {str(e)}",
            "pid": None,
        }


def _check_internet_access() -> bool:
    try:
        # Check connectivity to Google's public DNS
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except (OSError, socket.timeout):
        return False


def _get_network_connection() -> dict:
    try:
        # Find the active interface for the default route
        res = subprocess.run(
            ["/sbin/route", "-n", "get", "default"], capture_output=True, text=True
        )
        interface = ""
        for line in res.stdout.splitlines():
            if "interface:" in line:
                interface = line.split(":")[1].strip()
                break

        if not interface:
            return {"active": False, "type": "None", "name": "No connection"}

        # Determine if it's Wi-Fi or Ethernet
        hw_res = subprocess.run(
            ["/usr/sbin/networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
        )
        hw_type = "Ethernet"
        current_port = ""
        for line in hw_res.stdout.splitlines():
            if "Hardware Port:" in line:
                current_port = line.split(":")[1].strip()
            elif f"Device: {interface}" in line:
                hw_type = (
                    "Wi-Fi"
                    if "Wi-Fi" in current_port or "AirPort" in current_port
                    else "Ethernet"
                )
                break

        # Get name (SSID if Wi-Fi)
        conn_name = interface
        if hw_type == "Wi-Fi":
            wifi_res = subprocess.run(
                ["/usr/sbin/networksetup", "-getairportnetwork", interface],
                capture_output=True,
                text=True,
            )
            if "Current Wi-Fi Network:" in wifi_res.stdout:
                conn_name = wifi_res.stdout.split(":")[1].strip()

        return {
            "active": True,
            "type": hw_type,
            "name": conn_name,
            "interface": interface,
        }
    except Exception:
        return {"active": False, "type": "Error", "name": "Check failed"}


app = Flask(__name__)


@app.route("/api/status/stream")
def status_stream():
    def event_stream():
        last_full_check = 0
        last_status = None

        while True:
            now = time.time()
            # Service status (cheap)
            services = [_get_service_status(label) for label in SERVICE_LABELS]

            # System status (more expensive)
            if now - last_full_check > 30:
                system_status = {
                    "internet": _check_internet_access(),
                    "connection": _get_network_connection(),
                }
                last_full_check = now
            elif last_status:
                system_status = last_status["system"]
            else:
                system_status = {
                    "internet": _check_internet_access(),
                    "connection": _get_network_connection(),
                }
                last_full_check = now

            current_status = {"services": services, "system": system_status}

            if current_status != last_status:
                yield f"data: {json.dumps(current_status)}\n\n"
                last_status = current_status

            time.sleep(1)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/teachers")
def api_teachers():
    cache = load_teacher_cache()
    if not cache:
        return jsonify([])
    names = sorted(
        n for n, t in cache.get("teachers", {}).items() if t.get("status") == "ACTIVE"
    )
    return jsonify(names)


@app.route("/")
def index():
    schedules = sorted(p.stem for p in (BASE_DIR / "scheduling_rules").glob("*.yml"))

    # We look for all .log and .json files in logs/
    logs_files = list((BASE_DIR / "logs").glob("*.log")) + list(
        (BASE_DIR / "logs").glob("*.json")
    )
    logs = sorted(list(set(p.stem for p in logs_files)))

    # Service status
    services = [_get_service_status(label) for label in SERVICE_LABELS]

    system_status = {
        "internet": _check_internet_access(),
        "connection": _get_network_connection(),
    }

    return render_template(
        "index.html",
        schedules=schedules,
        logs=logs,
        services=services,
        system=system_status,
    )


def _validate_name(name: str):
    if not NAME_RE.match(name):
        abort(400, "Invalid name")


@app.route("/schedules/<name>")
def schedule_editor(name: str):
    _validate_name(name)
    path = BASE_DIR / "scheduling_rules" / f"{name}.yml"
    if not path.exists():
        abort(404)
    content = path.read_text()
    return render_template("editor.html", name=name, content=content)


@app.route("/schedules/<name>/save", methods=["POST"])
def save(name: str):
    _validate_name(name)
    path = BASE_DIR / "scheduling_rules" / f"{name}.yml"
    if not path.exists():
        abort(404)

    content = request.json.get("content", "")

    # Parse YAML
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        msg = str(e).split("\n")[0]
        return jsonify(ok=False, error=f"Invalid YAML — {msg}")

    # Sort rules helper
    from app.rules import sort_rules

    data = sort_rules(data)

    # Generate sorted YAML string with improved formatting
    header = _extract_header_comments(content)
    content = yaml.dump(
        data,
        Dumper=IndentDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )

    # Post-process to add spaces between major sections and rules
    for key in ["settings:", "credentials:", "rules:"]:
        content = content.replace(f"\n{key}", f"\n\n{key}")

    # Add blank lines between rules
    content = content.replace("\n  - ", "\n\n  - ")
    # But not before the first rule
    content = content.replace("rules:\n\n  - ", "rules:\n  - ")

    # Prepend original header comments
    content = header + content.strip() + "\n"

    # Validate rules
    try:
        rules = _load_rules_from_dict(data)
    except Exception as e:
        return jsonify(ok=False, error=_friendly_error(str(e)))

    # Check for duplicate rule IDs
    ids = [r.id for r in rules.rules]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        return jsonify(
            ok=False,
            error=f"Two rules share the same day and start time: {', '.join(dupes)}. Each rule must have a unique combination.",
        )

    # Validate against teacher cache
    cache = load_teacher_cache()
    if cache:
        try:
            validate_rules_against_cache(rules, cache)
        except ValueError as e:
            return jsonify(ok=False, error=str(e))

    path.write_text(content)
    return jsonify(ok=True, content=content)


@app.route("/logs/<name>")
def view_log(name: str):
    _validate_name(name)

    # Prefer .json file if it exists, otherwise use .log
    json_path = BASE_DIR / "logs" / f"{name}.json"
    log_path = BASE_DIR / "logs" / f"{name}.log"

    if json_path.exists():
        content = json_path.read_text()
        try:
            logs = json.loads(content)
        except Exception:
            logs = [
                {
                    "timestamp": "-",
                    "level": "INFO",
                    "message": "Malformed JSON log file",
                }
            ]
    elif log_path.exists():
        content = log_path.read_text()
        logs = [
            {"timestamp": "-", "level": "INFO", "message": line}
            for line in content.splitlines()
        ]
    else:
        abort(404)

    return render_template("logs.html", name=name, logs=logs[-1000:])


def _friendly_error(raw: str) -> str:
    r = raw.lower()
    if "weekday" in r:
        return "Invalid weekday — use one of: mon, tue, wed, thu, fri, sat, sun."
    if "start_time" in r and "half" in r:
        return 'Start time must be on the hour or half-hour, e.g. "13:00" or "13:30".'
    if "start_time" in r:
        return 'Invalid start time — use HH:MM format, e.g. "09:00" or "18:30".'
    if "slots" in r:
        return "Slots must be 1 (30 min) or 2 (1 hour)."
    if "timezone" in r:
        return 'Unknown timezone — use a standard timezone like "Europe/London" or "America/New_York".'
    if "preferred_teachers" in r:
        return "You must list at least one preferred teacher."
    if "credentials" in r:
        return "Missing credentials — add your email and password."
    if "field required" in r or "missing" in r:
        return "A required field is missing — check each rule has a weekday, start_time, slots, and preferred_teachers."
    return "Something doesn't look right — check your rules and try again."


def _load_rules_from_dict(data: dict):
    from app.rules import SchedulingRules

    return SchedulingRules(**data)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8008)
