from datetime import datetime as dt, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Any

from app.client import BookingClient
from app.config import app_config


def normalize_datetime(dt_str: str) -> str:
    """
    Normalizes a datetime string to UTC format: YYYY-MM-DDTHH:MM:00+00:00
    """
    try:
        d = dt.fromisoformat(dt_str.replace("Z", "+00:00"))
        d_utc = d.astimezone(timezone.utc)
        return d_utc.strftime("%Y-%m-%dT%H:%M:00+00:00")
    except Exception:
        return dt_str


def get_server_time(client: BookingClient) -> Dict[str, Any]:
    """
    Fetches the server time from the backend.

    The API has no time endpoint, so we read the HTTP Date header off a cheap
    request. That gives 1-second resolution — coarser than a dedicated endpoint,
    but the caller's half-RTT correction keeps it inside tolerance for the
    :00/:30 booking race.
    """
    response = client.get(app_config.quota_endpoint)

    if response.status_code != 200:
        return {
            "status": "error",
            "message": f"HTTP Error {response.status_code}: {response.text}",
        }

    date_header = response.headers.get("Date")
    if not date_header:
        return {"status": "error", "message": "No Date header in response"}

    try:
        server_dt = parsedate_to_datetime(date_header).astimezone(timezone.utc)
        return {"datetime": server_dt.strftime("%Y-%m-%dT%H:%M:%S")}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to parse Date header: {e}",
            "raw": date_header,
        }
