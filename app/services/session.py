from contextlib import contextmanager
from typing import Generator

from app.api.auth import login, TOKEN_CACHE_FILE
from app.client import BookingClient
from app.config import app_config, settings


def _master_credentials() -> dict:
    return {
        "email": settings.teacher_sync_login_email,
        "password": settings.teacher_sync_login_password,
    }


@contextmanager
def master_client(use_cache: bool = True) -> Generator[BookingClient, None, None]:
    """
    Context manager for read-only CLI queries and teacher sync.
    Uses the master credentials from .env. Do NOT use for booking.
    Raises RuntimeError if login fails.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(
            client, _master_credentials(), TOKEN_CACHE_FILE, use_cache=use_cache
        )
        if not token:
            raise RuntimeError("Authentication failed")
        client.set_token(token)
        yield client
    finally:
        client.close()
