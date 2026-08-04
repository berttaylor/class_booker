# Shortcuts for the two compose modes. The dev overlay is never auto-loaded, so
# it has to be named explicitly — these targets are the memorable way to do it.
DEV := docker compose -f compose.yml -f compose.dev.yml
PROD := docker compose -f compose.yml

.PHONY: up down logs dry-run test prod-up prod-down prod-logs

# --- local -------------------------------------------------------------------

up:  ## Start web + caddy locally on http://localhost:8080 (Basic Auth on)
	$(DEV) up -d --build
	@echo "http://localhost:8080 — user: bert"

down:
	$(DEV) --profile manual down

logs:
	$(DEV) logs -f --tail 50

dry-run:  ## Exercise the scheduler without booking anything
	$(DEV) run --rm cron python main.py run-due --force-soft

test:
	pytest

# --- production (on the VPS) -------------------------------------------------

prod-up:
	$(PROD) up -d --build

prod-down:
	$(PROD) down

prod-logs:
	$(PROD) logs -f --tail 50 cron
