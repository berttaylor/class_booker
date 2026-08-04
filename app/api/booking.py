from datetime import datetime as dt, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytz

from app import logger
from app.client import BookingClient
from app.config import app_config

SLOT_MINUTES = 30


def _api_ts(d: dt) -> str:
    """Formats a datetime as the millisecond UTC string the API expects."""
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_focus(client: BookingClient) -> Tuple[Optional[str], Optional[int]]:
    """
    Returns (focus_type, activity_suggestion_id) from the student's suggested
    activities, or (None, None) if unavailable.

    The web UI sends these with every confirm; whether they are required is
    unconfirmed. Degrading to (None, None) means a broken activities endpoint
    costs us the lesson topic, not the booking.
    """
    try:
        response = client.get(app_config.activities_endpoint)
        if response.status_code != 200:
            logger.warning(f"Activities fetch failed: {response.status_code}")
            return None, None

        activities = response.json().get("data", [])
        if not activities:
            return None, None

        top = activities[0]
        return (top.get("learning_goal", {}).get("title"), top.get("id"))
    except Exception as e:
        logger.warning(f"Could not fetch activities: {e}")
        return None, None


def get_bookings(client: BookingClient) -> List[Dict[str, Any]]:
    """
    Fetches upcoming classes, normalised to the flat shape the scheduler uses:
    staff_id / date / start_time (local) / status / past.

    date and start_time are converted from the API's UTC into the configured
    timezone, because the scheduler compares them against local-time rule slots.
    """
    local_tz = pytz.timezone(app_config.timezone)
    bookings = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        response = client.get(
            app_config.my_classes_endpoint,
            params={"tab": "upcoming", "page": page, "per_page": 50},
        )

        if response.status_code != 200:
            logger.error(f"Failed to fetch bookings. Status: {response.status_code}")
            return []

        try:
            payload = response.json()
        except Exception as e:
            logger.error(f"Error parsing bookings response: {e}")
            return []

        for c in payload.get("data", []):
            # Skip only the offending entry: returning [] for one bad record
            # would read as "nothing booked" and invite a double-booking.
            try:
                local_dt = dt.fromisoformat(
                    c["date_time"].replace("Z", "+00:00")
                ).astimezone(local_tz)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                logger.warning(f"Skipping unparseable booking {c.get('id')}: {e}")
                continue

            bookings.append(
                {
                    "staff_id": str(c.get("tutor_id")),
                    "date": local_dt.strftime("%Y-%m-%d"),
                    "start_time": local_dt.strftime("%H:%M:00"),
                    "status": "approved"
                    if c.get("status") == "upcoming"
                    else c.get("status"),
                    "past": False,
                    "booking_id": c.get("id"),
                    "duration_minutes": c.get("duration_minutes") or 30,
                }
            )

        total_pages = (payload.get("meta") or {}).get("total_pages", 1)
        page += 1

    return bookings


def cancel_booking(client: BookingClient, booking_id: str) -> Dict[str, Any]:
    """
    Cancels a specific booking by ID.
    """
    url = f"{app_config.cancel_booking_endpoint}/{booking_id}/cancel"
    response = client.post(url, json={})

    if response.status_code != 200:
        return {
            "status": "error",
            "message": f"HTTP Error {response.status_code}: {response.text}",
        }

    try:
        return {"status": "success", **response.json()}
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse cancel response: {e}"}


def book_lesson(
    client: BookingClient,
    teacher_id: str,
    lesson_datetime: str,
    focus_type: Optional[str] = None,
    activity_suggestion_id: Optional[int] = None,
    duration_minutes: int = SLOT_MINUTES,
) -> Dict[str, Any]:
    """
    Books a lesson via the two-step hold-then-confirm flow.

    duration_minutes may exceed one 30-minute slot: the API books a single
    longer lesson spanning consecutive slots, so a 60-minute class is one
    request rather than two.

    Success is HTTP 200 — neither endpoint returns a status field. If the hold
    succeeds but the confirm fails the slot stays held until it expires (~5 min),
    which blocks retries on that slot, so that case is logged loudly.
    """
    local_tz = pytz.timezone(app_config.timezone)

    try:
        start_dt = dt.fromisoformat(lesson_datetime.replace("Z", "+00:00"))
        start_utc = start_dt.astimezone(timezone.utc)
        end_utc = start_utc + timedelta(minutes=duration_minutes)

        logger.info(
            f"Booking {duration_minutes}min class for "
            f"{start_dt.astimezone(local_tz).strftime('%H:%M')} "
            f"Spain time ({start_utc.strftime('%H:%M')} UTC)"
        )

        slot = {
            "tutor_id": str(teacher_id),
            "start_time": _api_ts(start_utc),
            "end_time": _api_ts(end_utc),
        }

        hold = client.post(app_config.hold_endpoint, json=slot)
        if hold.status_code != 200:
            return {
                "status": "error",
                "status_code": hold.status_code,
                "message": f"Hold failed — HTTP {hold.status_code}: {hold.text}",
            }

        payload = {**slot, "duration_minutes": duration_minutes}
        if focus_type and activity_suggestion_id:
            payload["focus_type"] = focus_type
            payload["activity_suggestion_id"] = activity_suggestion_id

        confirm = client.post(app_config.confirm_endpoint, json=payload)
        if confirm.status_code != 200:
            logger.error(
                f"Slot held but confirm failed — slot stays locked until it expires. "
                f"HTTP {confirm.status_code}: {confirm.text}"
            )
            return {
                "status": "error",
                "status_code": confirm.status_code,
                "message": f"Confirm failed — HTTP {confirm.status_code}: {confirm.text}",
            }

        return {"status": "success", **confirm.json()}

    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}
