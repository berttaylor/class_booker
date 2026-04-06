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
def authed_client(use_cache: bool = True) -> Generator[BookingClient, None, None]:
    """
    Context manager that yields an authenticated BookingClient.
    Uses the master credentials from .env and the shared token cache.
    Raises RuntimeError if login fails.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, _master_credentials(), TOKEN_CACHE_FILE, use_cache=use_cache)
        if not token:
            raise RuntimeError("Authentication failed")
        client.set_token(token)
        yield client
    finally:
        client.close()


def ensure_fresh_token(client: BookingClient) -> bool:
    """
    Re-authenticates with use_cache=False and updates the client's token.
    Uses master credentials from .env.
    Returns True on success, False otherwise.
    """
    token = login(client, _master_credentials(), TOKEN_CACHE_FILE, use_cache=False)
    if token:
        client.set_token(token)
        return True
    return False
