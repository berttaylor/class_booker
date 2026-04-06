"""
Schedule editor web UI.
Run with: python web.py
Then open http://localhost:5001/schedules/bert in your browser.
"""

import re
from flask import Flask, abort, request, jsonify, render_template_string
from pathlib import Path
import yaml

from app.teachers import load_teacher_cache, validate_rules_against_cache

BASE_DIR = Path(__file__).parent
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

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
      <div id="status">unsaved</div>
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
    cm.setValue({{ content | tojson }});
    cm.setSize('100%', '100%');
    cm.on('change', () => setStatus('unsaved', ''));

    function setStatus(cls, msg) {
      const el = document.getElementById('status');
      el.className = cls;
      el.textContent = msg || 'unsaved';
    }

    function addRule() {
      const lines = [
        '',
        '  - label: new-session',
        '    enabled: true',
        '    weekday: mon',
        '    start_time: "13:00"',
        '    slots: 1',
        '    preferred_teachers:',
        '      - ""',
        '    allow_fallbacks: true',
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
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }
    header { padding: 16px 24px; background: #16213e; border-bottom: 1px solid #0f3460; display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 18px; font-weight: 600; }
    header { padding: 12px 16px; background: #16213e; border-bottom: 1px solid #0f3460; }
    header h1 { font-size: 16px; font-weight: 600; margin-bottom: 10px; }
    .toolbar { display: flex; align-items: center; gap: 8px; }
    a.home { padding: 8px 14px; background: #0f3460; border: 1px solid #1a5a8a; color: #eee; border-radius: 6px; font-size: 14px; text-decoration: none; white-space: nowrap; }
    a.home:hover { background: #1a5a8a; }
    .font-btns { display: flex; border: 1px solid #1a5a8a; border-radius: 6px; overflow: hidden; margin-left: auto; }
    .font-btn { padding: 6px 12px; background: #0f3460; border: none; color: #eee; cursor: pointer; font-size: 16px; line-height: 1; }
    .font-btn:hover { background: #1a5a8a; }
    .font-btn + .font-btn { border-left: 1px solid #1a5a8a; }
    #log-wrap { flex: 1; overflow: auto; padding: 16px 24px; }
    pre { font-family: monospace; font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; color: #ccc; }
  </style>
</head>
<body>
  <header>
    <h1>{{ name | capitalize }}</h1>
    <div class="toolbar">
      <a class="home" href="/">&#8592; Home</a>
      <div class="font-btns">
        <button class="font-btn" onclick="adjustFont(-1)">&#8722;</button>
        <button class="font-btn" onclick="adjustFont(1)">&#43;</button>
      </div>
    </div>
  </header>
  <div id="log-wrap">
    <pre id="log">{{ content }}</pre>
  </div>
  <script>
    let fontSize = 13;
    function adjustFont(delta) {
      fontSize = Math.max(8, Math.min(32, fontSize + delta));
      document.getElementById('log').style.fontSize = fontSize + 'px';
    }
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
    logs = sorted(p.stem for p in (BASE_DIR / "logs").glob("*.log"))
    return render_template_string(INDEX_PAGE, schedules=schedules, logs=logs)


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
            error=f"Two rules share the same day and label: {', '.join(dupes)}. Give them different labels.",
        )

    # Validate against teacher cache
    cache = load_teacher_cache()
    if cache:
        try:
            validate_rules_against_cache(rules, cache)
        except ValueError as e:
            return jsonify(ok=False, error=str(e))

    path.write_text(content)
    return jsonify(ok=True)


@app.route("/logs/<name>")
def view_log(name: str):
    _validate_name(name)
    path = BASE_DIR / "logs" / f"{name}.log"
    if not path.exists():
        abort(404)
    lines = path.read_text().splitlines()
    content = "\n".join(lines[-500:])
    return render_template_string(LOGS_PAGE, name=name, content=content)


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
    if "preferred_teachers" in r and "allow_fallbacks" in r:
        return (
            "If allow_fallbacks is false, you must list at least one preferred teacher."
        )
    if "credentials" in r:
        return "Missing credentials — add your email and password."
    if "field required" in r or "missing" in r:
        return "A required field is missing — check each rule has a label, weekday, start_time, slots, and allow_fallbacks."
    return "Something doesn't look right — check your rules and try again."


def _load_rules_from_dict(data: dict):
    from app.rules import SchedulingRules

    return SchedulingRules(**data)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
