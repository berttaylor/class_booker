from datetime import datetime as dt, timedelta, timezone
from typing import Any, Dict, List

import pytz

from app import logger
from app.client import BookingClient
from app.config import app_config


def get_bookings(client: BookingClient) -> List[Dict[str, Any]]:
    """
    Fetches the list of bookings for the user.
    """
    payload = {"timezone": app_config.timezone}
    response = client.post(app_config.list_bookings_endpoint, json=payload)

    if response.status_code != 200:
        logger.error(f"Failed to fetch bookings. Status: {response.status_code}")
        return []

    try:
        res_json = response.json()
        if res_json.get("status") == "success":
            return res_json.get("data", [])
    except Exception as e:
        logger.error(f"Error parsing bookings response: {e}")

    return []


def cancel_booking(client: BookingClient, booking_id: str) -> Dict[str, Any]:
    """
    Cancels a specific booking by ID.
    """
    url = f"{app_config.cancel_booking_endpoint}/{booking_id}"
    response = client.post(url)

    if response.status_code != 200:
        return {
            "status": "error",
            "message": f"HTTP Error {response.status_code}: {response.text}",
        }

    try:
        return response.json()
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse cancel response: {e}"}


def book_lesson(
    client: BookingClient, teacher_id: str, lesson_datetime: str
) -> Dict[str, Any]:
    """
    Performs a booking request for a specific teacher and datetime.
    """
    local_tz = pytz.timezone(app_config.timezone)

    try:
        start_dt = dt.fromisoformat(lesson_datetime.replace("Z", "+00:00"))
        start_utc = start_dt.astimezone(timezone.utc)
        start_madrid = start_dt.astimezone(local_tz)

        logger.info(
            f"Booking class for {start_madrid.strftime('%H:%M')} Spain time ({start_utc.strftime('%H:%M')} UTC)"
        )

        start_utc_time = start_utc.strftime("%H:%M")
        end_utc_time = (start_utc + timedelta(minutes=30)).strftime("%H:%M")

        payload = {
            "service_id": 1,
            "staff_id": str(teacher_id),
            "date": start_madrid.strftime("%Y-%m-%d"),
            "start_time": start_utc_time,
            "end_time": end_utc_time,
            "number_of_people": 1,
            "status": "approved",
            "timezone": app_config.timezone,
            "group_session_id": None,
            "type_of_class": "let_tutor_decide",
        }

        response = client.post(app_config.booking_endpoint, json=payload)

        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"HTTP Error {response.status_code}: {response.text}",
            }

        return response.json()

    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}
