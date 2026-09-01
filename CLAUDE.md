# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
pytest

# Run a single test file
pytest tests/test_booking.py

# Run a single test class or method
pytest tests/test_booking.py::TestBookLessonPayload
pytest tests/test_booking.py::TestBookLessonPayload::test_payload_summer_cest

# Run with coverage
pytest --cov=app

# Run the CLI
python main.py <command>

# Local Docker stack — web only, no TLS, no real bookings (login still required)
make up                          # http://127.0.0.1:8008
make dry-run                     # run-due --force-soft in the cron container
make down

# Deploy (needs an ssh alias "booker")
./deploy.sh
```

## Architecture

This is a Python CLI tool (Typer) that automates booking Spanish classes on worldsacross.com by calling the platform's backend API directly.

**API:** `api-comunity.worldsacross.com/api` (the backend behind `preview.worldsacross.com`). The platform migrated here from the legacy `api.worldsacross.com` in August 2026 — see the `new-api` branch history for the old endpoints. Notable traits:

- **Booking is two calls:** `POST /booking/favorites/hold-slot` then `POST /booking/favorites/confirm`. Success is HTTP 200; neither returns a `status` field. If the hold succeeds and the confirm fails, the slot stays locked until `expires_at` (~5 min), which blocks retries.
- **Timezone travels in the `x-timezone` header**, not the request body.
- **Calendar** (`GET /booking/favorites/calendar`) is keyed by date, covers ~9 days, 30-minute slots, and returns **favourite tutors only**. `get_bookings()` normalises `/students/me/my-classes` back to the flat `staff_id`/`date`/`start_time` shape the scheduler expects, converting UTC to the configured timezone.
- **No server-time endpoint** — `get_server_time()` reads the HTTP `Date` header off `/students/me/quota` (1-second resolution).
- **`/tutors` is paginated** (Laravel paginator, ~9 per page); `get_tutors_map()` walks every page.
- **Confirm deliberately sends no `focus_type` / `activity_suggestion_id`**, so bookings land with `activity_id: null` and the tutor or coach picks the topic — the same state as a class booked on the website. It used to send `activities[0]` from `/students/me/activities`, but that list is ordered oldest-first, so every booking got tagged with the account's very first (already completed) activity and all the classes came out on one subject. `test_no_activity_is_ever_sent` guards it.

**Module layout:**
```
app/
  client.py          — BookingClient: thin httpx wrapper (shared by everything)
  config.py          — AppConfig (from config.yaml), Settings (from .env)
  rules.py           — Pydantic models for scheduling_rules/*.yml
  teachers.py        — Teacher cache load/save/validate, populate_teachers()
  utils.py           — normalize_datetime(), get_server_time()
  cli.py             — Typer commands (thin: no business logic)
  api/
    auth.py          — JWT validation, token cache, login()
    availability.py  — get_tutors_map(), get_teacher_slots(), get_available_teachers()
    booking.py       — get_bookings(), cancel_booking(), book_lesson()
  logger.py          — JSONL event log (logs/events.jsonl)
  runstate.py        — run outcome history (logs/runs.jsonl) for the status panel
  notifications.py   — send_push() via Pushover
  services/
    session.py       — authed_client() context manager, ensure_fresh_token()
    scheduler.py     — run_due_process() + private helpers
  ui/
    calendar.py      — format_calendar()
web.py               — Flask schedule editor + status panel
Dockerfile           — one image for both the web and cron services
compose.yml          — web (gunicorn) + cron (supercronic) + caddy (TLS/auth)
compose.dev.yml      — local dev overlay (gitignored; copied from .example by make)
Caddyfile            — HTTPS reverse proxy for classes.bertbert.work
crontab              — read by supercronic; :29/:59 run-due, 03:00 teacher sync
deploy.sh            — ssh booker 'git pull && docker compose up -d --build'
```

**Request flow:** CLI commands use `authed_client()` from `services/session.py` as a context manager — it creates a `BookingClient`, calls `login()`, sets the token, yields the client, and closes it on exit. All HTTP calls go through `BookingClient` → httpx → the API.

**Authentication:** `services/session.py` is the single place for auth lifecycle. `authed_client()` handles login and client setup; `ensure_fresh_token()` handles mid-session re-auth on 401s. All `api/` functions are stateless — they just take a client and call an endpoint.

**Configuration layers:**
- `config.yaml` — API base URL and endpoint paths → `AppConfig` via `app/config.py`
- `.env` — master login credentials (used by `populate-teachers`) and optional Pushover tokens → `Settings`
- `scheduling_rules/*.yml` — one file per user/schedule, each containing:
  - `credentials` — per-account email/password used for booking (supports multiple accounts in parallel)
  - `settings.is_active` — whether this schedule is currently active
  - `timezone`, `rules` — timezone and booking rules
  - Edit directly or via `python web.py`

**Deployment** (Docker Compose on a VPS at `classes.bertbert.work`; `setup.sh` and `runners/*.plist` are the superseded macOS launchd path):
- **web** — gunicorn, 2 workers. Not port-published; only Caddy reaches it, for TLS and defense in depth. The app enforces its **own login** (session cookie, see web.py) and must never be exposed directly regardless.
- **cron** — `supercronic /app/crontab`. Same image and volumes as web. `:29`/`:59` → `run-due`, `03:00` → `populate-teachers`.
- **caddy** — ports 80/443, automatic Let's Encrypt, reverse proxy only (no auth — that's Flask's job now). Certs live in a named volume; losing it means re-requesting and risking the rate limit.

Bind mounts (all gitignored, so they must exist on the host): `scheduling_rules/`, `data/`, `logs/`, `cache/`.

`.env` is **not** mounted — `env_file:` injects its contents as environment variables, so the file itself never enters the container and `chmod 600` on the host stays meaningful. `AUTH_HASH_ADMIN`/`AUTH_HASH_LEIGH` need every `$` doubled in `.env` (see `.env.example`); `make hash` handles it.

`TZ=Europe/Madrid` is set in the Dockerfile. The booking window is **local midnight** and the **cron trigger times are local** — without TZ both land in the wrong hour.

Local development: `make up` → `http://127.0.0.1:8008`, the same login page as production (no dev bypass — one auth code path everywhere). The overlay is `compose.dev.yml`, gitignored and **never auto-loaded** (compose only auto-loads the name `compose.override.yml`), so `make` passes it with `-f`; `make` also creates it from `compose.dev.yml.example` on first use. It does two things: publishes `web` on `127.0.0.1:8008`, and parks **caddy and cron** behind a `manual` profile. Publishing that port is only safe because auth lives in Flask — the port leads to the login page, not past it. Caddy is skipped locally because with auth out of it, all it adds is TLS a laptop cannot get for the real domain. `deploy.sh` passes `-f compose.yml` explicitly regardless.

**Monitoring** is layered, because each layer misses what the others catch:
- Pushover (`app/notifications.py`) — booking/auth failures and crashes, pushed immediately.
- Healthchecks.io — the crontab pings `HC_PING_URL` after every successful `run-due`. A non-zero exit skips the ping. This is the only layer that catches the box being dead, since a dead container cannot notify anything.
- Status panel staleness warning — same failure, but only when someone looks.

**`BookingRule` schema** (`app/rules.py`): each rule has `weekday` (single string, e.g. `"mon"`), `start_time` (HH:MM, on the hour or half-hour), `slots` (1 or 2), and `preferred_teachers` (non-empty list of teacher name strings). An optional `label` can be provided; if missing, the `id` property is computed as `f"{weekday}_{start_time}"`. `slots` is a **duration multiplier, not a repeat count**: `duration_minutes` is `30 * slots`, so a 2-slot rule is one 60-minute booking rather than two consecutive 30-minute ones (the old API forced the split; this one takes `duration_minutes`). `slot_times()` therefore always returns a single start time. Pydantic validators enforce all constraints at load time.

**Teacher cache** (`app/teachers.py`): `data/teachers.json` (gitignored, project root) maps teacher name → `{id, status}`. Names are never deleted — absent teachers are marked `REMOVED`. Updated by `populate-teachers`. `run-due` checks the cache on startup: exits with a message if missing, and raises a `ValueError` if any name in the rules is unknown.

**Scheduler** (`services/scheduler.py` → `run_due_process`): two-phase design — Phase 1 uses the local clock only to check if any rule is due (no API calls); Phase 2 authenticates and syncs server time only when a booking is actually due. `_evaluate_rules` produces one entry per rule occurrence, keyed by `slot_key` (the rule id, e.g. `wed_13:00`), returning `(rule, slot_key)` tuples in `due_rules` and dicts keyed by `slot_key`. Uses a file lock (`.run_due.lock`) to prevent concurrent runs.

**Run state** (`app/runstate.py` → `logs/runs.jsonl`, one JSON object per run): backs the status panel. web and cron are separate containers, so a shared file is the only channel between them; readers therefore never raise (a missing or torn file yields `{}`/`[]`).

Outcomes are derived from `_counts` — tallies incremented at the sites that already know what happened — never by re-parsing log messages. Precedence: `locked` → `crashed` → `blocked` → `failed` → `booked` → `nothing_due`. Two consequences worth knowing:
- **Partial success reports `failed`.** One schedule booking while another fails records `booked: 1, failed: 1` with outcome `failed`. A green light that hides a failure is the only outcome that costs a lesson.
- **`No suitable teachers available` is counted as a failure but still logged at INFO.** The counter is the source of truth; the log level is cosmetic and was left alone to avoid recolouring thousands of historical rows.

`--force-soft` cannot inflate the booked count: `_attempt_booking` returns at the `[DRY RUN]` branch before reaching the `BOOKED` site. A `locked` run deliberately writes **no** record, so the loser of a lock race can't clobber the panel while a real booking is mid-flight. A crash records `crashed`, pushes, and **re-raises** — the traceback must still reach stderr and the exit code must stay non-zero so the healthcheck ping is skipped.

`next_cron_run()` hardcodes the `:29`/`:59` pair from `crontab`. **Change both together** — and note the `:59` entry is what catches the midnight booking window.

**Logging** (`app/logger.py`): appends one JSON object per line to `logs/events.jsonl`. Append-only, so a killed process can at worst leave a single torn line, which the reader skips. `logs/main.json` is the pre-JSONL archive and stays readable in the log viewer; `view_log()` uses `deque(f, maxlen=1000)` so the tail renders without parsing the whole file.

The booking window is **local midnight on the day 7 days before the lesson** (plus `BOOKING_OPEN_BUFFER_SECONDS`, currently 2 — the window is a server-side day boundary, so aiming a hair past it is safer than hitting `00:00:00.000`). The platform opens a whole day's slots at once and leaves them open, so there is no per-lesson offset any more; before August 2026 it was `lesson - 7 days - 30 min` and the arithmetic was done in UTC on purpose, to keep an exact absolute offset. That reasoning is now inverted: midnight is a wall-clock concept, so the date is localised directly and the absolute gap to the lesson legitimately moves by an hour across a DST boundary — see `TestBookingWindowDST`.

Because the whole day opens at once, **every rule falling on the same weekday is due in the same run**. The `:59` crontab entry is the one that matters: at 23:59 the window is inside `BOOKING_PRECHECK_LEAD_SECONDS`, so `_wait_for_window` waits out the last minute and the rules book back to back from midnight (the first waits, the rest find the wait already elapsed). One missed 23:59 run would therefore cost that whole day's lessons, not one, so `BOOKING_OPEN_GRACE_MINUTES` (60) keeps a window actionable for an hour after it opens: the `:29` and `:59` runs after midnight retry anything the 23:59 run failed to book. The platform leaves the day bookable all week, so the grace only bounds how many times we retry — wider would keep retrying, and keep pushing failure alerts, for a week.

Availability is fetched **after** `_wait_for_window`, not before. Under the old timing the calendar already listed a slot half an hour ahead of bookability, so a pre-fetch worked by accident; with the window at midnight the pre-fetch runs ~19 hours early, returns nothing, and the run gives up without ever waiting — which is exactly how the first live midnight run failed. For the same reason the `/tutors` walk (11 pages, ~15s, display names only) is hoisted out of `get_available_teachers` via its `tutor_map` argument and fetched once beside `get_focus`, before the countdown.

`_evaluate_rules` looks 22 days ahead, not 15: the nearest still-bookable occurrence is up to 8 days out (today's midnight has usually passed), so two consecutive holidays on the same weekday put the next viable lesson at +22.

`_is_already_booked` compares **time ranges**, not start times: recurring classes booked on the website are 60 minutes long, so a rule starting partway through one would otherwise double-book. It checks every booking regardless of date, so a lesson crossing midnight is caught.

**Schedule editor and status panel** (`web.py`): Flask app serving a CodeMirror YAML editor. Validates against `SchedulingRules` schema, checks teacher names against `data/teachers.json`, and detects duplicate rule IDs before saving.

The index page renders the run history from `runs.jsonl` — last run, outcome, next check, next booking window, last booking, plus a table of recent runs. **Server-rendered Jinja only, no JavaScript**: refresh the page for current state. Pushover covers anything urgent, so live-updating the panel earned nothing and the SSE endpoint it needed was removed.

Templates fetch CodeMirror and DataTables from CDNs, so the editor and log viewer degrade without outbound internet access.

Schedule and log links must **not** capitalise the URL (`/schedules/{{ name }}`, display text capitalised separately): the files are lowercase and Linux is case-sensitive, so `/schedules/Bert` 404s in the container even though it worked on macOS.

## Testing

Tests use `respx` to mock all `httpx` calls and `pytest-socket` to block real network connections (enforced globally via `--disable-socket` in `pytest.ini`).

**`tests/base.py`** defines `BaseTest` — all test classes that make HTTP calls inherit from it. `BaseTest.setup_method` creates `self.mock_client` (a `BookingClient` pointed at `TEST_BASE_URL = "http://localhost:9999"`) and `self.router` (a `respx` mock router). This ensures tests can never accidentally hit the real API. Classes only testing pure logic (e.g. `TestNormalizeDatetime`, `TestFormatCalendar`) do not inherit `BaseTest`.

When adding HTTP-touching test classes, inherit `BaseTest` and use `self.mock_client`/`self.router`. If tests need an authenticated client, call `self.mock_client.set_token(...)` in `setup_method`.

Scheduler tests patch `sched_module` (imported as `import app.services.scheduler as sched_module`) and must include `patch.object(sched_module, "is_token_expired", return_value=False)` to prevent the post-wait re-auth check from hitting the network.

**`conftest.py` has an autouse fixture redirecting `app.teachers.TEACHERS_CACHE_PATH` into `tmp_path` for every test.** `data/teachers.json` is real, gitignored, and only regenerable from the API, so a test that writes to it destroys live data — which happened once when the path became absolute while tests still relied on `monkeypatch.chdir` to redirect it. Never remove that fixture; patch over it if a test needs its own location.

Run-state tests patch `sched_module.runstate.append` and assert on the record dict rather than touching the filesystem. `app/runstate.py` and `app/logger.py` both no-op under `PYTEST_CURRENT_TEST`, so tests never write to `logs/`.

Test fixtures (`calendar_response`, `tutors_page1_response`, `tutors_page2_response`, `my_classes_response`, `activities_response`) are loaded from JSON files in `tests/fixtures/` and injected via `conftest.py`. Most were captured from real API responses.
