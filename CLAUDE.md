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

**Module layout:**
```
app/
  client.py          — BookingClient: thin httpx wrapper (shared by everything)
  config.py          — AppConfig (from config.yaml), Settings (from .env)
  notion.py          — All Notion API calls (teachers sync, schedule fetch, run log)
  rules.py           — Pydantic models for scheduling_rules.yml
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
```

**Request flow:** CLI commands use `authed_client()` from `services/session.py` as a context manager — it creates a `BookingClient`, calls `login()`, sets the token, yields the client, and closes it on exit. All HTTP calls go through `BookingClient` → httpx → the API.

**Authentication:** `services/session.py` is the single place for auth lifecycle. `authed_client()` handles login and client setup; `ensure_fresh_token()` handles mid-session re-auth on 401s. All `api/` functions are stateless — they just take a client and call an endpoint.

**Configuration layers:**
- `config.yaml` — API base URL and endpoint paths → `AppConfig` via `app/config.py`
- `.env` — login credentials, optional Notion tokens → `Settings`
- `scheduling_rules.yml` — automated booking rules → `SchedulingRules` via `app/rules.py` (local cache, overwritten from Notion on each `run-due`)

**Notion integration** (`app/notion.py`): optional, activated by setting env vars. All Notion calls follow the same pattern — credential check first (silent no-op if not configured), try/except wrapper, never raises. Three databases:
- `NOTION_TEACHERS_DATABASE_ID` — teachers are dual-written here on `populate-teachers` (PATCH existing, POST new, skip unchanged)
- `NOTION_SCHEDULE_DATABASE_ID` — schedule is fetched on every `run-due` and written to `scheduling_rules.yml` before `load_scheduling_rules()` runs; falls back to existing YAML if Notion unreachable
- `NOTION_RUN_LOG_DATABASE_ID` — a row is created for every notable run outcome (Booked, Failed, Error); silent runs are not logged

**`BookingRule` schema** (`app/rules.py`): each rule has `label` (e.g. `"Monday Midday"` — matches the Name column in the Notion Schedule database), `weekday` (single string, e.g. `"mon"`), `start_time` (HH:MM, on the hour or half-hour), `slots` (1 or 2), `preferred_teachers` (list of teacher name strings, default `[]`), and `allow_fallbacks`. The `id` property is computed as `f"{weekday}_{label}"`. `slot_times()` expands to `["13:00"]` or `["13:00", "13:30"]` depending on `slots`. Pydantic validators enforce all constraints at load time.

**Teacher cache** (`app/teachers.py`): `teachers.json` (gitignored, project root) maps teacher name → `{id, is_favorite, status}`. Names are never deleted — absent teachers are marked `REMOVED`. Updated by `list-tutors` and the `populate-teachers` CLI command. `run-due` checks the cache on startup: exits with a message if missing, auto-refreshes if older than `UPDATE_TEACHERS_FREQUENCY_DAYS` (default 7, set in `.env`), and raises a `ValueError` if any name in the rules is unknown.

**Scheduler** (`services/scheduler.py` → `run_due_process`): two-phase design — Phase 1 uses the local clock only to check if any rule is due (no API calls); Phase 2 authenticates and syncs server time only when a booking is actually due. `_evaluate_rules` expands each rule into individual slot entries keyed by `slot_key` (e.g. `wed_midday_slot1`), returning `(rule, slot_key)` tuples in `due_rules` and dicts keyed by `slot_key`. Uses a file lock (`.run_due.lock`) to prevent concurrent runs.

## Testing

Tests use `respx` to mock all `httpx` calls and `pytest-socket` to block real network connections (enforced globally via `--disable-socket` in `pytest.ini`).

**`tests/base.py`** defines `BaseTest` — all test classes that make HTTP calls inherit from it. `BaseTest.setup_method` creates `self.mock_client` (a `BookingClient` pointed at `TEST_BASE_URL = "http://localhost:9999"`) and `self.router` (a `respx` mock router). This ensures tests can never accidentally hit the real API. Classes only testing pure logic (e.g. `TestNormalizeDatetime`, `TestFormatCalendar`) do not inherit `BaseTest`.

When adding HTTP-touching test classes, inherit `BaseTest` and use `self.mock_client`/`self.router`. If tests need an authenticated client, call `self.mock_client.set_token(...)` in `setup_method`.

Scheduler tests patch `sched_module` (imported as `import app.services.scheduler as sched_module`) and must include `patch.object(sched_module, "is_token_expired", return_value=False)` to prevent the post-wait re-auth check from hitting the network.

Test fixtures (`calendar_response`, `tutors_response`, `bookings_response`) are loaded from JSON files in `tests/fixtures/` and injected via `conftest.py`.
