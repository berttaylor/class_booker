import os
import json
from pathlib import Path

# Patch env BEFORE any app module is imported — avoids needing a real .env file.
# pydantic-settings reads these at class instantiation time (module load).
os.environ.setdefault("TEACHER_SYNC_LOGIN_EMAIL", "test@example.com")
os.environ.setdefault("TEACHER_SYNC_LOGIN_PASSWORD", "test-password-123")

import pytest
import respx

from app import logger
from app.client import BookingClient
from tests.base import TEST_BASE_URL

# Disable logging during tests
logger.set_enabled(False)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Real-data guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _never_touch_real_teacher_cache(tmp_path, monkeypatch):
    """
    Redirect the teacher cache into tmp_path for every test.

    data/teachers.json is real, gitignored, and only regenerable from the API,
    so a test that writes to it destroys live data. Tests that need their own
    location patch this again; this is the floor, not the mechanism.
    """
    monkeypatch.setattr(
        "app.teachers.TEACHERS_CACHE_PATH", tmp_path / "data" / "teachers.json"
    )


# ---------------------------------------------------------------------------
# HTTP client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """
    A BookingClient with all httpx calls intercepted by respx.

    respx.mock() patches httpx globally. base_url on the router means
    route paths like "/auth/login" are matched against the full request URL
    "https://api.dummy.com/api/auth/login".
    """
    with respx.mock(base_url=TEST_BASE_URL, assert_all_called=False) as router:
        # router.route().pass_through()  # Allow respx to let unhandled requests through so pytest-socket can catch them
        client = BookingClient(base_url=TEST_BASE_URL)
        yield client, router
        client.close()


@pytest.fixture
def authed_client(mock_client):
    client, router = mock_client
    # Use a token with a far-future exp so is_token_expired returns False
    client.set_token("header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig")
    return client, router


# ---------------------------------------------------------------------------
# API response fixtures (loaded from JSON files)
# ---------------------------------------------------------------------------


@pytest.fixture
def calendar_response():
    with open(FIXTURES_DIR / "calendar_response.json") as f:
        return json.load(f)


@pytest.fixture
def tutors_page1_response():
    with open(FIXTURES_DIR / "tutors_page1.json") as f:
        return json.load(f)


@pytest.fixture
def tutors_page2_response():
    with open(FIXTURES_DIR / "tutors_page2.json") as f:
        return json.load(f)


@pytest.fixture
def my_classes_response():
    with open(FIXTURES_DIR / "my_classes_response.json") as f:
        return json.load(f)


@pytest.fixture
def activities_response():
    with open(FIXTURES_DIR / "activities_response.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------


def make_jwt(exp: int) -> str:
    """Build a minimal JWT with the given exp Unix timestamp (no real signing)."""
    import base64

    header = (
        base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    )
    payload_data = json.dumps({"sub": "testuser", "exp": exp}).encode()
    payload = base64.urlsafe_b64encode(payload_data).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"
