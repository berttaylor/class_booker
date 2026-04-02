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

**Request flow:** `cli.py` → `BookingClient` (httpx wrapper in `client.py`) → API endpoints defined in `config.yaml`. All CLI commands follow the pattern: instantiate `BookingClient(base_url=app_config.base_url)`, call `login()` to get a JWT, call `client.set_token()`, then call the relevant domain function.

**Configuration layers:**
- `config.yaml` — API base URL and endpoint paths, loaded into `AppConfig` via `app/config.py`
- `.env` — login credentials (`LOGIN_EMAIL`, `LOGIN_PASSWORD`), loaded into `Settings` (pydantic-settings)
- `scheduling_rules.yml` — automated booking rules, loaded into `SchedulingRules` via `app/rules.py`
- `app/config.py` exports `API_BASE_URL` (the real base URL as a Python constant), `app_config` (full config), and `settings` (credentials)

**Domain modules** (`auth.py`, `booking.py`, `availability.py`, `utils.py`) are stateless functions that each take a `BookingClient` as their first argument. They read endpoint paths from `app_config` (e.g. `app_config.login_endpoint`).

**Scheduler** (`scheduler.py` → `run_due_process`): evaluates all enabled rules from `scheduling_rules.yml`, calculates when each lesson's booking window opens (lesson time minus `open_offset_days`/`open_offset_minutes`), syncs with server time to correct for clock drift, then books the lesson when within `precheck_lead_seconds` of the window opening. Uses a file lock (`.run_due.lock`) to prevent concurrent runs.

**Token caching:** `auth.py` caches the JWT to `.token_cache.json`. CLI commands retry with a fresh login if the cached token appears to have caused a failure.

## Testing

Tests use `respx` to mock all `httpx` calls and `pytest-socket` to block real network connections (enforced globally via `--disable-socket` in `pytest.ini`).

**`tests/base.py`** defines `BaseTest` — all test classes that make HTTP calls inherit from it. `BaseTest.setup_method` creates `self.mock_client` (a `BookingClient` pointed at `TEST_BASE_URL = "http://localhost:9999"`) and `self.router` (a `respx` mock router). This ensures tests can never accidentally hit the real API. Classes only testing pure logic (e.g. `TestNormalizeDatetime`, `TestFormatCalendar`) do not inherit `BaseTest`.

When adding HTTP-touching test classes, inherit `BaseTest` and use `self.mock_client`/`self.router`. If tests need an authenticated client, call `self.mock_client.set_token(...)` in `setup_method`.

Test fixtures (`calendar_response`, `tutors_response`, `bookings_response`) are loaded from JSON files in `tests/fixtures/` and injected via `conftest.py`.
