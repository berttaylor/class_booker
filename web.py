"""
Schedule editor web UI.
Run with: python web.py
Then open http://localhost:5000 in your browser.
"""
from flask import Flask, request, jsonify, render_template_string
from pathlib import Path
import yaml

from app.rules import load_scheduling_rules
from app.teachers import load_teacher_cache, validate_rules_against_cache

RULES_PATH = Path(__file__).parent / "scheduling_rules.yml"

app = Flask(__name__)

PAGE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Schedule Editor</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/theme/material-darker.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/yaml/yaml.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; height: 100vh; display: flex; flex-direction: column; }
    header { padding: 16px 24px; background: #16213e; border-bottom: 1px solid #0f3460; display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 18px; font-weight: 600; }
    #status { font-size: 14px; padding: 6px 14px; border-radius: 6px; background: #0f3460; color: #aaa; }
    #status.ok { background: #1a4731; color: #4caf82; }
    #status.error { background: #4a1a1a; color: #f87171; }
    button { margin-left: auto; padding: 8px 20px; background: #0f3460; border: 1px solid #1a5a8a; color: #eee; border-radius: 6px; cursor: pointer; font-size: 14px; }
    button:hover { background: #1a5a8a; }
    .CodeMirror { flex: 1; height: 100%; font-size: 14px; line-height: 1.6; }
    #editor-wrap { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  </style>
</head>
<body>
  <header>
    <h1>scheduling_rules.yml</h1>
    <span id="status">unsaved</span>
    <button onclick="save()">Validate &amp; Save</button>
  </header>
  <div id="editor-wrap">
    <textarea id="editor">{{ content }}</textarea>
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
    cm.setSize('100%', '100%');
    cm.on('change', () => setStatus('unsaved', ''));

    function setStatus(cls, msg) {
      const el = document.getElementById('status');
      el.className = cls;
      el.textContent = msg || 'unsaved';
    }

    async function save() {
      setStatus('', 'saving...');
      const resp = await fetch('/save', {
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


@app.route("/")
def index():
    content = RULES_PATH.read_text() if RULES_PATH.exists() else ""
    return render_template_string(PAGE, content=content)


@app.route("/save", methods=["POST"])
def save():
    content = request.json.get("content", "")

    # Parse YAML
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return jsonify(ok=False, error=f"YAML error: {e}")

    # Validate rules
    try:
        rules = load_scheduling_rules.__wrapped__(data) if hasattr(load_scheduling_rules, '__wrapped__') else _load_rules_from_dict(data)
    except Exception as e:
        return jsonify(ok=False, error=str(e))

    # Check for duplicate rule IDs
    ids = [r.id for r in rules.rules]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        return jsonify(ok=False, error=f"Duplicate rule IDs: {', '.join(dupes)}")

    # Validate against teacher cache
    cache = load_teacher_cache()
    if cache:
        try:
            validate_rules_against_cache(rules, cache)
        except ValueError as e:
            return jsonify(ok=False, error=str(e))

    # Save
    RULES_PATH.write_text(content)
    return jsonify(ok=True)


def _load_rules_from_dict(data: dict):
    from app.rules import SchedulingRules
    return SchedulingRules(**data)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
