from contextlib import contextmanager
from typing import Generator

from app.api.auth import login
from app.client import BookingClient
from app.config import app_config


@contextmanager
def authed_client(use_cache: bool = True) -> Generator[BookingClient, None, None]:
    """
    Context manager that yields an authenticated BookingClient.
    Raises RuntimeError if login fails.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, use_cache=use_cache)
        if not token:
            raise RuntimeError("Authentication failed")
        client.set_token(token)
        yield client
    finally:
        client.close()


def ensure_fresh_token(client: BookingClient) -> bool:
    """
    Re-authenticates with use_cache=False and updates the client's token.
    Returns True on success, False otherwise.
    """
    token = login(client, use_cache=False)
    if token:
        client.set_token(token)
        return True
    return False
