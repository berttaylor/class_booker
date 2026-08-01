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
```

## Architecture

This is a Python CLI tool (Typer) that automates booking Spanish classes on worldsacross.com by calling the platform's backend API directly.

**API:** `api-comunity.worldsacross.com/api` (the backend behind `preview.worldsacross.com`). The platform migrated here from the legacy `api.worldsacross.com` in August 2026 — see the `new-api` branch history for the old endpoints. Notable traits:

- **Booking is two calls:** `POST /booking/favorites/hold-slot` then `POST /booking/favorites/confirm`. Success is HTTP 200; neither returns a `status` field. If the hold succeeds and the confirm fails, the slot stays locked until `expires_at` (~5 min), which blocks retries.
- **Timezone travels in the `x-timezone` header**, not the request body.
- **Calendar** (`GET /booking/favorites/calendar`) is keyed by date, covers ~9 days, 30-minute slots, and returns **favourite tutors only**. `get_bookings()` normalises `/students/me/my-classes` back to the flat `staff_id`/`date`/`start_time` shape the scheduler expects, converting UTC to the configured timezone.
- **No server-time endpoint** — `get_server_time()` reads the HTTP `Date` header off `/students/me/quota` (1-second resolution).
- **`/tutors` is paginated** (Laravel paginator, ~9 per page); `get_tutors_map()` walks every page.
- **Confirm sends `focus_type` + `activity_suggestion_id`** from `/students/me/activities`. `get_focus()` picks the top activity once per run and degrades to omitting both if unavailable.

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
  services/
    session.py       — authed_client() context manager, ensure_fresh_token()
    scheduler.py     — run_due_process() + private helpers
  ui/
    calendar.py      — format_calendar()
web.py               — Flask schedule editor (browser UI with validation)
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

**Scheduled jobs** (three independent launchd jobs, all managed by `setup.sh`):
- `run-due` (:29, :59) — reads local `scheduling_rules/bert.yml`, checks for due bookings, books.
- `populate-teachers` (03:00 daily) — fetches tutors from the booking API, merges into `data/teachers.json`.
- `web-interface` (always online) — browser UI for editing rules and viewing logs.

**`BookingRule` schema** (`app/rules.py`): each rule has `weekday` (single string, e.g. `"mon"`), `start_time` (HH:MM, on the hour or half-hour), `slots` (1 or 2), and `preferred_teachers` (non-empty list of teacher name strings). An optional `label` can be provided; if missing, the `id` property is computed as `f"{weekday}_{start_time}"`. `slots` is a **duration multiplier, not a repeat count**: `duration_minutes` is `30 * slots`, so a 2-slot rule is one 60-minute booking rather than two consecutive 30-minute ones (the old API forced the split; this one takes `duration_minutes`). `slot_times()` therefore always returns a single start time. Pydantic validators enforce all constraints at load time.

**Teacher cache** (`app/teachers.py`): `data/teachers.json` (gitignored, project root) maps teacher name → `{id, status}`. Names are never deleted — absent teachers are marked `REMOVED`. Updated by `populate-teachers`. `run-due` checks the cache on startup: exits with a message if missing, and raises a `ValueError` if any name in the rules is unknown.

**Scheduler** (`services/scheduler.py` → `run_due_process`): two-phase design — Phase 1 uses the local clock only to check if any rule is due (no API calls); Phase 2 authenticates and syncs server time only when a booking is actually due. `_evaluate_rules` produces one entry per rule occurrence, keyed by `slot_key` (the rule id, e.g. `wed_13:00`), returning `(rule, slot_key)` tuples in `due_rules` and dicts keyed by `slot_key`. Uses a file lock (`.run_due.lock`) to prevent concurrent runs.

The booking window is `lesson - 7 days - 30 min`, computed in **UTC** and converted back to local time. Doing that arithmetic on the local wall clock shifts the window by an hour across a DST boundary — see `TestBookingWindowDST`.

`_is_already_booked` compares **time ranges**, not start times: recurring classes booked on the website are 60 minutes long, so a rule starting partway through one would otherwise double-book. It checks every booking regardless of date, so a lesson crossing midnight is caught.

**Schedule editor** (`web.py`): Flask app serving a CodeMirror YAML editor at `http://localhost:8008`. Validates against `SchedulingRules` schema, checks teacher names against `data/teachers.json`, and detects duplicate rule IDs before saving.

## Testing

Tests use `respx` to mock all `httpx` calls and `pytest-socket` to block real network connections (enforced globally via `--disable-socket` in `pytest.ini`).

**`tests/base.py`** defines `BaseTest` — all test classes that make HTTP calls inherit from it. `BaseTest.setup_method` creates `self.mock_client` (a `BookingClient` pointed at `TEST_BASE_URL = "http://localhost:9999"`) and `self.router` (a `respx` mock router). This ensures tests can never accidentally hit the real API. Classes only testing pure logic (e.g. `TestNormalizeDatetime`, `TestFormatCalendar`) do not inherit `BaseTest`.

When adding HTTP-touching test classes, inherit `BaseTest` and use `self.mock_client`/`self.router`. If tests need an authenticated client, call `self.mock_client.set_token(...)` in `setup_method`.

Scheduler tests patch `sched_module` (imported as `import app.services.scheduler as sched_module`) and must include `patch.object(sched_module, "is_token_expired", return_value=False)` to prevent the post-wait re-auth check from hitting the network.

Test fixtures (`calendar_response`, `tutors_page1_response`, `tutors_page2_response`, `my_classes_response`, `activities_response`) are loaded from JSON files in `tests/fixtures/` and injected via `conftest.py`. Most were captured from real API responses.
