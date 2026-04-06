"""
Schedule editor web UI.
Run with: python web.py
Then open http://localhost:8008/schedules/bert in your browser.
"""

import re
from flask import Flask, abort, request, jsonify, render_template_string
import json
from pathlib import Path
import yaml
import subprocess
import plistlib
from datetime import datetime, timedelta


from app.teachers import load_teacher_cache, validate_rules_against_cache

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
    try:
        res = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True
        )
        if res.returncode != 0:
            return {"label": label, "status": "Not loaded", "pid": None}

        # Check if it has a PID
        for line in res.stdout.splitlines():
            if '"PID"' in line:
                try:
                    # Line looks like: "PID" = 43909;
                    pid_str = line.split("=")[1].strip().rstrip(";")
                    return {"label": label, "status": "Running", "pid": int(pid_str)}
                except (IndexError, ValueError):
                    pass

        next_run_str = _get_next_run(label)
        status = f"Loaded - {next_run_str}" if next_run_str else "Loaded (Waiting)"
        return {"label": label, "status": status, "pid": None}
    except Exception as e:
        return {"label": label, "status": f"Error: {str(e)}", "pid": None}


app = Flask(__name__)

PAGE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Schedule Editor</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-darker.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/yaml/yaml.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }
    header { padding: 12px 16px; background: #16213e; border-bottom: 1px solid #0f3460; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .header-title { flex: 1; min-width: 200px; }
    .header-title h1 { font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    #status { font-size: 11px; color: #666; margin-top: 2px; }
    #status.ok { color: #4caf82; }
    #status.error { color: #f87171; }
    .icon-btns { display: flex; border: 1px solid #1a5a8a; border-radius: 6px; overflow: hidden; flex-shrink: 0; }
    .icon-btn { padding: 7px 11px; background: #0f3460; border: none; color: #eee; cursor: pointer; font-size: 15px; line-height: 1; white-space: nowrap; }
    .icon-btn:hover { background: #1a5a8a; }
    .icon-btn + .icon-btn { border-left: 1px solid #1a5a8a; }
    button.save { padding: 8px 18px; background: #1a4731; border: 1px solid #2d6a4f; color: #4caf82; border-radius: 6px; cursor: pointer; font-size: 14px; white-space: nowrap; font-weight: 600; flex-shrink: 0; }
    button.save:hover { background: #2d6a4f; }
    a.home { color: #aaa; font-size: 20px; text-decoration: none; flex-shrink: 0; line-height: 1; }
    a.home:hover { color: #eee; }
    .CodeMirror { flex: 1; height: 100%; font-size: 14px; line-height: 1.6; }
    #editor-wrap { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
    #picker { display: none; position: absolute; top: 0; right: 0; bottom: 0; width: 240px; background: #16213e; border-left: 1px solid #0f3460; z-index: 10; flex-direction: column; }
    #picker.open { display: flex; }
    #picker-search { padding: 10px 12px; background: #0f3460; border: none; border-bottom: 1px solid #1a5a8a; color: #eee; font-size: 14px; outline: none; }
    #picker-search::placeholder { color: #666; }
    #picker-list { flex: 1; overflow-y: auto; }
    .picker-item { padding: 10px 14px; font-size: 13px; cursor: pointer; border-bottom: 1px solid #0f3460; }
    .picker-item:hover { background: #0f3460; }
    #picker-copied { padding: 12px 14px; text-align: center; font-size: 13px; color: #4caf82; display: none; }
  </style>
</head>
<body>
  <header>
    <a class="home" href="/" title="Home">&#8592;</a>
    <div class="header-title">
      <h1>{{ name | capitalize }}</h1>
      <div id="status" class="ok">saved</div>
    </div>
    <div class="icon-btns">
      <button class="icon-btn" onclick="adjustFont(1)"><img src="/static/icons/zoom_in.png" height="22" style="filter:invert(1)"></button>
      <button class="icon-btn" onclick="adjustFont(-1)"><img src="/static/icons/zoom_out.png" height="22" style="filter:invert(1)"></button>
      <button class="icon-btn" onclick="addRule()"><img src="/static/icons/add_rule.webp" height="24" style="filter:invert(1);margin:-3px"></button>
      <button class="icon-btn" onclick="togglePicker()"><img src="/static/icons/teachers.png" height="24" style="filter:invert(1);margin:-3px"></button>
    </div>
    <button class="save" onclick="save()">Save</button>
  </header>
  <div id="editor-wrap">
    <textarea id="editor"></textarea>
    <div id="picker">
      <input id="picker-search" type="text" placeholder="Search teachers..." oninput="filterPicker(this.value)">
      <div id="picker-copied">Copied!</div>
      <div id="picker-list"></div>
    </div>
  </div>
  <script>
    const cm = CodeMirror.fromTextArea(document.getElementById('editor'), {
      mode: 'yaml',
      theme: 'material-darker',
      lineNumbers: true,
      indentWithTabs: false,
      tabSize: 2,
      extraKeys: { "Ctrl-S": save, "Cmd-S": save },
    });
    let ignoreChange = true;
    cm.setValue({{ content | tojson }});
    cm.setSize('100%', '100%');
    ignoreChange = false;
    cm.on('change', () => {
      if (!ignoreChange) setStatus('unsaved', '');
    });

    function setStatus(cls, msg) {
      const el = document.getElementById('status');
      el.className = cls;
      el.textContent = msg || 'unsaved';
    }

    function addRule() {
      const lines = [
        '',
        '  - weekday: mon',
        '    start_time: "13:00"',
        '    enabled: true',
        '    slots: 1',
        '    preferred_teachers:',
        '      - ""',
        '',
      ];
      cm.setValue(cm.getValue().trimEnd() + '\\n' + lines.join('\\n'));
      cm.scrollIntoView({ line: cm.lineCount() - 1, ch: 0 });
    }

    let allTeachers = [];
    fetch('/api/teachers').then(r => r.json()).then(names => { allTeachers = names; });

    function togglePicker() {
      const picker = document.getElementById('picker');
      if (picker.classList.contains('open')) {
        picker.classList.remove('open');
      } else {
        renderPicker(allTeachers);
        picker.classList.add('open');
        document.getElementById('picker-search').value = '';
        document.getElementById('picker-search').focus();
      }
    }

    function filterPicker(q) {
      const filtered = allTeachers.filter(n => n.toLowerCase().includes(q.toLowerCase()));
      renderPicker(filtered);
    }

    function renderPicker(names) {
      const list = document.getElementById('picker-list');
      list.innerHTML = '';
      names.forEach(n => {
        const el = document.createElement('div');
        el.className = 'picker-item';
        el.textContent = n;
        el.addEventListener('click', () => insertTeacher(n));
        list.appendChild(el);
      });
    }

    function insertTeacher(name) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(name);
      } else {
        const ta = document.createElement('textarea');
        ta.value = name;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      const copied = document.getElementById('picker-copied');
      document.getElementById('picker-search').style.display = 'none';
      document.getElementById('picker-list').style.display = 'none';
      copied.style.display = 'block';
      setTimeout(() => {
        copied.style.display = 'none';
        document.getElementById('picker-search').style.display = '';
        document.getElementById('picker-list').style.display = '';
        document.getElementById('picker').classList.remove('open');
      }, 400);
    }

    let fontSize = 14;
    function adjustFont(delta) {
      fontSize = Math.max(8, Math.min(32, fontSize + delta));
      cm.getWrapperElement().style.fontSize = fontSize + 'px';
      cm.refresh();
    }

    async function save() {
      setStatus('', 'saving...');
      const resp = await fetch('/schedules/{{ name }}/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: cm.getValue() }),
      });
      const data = await resp.json();
      if (data.ok) {
        if (data.content) {
          const cursor = cm.getCursor();
          ignoreChange = true;
          cm.setValue(data.content);
          cm.setCursor(cursor);
          ignoreChange = false;
        }
        setStatus('ok', 'saved');
      } else {
        setStatus('error', data.error);
      }
    }
  </script>
</body>
</html>
"""

INDEX_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Class Booker</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; padding: 40px; }
    h1 { font-size: 20px; font-weight: 600; margin-bottom: 32px; }
    h2 { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #aaa; margin-bottom: 12px; }
    section { margin-bottom: 28px; }
    ul { list-style: none; display: flex; flex-direction: column; gap: 8px; }
    .status-card { display: flex; align-items: center; justify-content: space-between; padding: 4px 0; font-size: 13px; color: #888; border-bottom: 1px solid #16213e; }
    .status-card:last-child { border-bottom: none; }
    .status-badge { font-size: 10px; font-weight: 600; text-transform: uppercase; padding: 1px 6px; border-radius: 3px; margin-left: 12px; }
    .status-running { background: #1a4731; color: #4caf82; }
    .status-stopped { background: #47311a; color: #fbbf24; }
    .status-not-loaded { background: #333; color: #666; }
    a { display: inline-block; padding: 8px 16px; background: #16213e; border: 1px solid #0f3460; border-radius: 6px; color: #eee; text-decoration: none; font-size: 14px; }
    a:hover { background: #0f3460; }
  </style>
</head>
<body>
  <h1>Class Booker</h1>
  <section>
    <h2>Schedules</h2>
    <ul>
      {% for name in schedules %}
      <li><a href="/schedules/{{ name | capitalize }}">{{ name | capitalize }}</a></li>
      {% endfor %}
    </ul>
  </section>
  <section>
    <h2>Logs</h2>
    <ul>
      {% for name in logs %}
      <li><a href="/logs/{{ name | capitalize }}">{{ name | capitalize }}</a></li>
      {% endfor %}
    </ul>
  </section>
  <section>
    <h2>Services</h2>
    <ul>
      {% for s in services %}
      <li class="status-card">
        <span>{{ s.label }} {% if s.pid %}<small style="color:#444; margin-left: 8px;">(PID: {{ s.pid }})</small>{% endif %}</span>
        <span class="status-badge {% if s.status == 'Running' %}status-running{% elif s.status == 'Not loaded' %}status-not-loaded{% else %}status-stopped{% endif %}">
          {{ s.status }}
        </span>
      </li>
      {% endfor %}
    </ul>
  </section>
</body>
</html>
"""

LOGS_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ name }}</title>
  <link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css">
  <script src="https://code.jquery.com/jquery-3.7.0.js"></script>
  <script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }
    header { padding: 12px 16px; background: #16213e; border-bottom: 1px solid #0f3460; display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 16px; font-weight: 600; }
    .toolbar { display: flex; align-items: center; gap: 10px; flex: 1; }
    .toolbar-right { display: flex; align-items: center; gap: 8px; margin-left: auto; }
    a.home { padding: 8px 14px; background: #0f3460; border: 1px solid #1a5a8a; color: #eee; border-radius: 6px; font-size: 14px; text-decoration: none; white-space: nowrap; }
    a.home:hover { background: #1a5a8a; }
    
    .toolbar select, .toolbar input {
        background: #0f3460;
        border: 1px solid #1a5a8a;
        color: #eee;
        padding: 6px 12px;
        border-radius: 6px;
        font-size: 13px;
        outline: none;
        width: 140px;
    }
    .toolbar input::placeholder { color: #666; }
    
    #log-wrap { flex: 1; overflow: auto; padding: 20px; background: #1a1a2e; }
    
    /* DataTable Dark Theme Overrides */
    .dataTables_wrapper { color: #eee !important; font-size: 13px; }
    table.dataTable { background-color: #16213e !important; color: #eee !important; border-bottom: none !important; margin-top: 15px !important; }
    table.dataTable thead th { background-color: #0f3460 !important; color: #aaa !important; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; padding: 12px 10px !important; border-bottom: 1px solid #1a5a8a !important; }
    table.dataTable tbody tr { background-color: #16213e !important; }
    table.dataTable tbody tr:hover { background-color: #1a5a8a !important; }
    table.dataTable tbody td { padding: 10px !important; border-bottom: 1px solid #0f3460 !important; line-height: 1.4; vertical-align: top; }
    
    .dataTables_filter input { background: #0f3460 !important; border: 1px solid #1a5a8a !important; color: #eee !important; padding: 6px 12px !important; border-radius: 6px !important; outline: none; }
    .dataTables_length select { background: #0f3460 !important; border: 1px solid #1a5a8a !important; color: #eee !important; padding: 4px !important; border-radius: 4px !important; }
    .dataTables_info { padding-top: 15px !important; color: #666 !important; }
    .dataTables_paginate { padding-top: 15px !important; }
    .paginate_button { color: #aaa !important; }
    .paginate_button.current { background: #1a5a8a !important; border: 1px solid #1a5a8a !important; color: white !important; }
    
    .lvl-ERROR { color: #f87171; font-weight: 600; }
    .lvl-WARNING { color: #fbbf24; font-weight: 600; }
    .lvl-INFO { color: #4caf82; }
    
    .timestamp { white-space: nowrap; color: #888; font-family: monospace; }
    .schedule-tag { background: #1a4731; color: #4caf82; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .run-id-tag { background: #0f3460; color: #1a5a8a; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .msg-cell { word-break: break-all; }
  </style>
</head>
<body>
  <header>
    <h1>{{ name | capitalize }}</h1>
    <div class="toolbar">
      <a class="home" href="/">&#8592; Home</a>
      <div class="toolbar-right">
        <select id="level-filter">
          <option value="">All Levels</option>
        </select>
        <select id="schedule-filter">
          <option value="">All Schedules</option>
        </select>
        <input id="run-id-filter" type="text" placeholder="Filter Run ID..." list="run-ids">
        <datalist id="run-ids"></datalist>
      </div>
    </div>
  </header>
  <div id="log-wrap">
    <table id="log-table" class="display" style="width:100%">
        <thead>
            <tr>
                <th width="160">Timestamp</th>
                <th width="80">Level</th>
                <th width="100">Run ID</th>
                <th width="100">Schedule</th>
                <th>Message</th>
            </tr>
        </thead>
        <tbody>
            {% for log in logs %}
            <tr>
                <td class="timestamp">{{ log.timestamp }}</td>
                <td class="lvl-{{ log.level }}">{{ log.level }}</td>
                <td>{% if log.run_id %}<span class="run-id-tag">{{ log.run_id }}</span>{% endif %}</td>
                <td>{% if log.schedule %}<span class="schedule-tag">{{ log.schedule }}</span>{% endif %}</td>
                <td class="msg-cell">{{ log.message }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
  </div>
  <script>
    $(document).ready(function() {
        var table = $('#log-table').DataTable({
            order: [[0, 'desc']],
            pageLength: 50,
            language: {
                search: "_INPUT_",
                searchPlaceholder: "Filter logs..."
            }
        });

        // Fill filters
        function populateFilters() {
            var levels = [];
            var schedules = [];
            var runIds = [];

            table.rows().nodes().to$().each(function() {
                var level = $(this).find('td:eq(1)').text().trim();
                var runId = $(this).find('td:eq(2)').text().trim();
                var schedule = $(this).find('td:eq(3)').text().trim();

                if (level && levels.indexOf(level) === -1) levels.push(level);
                if (runId && runIds.indexOf(runId) === -1) runIds.push(runId);
                if (schedule && schedules.indexOf(schedule) === -1) schedules.push(schedule);
            });

            levels.sort().forEach(l => $('#level-filter').append('<option value="' + l + '">' + l + '</option>'));
            schedules.sort().forEach(s => $('#schedule-filter').append('<option value="' + s + '">' + s + '</option>'));
            runIds.sort().reverse().forEach(r => $('#run-ids').append('<option value="' + r + '">'));
        }

        populateFilters();

        $('#level-filter, #schedule-filter').on('change', function() {
            var colIdx = this.id === 'level-filter' ? 1 : 3;
            var val = $(this).val();
            table.column(colIdx).search(val ? '^' + val + '$' : '', true, false).draw();
        });

        $('#run-id-filter').on('input change', function() {
            var val = $(this).val();
            table.column(2).search(val ? '^' + val + '$' : '', true, false).draw();
        });
    });
  </script>
</body>
</html>
"""


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
    labels = [
        "com.berttaylor.class_booker",
        "com.berttaylor.class_booker.teachers",
        "com.berttaylor.class_booker.web",
    ]
    services = [_get_service_status(label) for label in labels]

    return render_template_string(
        INDEX_PAGE, schedules=schedules, logs=logs, services=services
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
    return render_template_string(PAGE, name=name, content=content)


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

    return render_template_string(LOGS_PAGE, name=name, logs=logs[-1000:])


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
