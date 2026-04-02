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
  rules.py           — Pydantic models for scheduling_rules.yml
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
- `.env` — login credentials (`LOGIN_EMAIL`, `LOGIN_PASSWORD`) → `Settings`
- `scheduling_rules.yml` — automated booking rules → `SchedulingRules` via `app/rules.py`

**Scheduler** (`services/scheduler.py` → `run_due_process`): evaluates all enabled rules from `scheduling_rules.yml`, calculates when each lesson's booking window opens (lesson time minus `open_offset_days`/`open_offset_minutes`), syncs with server time to correct for clock drift, then books the lesson when within `precheck_lead_seconds` of the window opening. The main function is ~60 lines delegating to private helpers (`_evaluate_rules`, `_get_candidates`, `_wait_for_window`, `_attempt_booking`, etc.).

## Testing

Tests use `respx` to mock all `httpx` calls and `pytest-socket` to block real network connections (enforced globally via `--disable-socket` in `pytest.ini`).

**`tests/base.py`** defines `BaseTest` — all test classes that make HTTP calls inherit from it. `BaseTest.setup_method` creates `self.mock_client` (a `BookingClient` pointed at `TEST_BASE_URL = "http://localhost:9999"`) and `self.router` (a `respx` mock router). This ensures tests can never accidentally hit the real API. Classes only testing pure logic (e.g. `TestNormalizeDatetime`, `TestFormatCalendar`) do not inherit `BaseTest`.

When adding HTTP-touching test classes, inherit `BaseTest` and use `self.mock_client`/`self.router`. If tests need an authenticated client, call `self.mock_client.set_token(...)` in `setup_method`.

Scheduler tests patch `sched_module` (imported as `import app.services.scheduler as sched_module`) and must include `patch.object(sched_module, "is_token_expired", return_value=False)` to prevent the post-wait re-auth check from hitting the network.

Test fixtures (`calendar_response`, `tutors_response`, `bookings_response`) are loaded from JSON files in `tests/fixtures/` and injected via `conftest.py`.
