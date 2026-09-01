from datetime import datetime as dt, timedelta
from typing import Any, Dict, List

import pytz

from app import logger
from app.client import BookingClient
from app.config import app_config
from app.utils import normalize_datetime


def get_tutors_map(client: BookingClient) -> Dict[str, Dict[str, Any]]:
    """
    Fetches all tutors and returns a mapping of ID to its data (name).

    /tutors is a standard Laravel paginator (per_page 9, ~11 pages), so we walk
    every page. Tutor objects sit at the top level of `data` — unlike
    /favorite-tutors, which nests them under a "tutor" key.
    """
    tutor_map: Dict[str, Dict[str, Any]] = {}
    page = 1
    last_page = 1

    while page <= last_page:
        response = client.get(app_config.tutors_list_endpoint, params={"page": page})
        if response.status_code != 200:
            logger.error(f"Failed to fetch tutors page {page}: {response.status_code}")
            return {}

        try:
            res_data = response.json()
        except Exception as e:
            logger.error(f"Error parsing tutors page {page}: {e}")
            return {}

        for tutor in res_data.get("data", []):
            tid = str(tutor.get("id"))
            name = tutor.get("name")
            if tid and name:
                tutor_map[tid] = {"name": name}

        last_page = res_data.get("last_page", 1)
        page += 1

    return tutor_map


def get_favorite_tutor_ids(client: BookingClient) -> set[str]:
    """
    Returns the ids of the student's favourite tutors, as strings.

    Worth having because /booking/favorites/calendar only ever advertises
    favourites: a tutor named in the rules but not favourited can never show
    availability, and the resulting failure is indistinguishable from a
    genuinely full calendar. Returns an empty set on error, which suppresses
    the diagnostic rather than inventing a wrong one.
    """
    ids: set[str] = set()
    page = 1
    last_page = 1

    while page <= last_page:
        response = client.get(
            app_config.favorite_tutors_endpoint, params={"page": page}
        )
        if response.status_code != 200:
            logger.warning(f"Favourites fetch failed: {response.status_code}")
            return set()

        try:
            res_data = response.json()
        except Exception as e:
            logger.warning(f"Error parsing favourites page {page}: {e}")
            return set()

        for entry in res_data.get("data", []):
            # tutor_id sits at the top level; the nested "tutor" object carries
            # the display fields we do not need here.
            tid = entry.get("tutor_id") or (entry.get("tutor") or {}).get("id")
            if tid is not None:
                ids.add(str(tid))

        last_page = res_data.get("last_page", 1)
        page += 1

    return ids


def _get_calendar_slots(client: BookingClient) -> List[Dict[str, Any]]:
    """
    Fetches the favourites calendar and flattens it into a single slot list.

    The response is keyed by date — {"data": {"2026-08-01": [slot, ...]}} — and
    each slot already carries its full UTC start_time, so the date keys add
    nothing once flattened. Covers ~9 days ahead, favourite tutors only.
    """
    response = client.get(app_config.calendar_endpoint)

    if response.status_code != 200:
        logger.error(f"Failed to fetch calendar. Status: {response.status_code}")
        return []

    try:
        by_date = response.json().get("data", {})
        return [slot for slots in by_date.values() for slot in slots]
    except Exception as e:
        logger.error(f"Error parsing calendar response: {e}")
        return []


def get_teacher_slots(client: BookingClient, teacher_id: str) -> list:
    """
    Fetches all availability slots for a specific teacher.
    """
    return [
        slot
        for slot in _get_calendar_slots(client)
        if str(slot.get("tutor_id")) == str(teacher_id)
    ]


def get_available_teachers(
    client: BookingClient,
    lesson_datetime: str,
    duration_minutes: int = 30,
    tutor_map: Dict[str, Dict[str, Any]] | None = None,
) -> list:
    """
    Returns a list of available teachers for a given lesson datetime.
    Each entry is a dict with 'id', 'name', and 'start_time_local'.

    The calendar only ever advertises 30-minute slots, so a longer lesson
    requires every consecutive half-hour it spans to be free — a teacher with
    only the first half open cannot take a 60-minute class.

    Pass `tutor_map` to skip the /tutors walk. It is 11 paginated requests and
    only supplies display names, so the scheduler fetches it once *before* the
    booking window opens rather than paying ~15s for it at the moment the race
    starts.
    """
    slots = _get_calendar_slots(client)
    if not slots:
        return []

    if tutor_map is None:
        tutor_map = get_tutors_map(client)
    target_utc = normalize_datetime(lesson_datetime)
    local_tz = pytz.timezone(app_config.timezone)

    # (tutor_id, normalised start) pairs that are open, for the span check below
    open_slots = {
        (str(s.get("tutor_id")), normalize_datetime(s.get("start_time", "")))
        for s in slots
        if s.get("status") == "available"
    }

    target_dt = dt.fromisoformat(target_utc)
    required = [
        normalize_datetime((target_dt + timedelta(minutes=offset)).isoformat())
        for offset in range(0, duration_minutes, 30)
    ]

    available_teachers = []
    seen = set()

    for slot in slots:
        if slot.get("status") != "available":
            continue
        if normalize_datetime(slot.get("start_time", "")) != target_utc:
            continue

        teacher_id = str(slot.get("tutor_id"))
        if teacher_id in seen:
            continue
        seen.add(teacher_id)

        if not all((teacher_id, start) in open_slots for start in required):
            continue

        name = tutor_map.get(teacher_id, {}).get("name", f"Teacher {teacher_id}")
        start_time_local = (
            dt.fromisoformat(slot["start_time"].replace("Z", "+00:00"))
            .astimezone(local_tz)
            .strftime("%H:%M")
        )
        available_teachers.append(
            {
                "id": teacher_id,
                "name": name,
                "start_time_local": start_time_local,
            }
        )

    return available_teachers
