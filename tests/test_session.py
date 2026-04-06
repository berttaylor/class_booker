import pytest

from tests.base import TEST_BASE_URL
from app.services import session as session_module
from app.services.session import master_client

_VALID_TOKEN = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig"
_LOGIN_SUCCESS = {"status": "success", "access_token": _VALID_TOKEN}
_LOGIN_FAIL = {"status": "error", "message": "Invalid credentials"}


class TestMasterClient:
    def test_yields_client_with_token_on_success(self, monkeypatch):
        monkeypatch.setattr(
            session_module,
            "login",
            lambda client, creds, cache_file, use_cache=True: _VALID_TOKEN,
        )
        monkeypatch.setattr(
            session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})()
        )

        with master_client() as client:
            assert (
                client.client.headers.get("Authorization") == f"Bearer {_VALID_TOKEN}"
            )

    def test_raises_on_login_failure(self, monkeypatch):
        monkeypatch.setattr(
            session_module,
            "login",
            lambda client, creds, cache_file, use_cache=True: None,
        )
        monkeypatch.setattr(
            session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})()
        )

        with pytest.raises(RuntimeError, match="Authentication failed"):
            with master_client() as _:
                pass

    def test_client_closed_after_exit(self, monkeypatch):
        monkeypatch.setattr(
            session_module,
            "login",
            lambda client, creds, cache_file, use_cache=True: _VALID_TOKEN,
        )
        monkeypatch.setattr(
            session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})()
        )

        with master_client() as client:
            inner_client = client

        assert inner_client.client.is_closed

    def test_client_closed_even_on_exception(self, monkeypatch):
        monkeypatch.setattr(
            session_module,
            "login",
            lambda client, creds, cache_file, use_cache=True: _VALID_TOKEN,
        )
        monkeypatch.setattr(
            session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})()
        )

        inner_client = None
        with pytest.raises(ValueError):
            with master_client() as client:
                inner_client = client
                raise ValueError("something went wrong")

        assert inner_client.client.is_closed

    def test_use_cache_false_passed_to_login(self, monkeypatch):
        calls = []

        def mock_login(client, creds, cache_file, use_cache=True):
            calls.append(use_cache)
            return _VALID_TOKEN

        monkeypatch.setattr(session_module, "login", mock_login)
        monkeypatch.setattr(
            session_module, "app_config", type("C", (), {"base_url": TEST_BASE_URL})()
        )

        with master_client(use_cache=False) as _:
            pass

        assert calls == [False]
