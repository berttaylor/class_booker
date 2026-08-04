# Shortcuts for the two compose modes. The dev overlay is gitignored and never
# auto-loaded, so it has to be named explicitly — these targets are the
# memorable way to do it.
DEV := docker compose -f compose.yml -f compose.dev.yml
PROD := docker compose -f compose.yml

.PHONY: up down logs dry-run test prod-up prod-down prod-logs compose.dev.yml

# compose.dev.yml is gitignored (it must never reach a server, where compose
# could load it and run the stack without Caddy). Create it from the template.
compose.dev.yml:
	@test -f $@ || { cp $@.example $@ && echo "created $@ from template"; }

# --- local -------------------------------------------------------------------

up: compose.dev.yml  ## Start web + caddy locally on http://localhost:8080 (Basic Auth on)
	$(DEV) up -d --build
	@echo "http://localhost:8080 — user: bert"

down: compose.dev.yml
	$(DEV) --profile manual down

logs: compose.dev.yml
	$(DEV) logs -f --tail 50

dry-run: compose.dev.yml  ## Exercise the scheduler without booking anything
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
