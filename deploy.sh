#!/bin/bash
# Deploy to the VPS. Requires an ssh host alias named "booker" in ~/.ssh/config.
#
# Deploys stay on the SSH key deliberately: a git-pull trigger inside the web UI
# would be a remote code execution path guarded only by a password.
#
# -f compose.yml is explicit so no local overlay (compose.dev.yml, which
# publishes a port and parks Caddy and the scheduler behind a profile) can be
# picked up in production.
set -euo pipefail

HOST="${BOOKER_HOST:-booker}"
DIR="${BOOKER_DIR:-/srv/class_booker}"
COMPOSE="docker compose -f compose.yml"

echo "Deploying to $HOST:$DIR"
ssh "$HOST" "cd '$DIR' && git pull --ff-only && $COMPOSE up -d --build"

echo
echo "Services:"
ssh "$HOST" "cd '$DIR' && $COMPOSE ps"

echo
echo "Recent scheduler output:"
ssh "$HOST" "cd '$DIR' && $COMPOSE logs --tail 20 cron"
