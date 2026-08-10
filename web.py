"""
Schedule editor and status web UI.

Served by gunicorn in the container; see compose.yml. Runs behind Caddy, which
terminates TLS — Caddy no longer does auth, this app does (see app/webauth.py).
Caddy is still the only thing that should reach it directly.
"""

import re
from flask import Flask, abort, request, jsonify, render_template
import json
from collections import deque
from pathlib import Path
import yaml

from app import runstate
from app.teachers import load_teacher_cache, validate_rules_against_cache
from app.webauth import init_auth

BASE_DIR = Path(__file__).parent
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


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


app = Flask(__name__)
init_auth(app)


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

    log_dir = BASE_DIR / "logs"
    logs_files = (
        list(log_dir.glob("*.log"))
        + list(log_dir.glob("*.json"))
        + list(log_dir.glob("*.jsonl"))
    )
    # runs.jsonl backs the panel above; it would be noise as a log link.
    logs = sorted({p.stem for p in logs_files} - {"runs"})

    last = runstate.last()
    return render_template(
        "index.html",
        schedules=schedules,
        logs=logs,
        last=last,
        runs=runstate.recent(50),
        last_booking=runstate.last_booking(),
        next_cron=runstate.next_cron_run().strftime("%H:%M"),
        stale=runstate.is_stale(last),
        stale_after=runstate.STALE_AFTER_MINUTES,
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

    TAIL = 1000
    jsonl_path = BASE_DIR / "logs" / f"{name}.jsonl"
    json_path = BASE_DIR / "logs" / f"{name}.json"
    log_path = BASE_DIR / "logs" / f"{name}.log"

    if jsonl_path.exists():
        # deque streams the file and keeps only the tail, so the whole log never
        # has to be parsed to render the last screenful.
        with open(jsonl_path) as f:
            lines = deque(f, maxlen=TAIL)
        logs = []
        for line in lines:
            if not line.strip():
                continue
            try:
                logs.append(json.loads(line))
            except ValueError:
                # One torn line shouldn't blank the whole view.
                logs.append(
                    {"timestamp": "-", "level": "WARNING", "message": line.strip()}
                )
    elif json_path.exists():
        # Pre-JSONL archive, kept readable.
        try:
            logs = json.loads(json_path.read_text())
        except ValueError:
            logs = [
                {
                    "timestamp": "-",
                    "level": "INFO",
                    "message": "Malformed JSON log file",
                }
            ]
    elif log_path.exists():
        logs = [
            {"timestamp": "-", "level": "INFO", "message": line}
            for line in log_path.read_text().splitlines()
        ]
    else:
        abort(404)

    return render_template("logs.html", name=name, logs=logs[-TAIL:])


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
    # Local debugging only, bound to loopback. Production runs under gunicorn
    # (see compose.yml): debug=True exposes the Werkzeug console, which is a
    # remote shell for anyone who reaches it.
    app.run(debug=True, host="127.0.0.1", port=8008)
