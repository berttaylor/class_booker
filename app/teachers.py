import json
from datetime import date

from app import logger
from app.client import BookingClient
from app.api.availability import get_tutors_map
from app.config import settings

TEACHERS_CACHE_PATH = settings.teachers_cache_file


def load_teacher_cache() -> dict:
    """Returns the full cache dict, or {} if file is missing."""
    if not TEACHERS_CACHE_PATH.exists():
        return {}
    with open(TEACHERS_CACHE_PATH, "r") as f:
        return json.load(f)


def save_teacher_cache(cache: dict) -> None:
    """Writes cache to data/teachers.json with today's date in 'updated'."""
    cache["updated"] = date.today().isoformat()
    TEACHERS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TEACHERS_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def populate_teachers(client: BookingClient) -> None:
    """
    Fetches tutors from the API and merges into the existing cache.
    - New teachers: added as ACTIVE
    - Present in API response: status set to ACTIVE
    - Absent from API response: status set to REMOVED
    Saves the updated cache to data/teachers.json.
    """
    tutor_map = get_tutors_map(client)  # {id_str: {"name": ...}}

    cache = load_teacher_cache()
    teachers = cache.get("teachers", {})

    # Collapse the API response to name -> id.
    api_ids: dict[str, int] = {
        data["name"]: int(tid) for tid, data in tutor_map.items()
    }

    # Refresh id + status for every cached name, then add new ones. The id is
    # always overwritten so a namespace change on the platform self-heals.
    for name in teachers:
        if name in api_ids:
            teachers[name]["id"] = api_ids[name]
            teachers[name]["status"] = "ACTIVE"
        else:
            teachers[name]["status"] = "REMOVED"

    for name, tid in api_ids.items():
        if name not in teachers:
            teachers[name] = {"id": tid, "status": "ACTIVE"}

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
                logger.warning(
                    f"Teacher '{name}' in rule '{rule.id}' is marked REMOVED in data/teachers.json"
                )

    if unknown:
        raise ValueError(
            f"Unknown teacher names in scheduling rules: {', '.join(unknown)}"
        )
