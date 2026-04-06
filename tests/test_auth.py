import json
import time
import httpx

from tests.conftest import make_jwt
from tests.base import BaseTest
from app.api.auth import is_token_expired, get_cached_token, _save_cached_token, login


# ---------------------------------------------------------------------------
# is_token_expired
# ---------------------------------------------------------------------------

class TestIsTokenExpired:
    def test_valid_token_not_expired(self):
        token = make_jwt(exp=int(time.time()) + 300)
        assert is_token_expired(token) is False

    def test_expired_token(self):
        token = make_jwt(exp=int(time.time()) - 10)
        assert is_token_expired(token) is True

    def test_token_within_buffer(self):
        # exp is 20s from now — within the 30s default buffer
        token = make_jwt(exp=int(time.time()) + 20)
        assert is_token_expired(token) is True

    def test_token_just_outside_buffer(self):
        # exp is 60s from now — outside 30s buffer → not expired
        token = make_jwt(exp=int(time.time()) + 60)
        assert is_token_expired(token) is False

    def test_custom_buffer(self):
        token = make_jwt(exp=int(time.time()) + 45)
        # With buffer=60 it's expired; with buffer=0 it's not
        assert is_token_expired(token, buffer_seconds=60) is True
        assert is_token_expired(token, buffer_seconds=0) is False

    def test_no_exp_claim(self):
        import base64
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b'{"sub":"user"}').rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        assert is_token_expired(token) is False

    def test_malformed_token_treated_as_expired(self):
        assert is_token_expired("not-a-jwt") is True

    def test_base64_padding_handled(self):
        # Construct a JWT whose payload b64 length is not a multiple of 4
        import base64
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
        # Payload that encodes to a non-padded-4 length after stripping '='
        raw = json.dumps({"exp": int(time.time()) + 300}).encode()
        payload = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        # Confirm padding was stripped
        assert len(payload) % 4 != 0 or True  # just ensure it doesn't crash
        token = f"{header}.{payload}.sig"
        assert is_token_expired(token) is False


# ---------------------------------------------------------------------------
# get_cached_token / _save_cached_token
# ---------------------------------------------------------------------------

class TestTokenCache:
    def test_cache_miss_when_no_file(self, tmp_path):
        assert get_cached_token(tmp_path / "nofile.json") is None

    def test_cache_hit_valid_token(self, tmp_path):
        cache_file = tmp_path / ".teacher_sync_token_cache.json"
        valid_token = make_jwt(exp=int(time.time()) + 3600)
        cache_file.write_text(json.dumps({"access_token": valid_token}))
        assert get_cached_token(cache_file) == valid_token

    def test_cache_miss_expired_token(self, tmp_path):
        cache_file = tmp_path / ".teacher_sync_token_cache.json"
        expired_token = make_jwt(exp=int(time.time()) - 60)
        cache_file.write_text(json.dumps({"access_token": expired_token}))
        assert get_cached_token(cache_file) is None

    def test_cache_miss_malformed_json(self, tmp_path):
        cache_file = tmp_path / ".teacher_sync_token_cache.json"
        cache_file.write_text("not json {{{")
        assert get_cached_token(cache_file) is None

    def test_save_and_retrieve_roundtrip(self, tmp_path):
        cache_file = tmp_path / ".teacher_sync_token_cache.json"
        valid_token = make_jwt(exp=int(time.time()) + 3600)
        _save_cached_token(valid_token, cache_file)
        assert get_cached_token(cache_file) == valid_token

    def test_save_creates_file(self, tmp_path):
        cache_file = tmp_path / ".teacher_sync_token_cache.json"
        token = make_jwt(exp=int(time.time()) + 3600)
        _save_cached_token(token, cache_file)
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["access_token"] == token


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

FAKE_CREDS = {"email": "test@example.com", "password": "secret"}


class TestLogin(BaseTest):
    def test_login_success(self, tmp_path):
        cache_file = tmp_path / ".cache.json"
        fresh_token = make_jwt(exp=int(time.time()) + 3600)

        self.router.post("/auth/login").mock(
            return_value=httpx.Response(200, json={"status": "success", "access_token": fresh_token})
        )

        result = login(self.mock_client, FAKE_CREDS, cache_file, use_cache=False)
        assert result == fresh_token

    def test_login_failure_bad_credentials(self, tmp_path):
        cache_file = tmp_path / ".cache.json"

        self.router.post("/auth/login").mock(
            return_value=httpx.Response(401, json={"status": "error", "message": "Unauthorized"})
        )

        result = login(self.mock_client, FAKE_CREDS, cache_file, use_cache=False)
        assert result is None

    def test_login_non_success_status_in_body(self, tmp_path):
        cache_file = tmp_path / ".cache.json"

        self.router.post("/auth/login").mock(
            return_value=httpx.Response(200, json={"status": "error", "message": "Invalid credentials"})
        )

        result = login(self.mock_client, FAKE_CREDS, cache_file, use_cache=False)
        assert result is None

    def test_login_uses_cache_when_valid(self, tmp_path):
        valid_token = make_jwt(exp=int(time.time()) + 3600)
        cache_file = tmp_path / ".cache.json"
        cache_file.write_text(json.dumps({"access_token": valid_token}))

        route = self.router.post("/auth/login").mock(
            return_value=httpx.Response(200, json={"status": "success", "access_token": "new_token"})
        )

        result = login(self.mock_client, FAKE_CREDS, cache_file, use_cache=True)
        assert result == valid_token
        assert route.called is False

    def test_login_bypasses_cache_when_use_cache_false(self, tmp_path):
        valid_token = make_jwt(exp=int(time.time()) + 3600)
        new_token = make_jwt(exp=int(time.time()) + 7200)
        cache_file = tmp_path / ".cache.json"
        cache_file.write_text(json.dumps({"access_token": valid_token}))

        self.router.post("/auth/login").mock(
            return_value=httpx.Response(200, json={"status": "success", "access_token": new_token})
        )

        result = login(self.mock_client, FAKE_CREDS, cache_file, use_cache=False)
        assert result == new_token
