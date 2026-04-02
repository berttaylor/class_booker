import pytest
import httpx

def test_external_call_fails():
    """This test should fail because sockets are disabled."""
    with pytest.raises(Exception) as excinfo:
        httpx.get("https://google.com")
    
    # pytest-socket raises SocketBlockedError which is a subclass of RuntimeError
    # or sometimes it just says "Socket access is disabled"
    assert "socket" in str(excinfo.value).lower() or "blocked" in str(excinfo.value).lower()
