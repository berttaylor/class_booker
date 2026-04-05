#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_PLIST="com.$(whoami).$(basename "$SCRIPT_DIR")"
LOG_OUT="$SCRIPT_DIR/logs/classbooker.log"
LOG_ERR="$SCRIPT_DIR/logs/classbooker.error.log"
RUNNERS_DIR="$SCRIPT_DIR/runners"

echo "=== Class Booker Setup ==="
echo "Service: $BASE_PLIST"
echo ""

# .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "✓ .env already exists — skipping"
else
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "✓ Created .env from .env.example"
    echo "  → Open .env and fill in your LOGIN_EMAIL and LOGIN_PASSWORD"
fi

# scheduling_rules/bert.yml
mkdir -p "$SCRIPT_DIR/scheduling_rules"
if [ -f "$SCRIPT_DIR/scheduling_rules/bert.yml" ]; then
    echo "✓ scheduling_rules/bert.yml already exists — skipping"
else
    cat > "$SCRIPT_DIR/scheduling_rules/bert.yml" <<'TMPL'
timezone: Europe/Madrid

booking:
  open_offset_days: 7
  open_offset_minutes: 30
  precheck_lead_seconds: 120

rules:
  # MONDAY
  - label: midday
    weekday: mon
    enabled: true
    start_time: "13:00"
    slots: 2
    preferred_teachers:
      - "Teacher Name"
      - "Another Teacher"
    allow_fallbacks: true

  - label: evening
    weekday: mon
    enabled: false
    start_time: "18:00"
    slots: 2
    allow_fallbacks: true

  # Add more rules following the same pattern.
  # label:              short name for the rule (e.g. "midday", "evening")
  # weekday:            one of mon, tue, wed, thu, fri, sat, sun
  # enabled:            true/false
  # start_time:         "HH:MM" - must be on the hour or half-hour
  # slots:              1 or 2 consecutive 30-min bookings starting at start_time
  # preferred_teachers: optional. teacher names in priority order - must match names in data/teachers.json exactly
  #                     run `python main.py populate-teachers` to generate data/teachers.json.
  # allow_fallbacks:    if true, fall back to any available teacher when preferred are unavailable
TMPL
    echo "✓ Created scheduling_rules/bert.yml from template"
    echo "  → Open scheduling_rules/bert.yml and configure your lesson schedule"
fi

# logs and runners directories
mkdir -p "$SCRIPT_DIR/logs"
mkdir -p "$RUNNERS_DIR"
echo "✓ Logs directory ready"
echo "✓ Runners directory ready"

# ── run-due plist (:29 and :59 every hour) ──────────────────────────────────
PLIST_RUN="$BASE_PLIST"
cat > "$RUNNERS_DIR/$PLIST_RUN.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_RUN</string>

    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/.venv/bin/python3</string>
        <string>$SCRIPT_DIR/main.py</string>
        <string>run-due</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Minute</key>
            <integer>29</integer>
        </dict>
        <dict>
            <key>Minute</key>
            <integer>59</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>$LOG_OUT</string>

    <key>StandardErrorPath</key>
    <string>$LOG_ERR</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF
echo "✓ Generated runners/$PLIST_RUN.plist  (run-due at :29 and :59)"

# ── populate-teachers plist (daily at 03:00) ─────────────────────────────────
PLIST_TEACHERS="$BASE_PLIST.teachers"
cat > "$RUNNERS_DIR/$PLIST_TEACHERS.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_TEACHERS</string>

    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/.venv/bin/python3</string>
        <string>$SCRIPT_DIR/main.py</string>
        <string>populate-teachers</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>
            <integer>3</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>$LOG_OUT</string>

    <key>StandardErrorPath</key>
    <string>$LOG_ERR</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF
echo "✓ Generated runners/$PLIST_TEACHERS.plist  (populate-teachers daily at 03:00)"

# ── Load both into launchd ────────────────────────────────────────────────────
for PLIST in "$PLIST_RUN" "$PLIST_TEACHERS"; do
    DEST="$HOME/Library/LaunchAgents/$PLIST.plist"
    if launchctl list | grep -q "$PLIST"; then
        launchctl unload "$DEST" 2>/dev/null || true
    fi
    cp "$RUNNERS_DIR/$PLIST.plist" "$DEST"
    launchctl load "$DEST"
    echo "✓ Loaded $PLIST into launchd"
done

echo ""
echo "=== Setup complete ==="
echo ""
echo "Two scheduled jobs installed:"
echo "  run-due           — every hour at :29 and :59 (books due lessons)"
echo "  populate-teachers — daily at 03:00 (refreshes teacher list)"
echo ""
echo "Useful commands:"
echo "  tail -f logs/classbooker.log          # watch live logs"
echo "  python main.py run-due --force-soft   # dry-run the next upcoming rule"
echo "  python main.py populate-teachers      # manual teacher refresh"
echo "  python web.py                         # open schedule editor in browser"
echo "  launchctl list | grep $(basename "$SCRIPT_DIR")  # check service status"
