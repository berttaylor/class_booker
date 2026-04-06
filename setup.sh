#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_PLIST="com.$(whoami).$(basename "$SCRIPT_DIR")"
LOG_OUT="$SCRIPT_DIR/logs/main.log"
LOG_ERR="$SCRIPT_DIR/logs/error.log"
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

# scheduling_rules/
mkdir -p "$SCRIPT_DIR/scheduling_rules"
if compgen -G "$SCRIPT_DIR/scheduling_rules/*.yml" > /dev/null 2>&1; then
    echo "✓ scheduling_rules/ already has files — skipping example"
else
    cat > "$SCRIPT_DIR/scheduling_rules/example.yml" <<'TMPL'
timezone: Europe/London

settings:
  is_active: false

credentials:
  email: YOUR_EMAIL
  password: YOUR_PASSWORD

rules:
  # Each rule books one session per week at a fixed day and time.
  # Use the "+ Rule" button in the web editor to add rules, or copy this block.
  #
  # - weekday: mon            # mon, tue, wed, thu, fri, sat, sun
  #   start_time: "13:00"    # on the hour or half-hour, e.g. "09:00", "13:30", "18:00"
  #   enabled: true           # false = skip without deleting
  #   slots: 1               # 1 = 30 min, 2 = 1 hour
  #   preferred_teachers:    # in priority order — must match names exactly as shown on the platform
  #     - "Teacher Name"
  #   label: midday          # (optional) short name — must be unique per weekday if start_times are same
TMPL
    echo "✓ Created scheduling_rules/example.yml"
    echo "  → Open the web editor and fill in your credentials and rules"
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
echo "  tail -f logs/main.log          # watch live logs"
echo "  python main.py run-due --force-soft   # dry-run the next upcoming rule"
echo "  python main.py populate-teachers      # manual teacher refresh"
echo "  python web.py                         # open schedule editor in browser"
echo "  launchctl list | grep $(basename "$SCRIPT_DIR")  # check service status"
