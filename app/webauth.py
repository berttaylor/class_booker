"""
Session-based login for the Flask web UI (see web.py).

Kept separate from route handlers, mirroring how api/auth.py and
services/session.py split auth concerns out of the booking client.
"""

from datetime import timedelta

from flask import redirect, render_template, request, session
from werkzeug.security import check_password_hash

from app.config import settings


def _safe_next(target: str | None) -> str:
    """Only same-site paths. "//evil.com" and "https://evil.com" are redirects
    off this site, so an attacker-supplied ?next= would turn the login page
    into an open redirect."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


def _require(name: str) -> str:
    value = getattr(settings, name)
    if not value:
        raise RuntimeError(f"{name.upper()} must be set in .env")
    return value


# Fail fast, same philosophy as compose's `:?` guards: a missing secret should
# stop the app from starting, not silently serve without auth.
SECRET_KEY = _require("secret_key")
AUTH_HASHES = {
    "admin": _require("auth_hash_admin"),
    "leigh": _require("auth_hash_leigh"),
}


def init_auth(app):
    app.secret_key = SECRET_KEY
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "static"):
            return
        if "user" not in session:
            return redirect(f"/login?next={request.path}")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            stored_hash = AUTH_HASHES.get(username)
            if stored_hash and check_password_hash(stored_hash, password):
                session.permanent = True
                session["user"] = username
                return redirect(_safe_next(request.form.get("next")))
            error = "Invalid username or password"
        return render_template(
            "login.html", error=error, next=_safe_next(request.args.get("next"))
        )

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect("/login")
