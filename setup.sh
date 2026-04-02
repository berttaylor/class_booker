#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.$(whoami).classbooker"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "=== Class Booker Setup ==="
echo ""

# .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "✓ .env already exists — skipping"
else
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "✓ Created .env from .env.example"
    echo "  → Open .env and fill in your LOGIN_EMAIL and LOGIN_PASSWORD"
fi

# scheduling_rules.yml
if [ -f "$SCRIPT_DIR/scheduling_rules.yml" ]; then
    echo "✓ scheduling_rules.yml already exists — skipping"
else
    cp "$SCRIPT_DIR/scheduling_rules.template.yml" "$SCRIPT_DIR/scheduling_rules.yml"
    echo "✓ Created scheduling_rules.yml from template"
    echo "  → Open scheduling_rules.yml and configure your lesson schedule"
fi

# logs directory
mkdir -p "$SCRIPT_DIR/logs"
echo "✓ Logs directory ready"

# plist
cat > "$SCRIPT_DIR/$PLIST_NAME.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>

    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/.venv/bin/python3</string>
        <string>$SCRIPT_DIR/main.py</string>
        <string>run-due</string>
        <string>--verbose</string>
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
    <string>$SCRIPT_DIR/logs/classbooker.log</string>

    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/logs/classbooker.error.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF
echo "✓ Generated $PLIST_NAME.plist"

# Load into launchd
if launchctl list | grep -q "$PLIST_NAME"; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi
cp "$SCRIPT_DIR/$PLIST_NAME.plist" "$PLIST_DEST"
launchctl load "$PLIST_DEST"
echo "✓ Loaded into launchd"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Edit scheduling_rules.yml with your lesson schedule"
echo ""
echo "Useful commands:"
echo "  tail -f logs/classbooker.log        # watch live logs"
echo "  python main.py run-due --verbose    # test a manual run"
echo "  launchctl list | grep classbooker   # check service status"
