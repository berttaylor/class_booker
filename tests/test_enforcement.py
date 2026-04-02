import pytest
import httpx
from pytest_socket import SocketBlockedError


def test_sockets_are_blocked():
    """Verifies --disable-socket in pytest.ini is active and blocking real network calls."""
    with pytest.raises(SocketBlockedError):
        httpx.get("https://google.com")
