# Shortcuts for the two compose modes. The dev overlay is gitignored and never
# auto-loaded, so it has to be named explicitly — these targets are the
# memorable way to do it.
DEV := docker compose -f compose.yml -f compose.dev.yml
PROD := docker compose -f compose.yml

.PHONY: up down logs dry-run test prod-up prod-down prod-logs compose.dev.yml hash

# Generate a login password hash for AUTH_HASH_ADMIN / AUTH_HASH_LEIGH in
# .env, already escaped for it (see .env.example for why escaping is needed).
# Runs through the project's own image so it uses the same Werkzeug that
# web.py checks against.
hash:
	@printf 'Password (not echoed): ' >&2
	@stty -echo; read -r p; stty echo; printf '\n' >&2; \
	  $(PROD) run --rm -e HASH_PW="$$p" web python -c \
	    "import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ['HASH_PW']))" \
	    | sed 's/\$$/$$$$/g'

# compose.dev.yml is gitignored (it must never reach a server, where compose
# could load it and publish a port or park the scheduler). Create it from the
# template.
compose.dev.yml:
	@test -f $@ || { cp $@.example $@ && echo "created $@ from template"; }

# --- local -------------------------------------------------------------------

up: compose.dev.yml  ## Start the web UI on http://127.0.0.1:8008 (login required)
	$(DEV) up -d --build
	@echo "http://127.0.0.1:8008 — log in as admin or leigh"

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
