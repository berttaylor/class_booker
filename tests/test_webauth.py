"""
Login gate for the web UI (app/webauth.py).

app/webauth.py reads the secrets off `settings` at import time, so they are
overridden on the instance here — setting environment variables would be too
late once another test module has already imported app.config.
"""

from werkzeug.security import generate_password_hash

from app.config import settings

PASSWORD = "test-password"

settings.secret_key = "test-secret"
settings.auth_hash_admin = generate_password_hash(PASSWORD)
settings.auth_hash_leigh = generate_password_hash(PASSWORD)

import web  # must be imported after the settings override above


class TestLoginGate:
    def setup_method(self):
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def _log_in(self, password=PASSWORD, **extra):
        return self.client.post(
            "/login", data={"username": "admin", "password": password, **extra}
        )

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get("/")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/login?next=/"

    def test_anonymous_cannot_read_the_api_or_save(self):
        # The editor's save endpoint writes booking credentials to disk, so it
        # must never be reachable without a session.
        assert self.client.get("/api/teachers").status_code == 302
        assert self.client.post("/schedules/bert/save", json={}).status_code == 302

    def test_wrong_password_does_not_create_a_session(self):
        assert self._log_in(password="wrong").status_code == 200
        assert self.client.get("/").status_code == 302

    def test_unknown_username_does_not_create_a_session(self):
        resp = self.client.post(
            "/login", data={"username": "nobody", "password": PASSWORD}
        )
        assert resp.status_code == 200
        assert self.client.get("/").status_code == 302

    def test_correct_password_grants_access_until_logout(self):
        assert self._log_in().headers["Location"] == "/"
        assert self.client.get("/").status_code == 200
        assert self.client.post("/logout").headers["Location"] == "/login"
        assert self.client.get("/").status_code == 302

    def test_next_cannot_redirect_off_site(self):
        # Otherwise the login page is an open redirect: ?next=https://evil.example
        # sends a freshly authenticated user straight to an attacker's page.
        hostile_targets = ("https://evil.example", "//evil.example", "javascript:x")
        for hostile in hostile_targets:
            self.setup_method()
            assert self._log_in(next=hostile).headers["Location"] == "/"

    def test_next_preserves_an_internal_path(self):
        assert self._log_in(next="/logs/events").headers["Location"] == "/logs/events"
