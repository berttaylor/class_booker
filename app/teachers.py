import json
from datetime import date
from pathlib import Path

from app.client import BookingClient
from app.api.availability import get_tutors_map
from app.config import settings

TEACHERS_CACHE_PATH = Path(settings.teachers_cache_path)


def load_teacher_cache() -> dict:
    """Returns the full cache dict, or {} if file is missing."""
    if not TEACHERS_CACHE_PATH.exists():
        return {}
    with open(TEACHERS_CACHE_PATH, "r") as f:
        return json.load(f)


def save_teacher_cache(cache: dict) -> None:
    """Writes cache to teachers.json with today's date in 'updated'."""
    cache["updated"] = date.today().isoformat()
    with open(TEACHERS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def populate_teachers(client: BookingClient) -> None:
    """
    Fetches tutors from the API and merges into the existing cache.
    - New teachers: added as ACTIVE
    - Present in API response: status set to ACTIVE
    - Absent from API response: status set to REMOVED
    Saves the updated cache to teachers.json.
    """
    tutor_map = get_tutors_map(client)  # {id_str: {"name": ...}}

    cache = load_teacher_cache()
    teachers = cache.get("teachers", {})

    # Build set of names returned by the API
    api_names = {data["name"] for data in tutor_map.values()}

    # Mark existing entries ACTIVE or REMOVED based on API response
    for name in teachers:
        teachers[name]["status"] = "ACTIVE" if name in api_names else "REMOVED"

    # Add new teachers from the API
    for data in tutor_map.values():
        name = data["name"]
        if name not in teachers:
            teachers[name] = {
                "id": int(next(tid for tid, d in tutor_map.items() if d["name"] == name)),
                "status": "ACTIVE",
            }

    cache["teachers"] = teachers
    save_teacher_cache(cache)


def validate_rules_against_cache(rules_data, cache: dict) -> None:
    """
    Raises ValueError if any preferred_teachers name is not in the cache.
    Prints a warning (does not raise) for any name with status REMOVED.
    """
    teachers = cache.get("teachers", {})
    unknown = []
    for rule in rules_data.rules:
        if not rule.enabled:
            continue
        for name in rule.preferred_teachers:
            if name not in teachers:
                unknown.append(f"'{name}' (rule: {rule.id})")
            elif teachers[name]["status"] == "REMOVED":
                print(f"  Warning: '{name}' in rule '{rule.id}' is marked REMOVED in teachers.json")

    if unknown:
        raise ValueError(f"Unknown teacher names in scheduling rules: {', '.join(unknown)}")
