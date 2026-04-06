from datetime import datetime as dt
from typing import Any, Dict

import pytz

from app import logger
from app.client import BookingClient
from app.config import app_config
from app.utils import normalize_datetime


def get_tutors_map(client: BookingClient) -> Dict[str, Dict[str, Any]]:
    """
    Fetches the list of tutors and returns a mapping of ID to its data (name).
    """
    response = client.get(app_config.tutors_list_endpoint)
    if response.status_code != 200:
        return {}

    try:
        res_data = response.json()
        tutors = res_data.get("data", [])

        tutor_map = {}
        for tutor in tutors:
            tid = str(tutor.get("id"))
            name = tutor.get("name")
            if tid and name:
                tutor_map[tid] = {"name": name}
        return tutor_map
    except Exception as e:
        logger.error(f"Error fetching tutors list: {e}")
        return {}


def get_teacher_slots(client: BookingClient, teacher_id: str) -> list:
    """
    Fetches all availability slots for a specific teacher.
    """
    data = {"duration": 60}
    response = client.post(app_config.availability_endpoint, json=data)

    if response.status_code != 200:
        return []

    try:
        res_json = response.json()
        service_data = res_json.get("1", {})
        if not service_data and isinstance(res_json, list):
            for item in res_json:
                if isinstance(item, dict) and "1" in item:
                    service_data = item["1"]
                    break

        teacher_slots = service_data.get(str(teacher_id), [])
        if not teacher_slots:
            teacher_slots = service_data.get(int(teacher_id), [])

        return teacher_slots
    except Exception as e:
        logger.error(f"Error fetching teacher slots: {e}")
        return []


def get_available_teachers(client: BookingClient, lesson_datetime: str) -> list:
    """
    Returns a list of available teachers for a given lesson datetime.
    Each entry is a dict with 'id', 'name', and 'start_time_local'.
    """
    tutor_map = get_tutors_map(client)
    target_utc = normalize_datetime(lesson_datetime)

    data = {"duration": 60}
    response = client.post(app_config.availability_endpoint, json=data)

    if response.status_code != 200:
        logger.error(f"Failed to fetch availability. Status: {response.status_code}")
        return []

    try:
        res_json = response.json()

        service_data = {}
        if isinstance(res_json, dict):
            service_data = res_json.get("1", {})
        elif isinstance(res_json, list):
            for item in res_json:
                if isinstance(item, dict) and "1" in item:
                    service_data = item["1"]
                    break

        available_teachers = []
        if isinstance(service_data, dict):
            for teacher_id, slots in service_data.items():
                if not isinstance(slots, list):
                    continue
                for slot in slots:
                    if (
                        slot.get("start_time") == target_utc
                        and slot.get("status") == "available"
                    ):
                        teacher_id_str = str(teacher_id)
                        tutor_data = tutor_map.get(teacher_id_str, {})
                        name = tutor_data.get("name", f"Teacher {teacher_id_str}")
                        local_tz = pytz.timezone(app_config.timezone)
                        start_time_local = (
                            dt.fromisoformat(slot["start_time"].replace("Z", "+00:00"))
                            .astimezone(local_tz)
                            .strftime("%H:%M")
                        )
                        available_teachers.append(
                            {
                                "id": teacher_id_str,
                                "name": name,
                                "start_time_local": start_time_local,
                            }
                        )
                        break
        return available_teachers
    except Exception as e:
        logger.error(f"Error parsing availability response: {e}")
        return []
