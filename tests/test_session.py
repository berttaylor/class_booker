import pytest

from tests.base import TEST_BASE_URL
from app.services import session as session_module
from app.services.session import ensure_fresh_token
from app.client import BookingClient

_VALID_TOKEN = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig"
_LOGIN_SUCCESS = {"status": "success", "access_token": _VALID_TOKEN}
_LOGIN_FAIL = {"status": "error", "message": "Invalid credentials"}


class TestAuthedClient:
    def test_yields_client_with_token_on_success(self, monkeypatch):
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: _VALID_TOKEN)
        monkeypatch.setattr(session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})())

        from app.services.session import authed_client
        with authed_client() as client:
            assert client.client.headers.get("Authorization") == f"Bearer {_VALID_TOKEN}"

    def test_raises_on_login_failure(self, monkeypatch):
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: None)
        monkeypatch.setattr(session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})())

        from app.services.session import authed_client
        with pytest.raises(RuntimeError, match="Authentication failed"):
            with authed_client() as _:
                pass

    def test_client_closed_after_exit(self, monkeypatch):
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: _VALID_TOKEN)
        monkeypatch.setattr(session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})())

        from app.services.session import authed_client
        with authed_client() as client:
            inner_client = client

        assert inner_client.client.is_closed

    def test_client_closed_even_on_exception(self, monkeypatch):
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: _VALID_TOKEN)
        monkeypatch.setattr(session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})())

        from app.services.session import authed_client
        inner_client = None
        with pytest.raises(ValueError):
            with authed_client() as client:
                inner_client = client
                raise ValueError("something went wrong")

        assert inner_client.client.is_closed

    def test_use_cache_false_passed_to_login(self, monkeypatch):
        calls = []
        def mock_login(client, use_cache=True):
            calls.append(use_cache)
            return _VALID_TOKEN

        monkeypatch.setattr(session_module, "login", mock_login)
        monkeypatch.setattr(session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})())

        from app.services.session import authed_client
        with authed_client(use_cache=False) as _:
            pass

        assert calls == [False]


class TestEnsureFreshToken:
    def test_updates_token_on_success(self, monkeypatch):
        new_token = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.new"
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: new_token)

        client = BookingClient(base_url=TEST_BASE_URL)
        client.set_token(_VALID_TOKEN)
        result = ensure_fresh_token(client)

        assert result is True
        assert client.client.headers.get("Authorization") == f"Bearer {new_token}"
        client.close()

    def test_returns_false_on_login_failure(self, monkeypatch):
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: None)

        client = BookingClient(base_url=TEST_BASE_URL)
        result = ensure_fresh_token(client)

        assert result is False
        client.close()

    def test_token_unchanged_on_failure(self, monkeypatch):
        monkeypatch.setattr(session_module, "login", lambda client, use_cache=True: None)

        client = BookingClient(base_url=TEST_BASE_URL)
        client.set_token(_VALID_TOKEN)
        ensure_fresh_token(client)

        assert client.client.headers.get("Authorization") == f"Bearer {_VALID_TOKEN}"
        client.close()

    def test_calls_login_with_use_cache_false(self, monkeypatch):
        calls = []
        def mock_login(client, use_cache=True):
            calls.append(use_cache)
            return _VALID_TOKEN

        monkeypatch.setattr(session_module, "login", mock_login)

        client = BookingClient(base_url=TEST_BASE_URL)
        ensure_fresh_token(client)
        client.close()

        assert calls == [False]
