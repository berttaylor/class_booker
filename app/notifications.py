import httpx

from app.config import settings

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def send_push(message: str, title: str = "Class Booker", priority: int = 0) -> bool:
    """
    Sends a push notification via Pushover.
    Returns True on success, False on failure (never raises).
    Silently skips if credentials are not configured.

    priority: -1 = silent, 0 = normal, 1 = high (bypasses DND)
    """
    if not settings.pushover_user_key or not settings.pushover_api_token:
        return False

    try:
        response = httpx.post(PUSHOVER_API_URL, data={
            "token": settings.pushover_api_token,
            "user": settings.pushover_user_key,
            "title": title,
            "message": message,
            "priority": priority,
        }, timeout=10)
        return response.status_code == 200
    except Exception:
        return False
