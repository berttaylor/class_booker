import httpx
import time
import yaml
from datetime import date

from app.config import settings

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

NOTION_TEACHERS_FETCH_TIMEOUT = 10  # seconds
NOTION_SCHEDULE_FETCH_TIMEOUT = 5  # seconds


class NotionTeachersTimeoutError(Exception):
    pass


class NotionScheduleTimeoutError(Exception):
    pass


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.notion_api_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _teacher_properties(entry: dict, updated_date: str) -> dict:
    """Builds the Notion properties payload for a teacher entry."""
    return {
        "Platform ID": {"number": entry["id"]},
        "Status": {"select": {"name": entry.get("status", "ACTIVE")}},
        "Updated": {"date": {"start": updated_date}},
    }


def _extract_page_state(page: dict) -> dict:
    """
    Extracts the comparable state from a Notion page's properties.
    Returns a dict with the fields we care about for change detection.
    """
    props = page.get("properties", {})
    return {
        "platform_id": props.get("Platform ID", {}).get("number"),
        "status": (props.get("Status", {}).get("select") or {}).get("name"),
        "updated": (props.get("Updated", {}).get("date") or {}).get("start"),
    }


def _has_changes(page_state: dict, entry: dict, updated_date: str) -> bool:
    """Returns True if the cache entry differs from the current Notion page state."""
    return (
        page_state["platform_id"] != entry["id"]
        or page_state["status"] != entry.get("status", "ACTIVE")
        or page_state["updated"] != updated_date
    )


def _fetch_all_teacher_pages(database_id: str) -> dict[str, dict]:
    """
    Fetches all pages from the Teachers database in one paginated query.
    Returns a dict of {name: {"id": page_id, "state": {...}}} for all existing entries.
    Raises NotionTeachersTimeoutError if any page request exceeds NOTION_TEACHERS_FETCH_TIMEOUT.
    """
    existing = {}
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            response = httpx.post(
                f"{NOTION_API_BASE}/databases/{database_id}/query",
                headers=_headers(),
                json=body,
                timeout=NOTION_TEACHERS_FETCH_TIMEOUT,
            )
        except httpx.TimeoutException as e:
            raise NotionTeachersTimeoutError(
                f"Teachers fetch timed out after {NOTION_TEACHERS_FETCH_TIMEOUT}s: {e}"
            )
        except Exception:
            return existing

        if response.status_code == 401:
            print("  Notion: teachers fetch failed (check NOTION_API_TOKEN)")
            return existing
        if response.status_code != 200:
            print(f"  Notion: teachers fetch failed (HTTP {response.status_code})")
            return existing

        data = response.json()
        for page in data.get("results", []):
            title_parts = page.get("properties", {}).get("Name", {}).get("title", [])
            if title_parts:
                name = title_parts[0].get("plain_text", "")
                if name:
                    existing[name] = {
                        "id": page["id"],
                        "state": _extract_page_state(page),
                    }
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return existing


def _resolve_relation_names(page_ids: list[str], existing_teachers: dict[str, dict]) -> list[str]:
    """
    Resolves a list of Notion page IDs to teacher names using the already-fetched
    teachers map (name -> {id, state}). Skips IDs that can't be resolved.
    """
    id_to_name = {v["id"]: name for name, v in existing_teachers.items()}
    return [id_to_name[pid] for pid in page_ids if pid in id_to_name]


def fetch_schedule_from_notion() -> dict | None:
    """
    Reads the Schedule database from Notion and returns a dict matching the
    scheduling_rules.yml structure (ready to pass to SchedulingRules(**data)).
    Returns None if not configured or on any error.
    Teacher relation IDs are resolved to names via the Teachers database.
    """
    if not settings.notion_api_token or not settings.notion_schedule_database_id:
        print("  Notion: schedule DB not configured — using local scheduling_rules.yml")
        return None

    schedule_db_id = settings.notion_schedule_database_id

    try:
        t0 = time.monotonic()

        # Resolve teacher relation IDs → names from local cache (no HTTP call needed)
        from app.teachers import load_teacher_cache
        teachers_cache = load_teacher_cache().get("teachers", {})
        id_to_name = {
            v["notion_page_id"]: name
            for name, v in teachers_cache.items()
            if v.get("notion_page_id")
        }
        print(f"  Notion: resolved {len(id_to_name)} teacher IDs from local cache")

        # Fetch all schedule rows
        body = {"page_size": 100}
        try:
            response = httpx.post(
                f"{NOTION_API_BASE}/databases/{schedule_db_id}/query",
                headers=_headers(),
                json=body,
                timeout=NOTION_SCHEDULE_FETCH_TIMEOUT,
            )
        except httpx.TimeoutException as e:
            raise NotionScheduleTimeoutError(
                f"Schedule fetch timed out after {NOTION_SCHEDULE_FETCH_TIMEOUT}s: {e}"
            )
        t2 = time.monotonic()
        print(f"  Notion: schedule fetch {t2 - t0:.2f}s (HTTP {response.status_code})")

        if response.status_code == 401:
            print("  Notion: unauthorized (check NOTION_API_TOKEN) — using local scheduling_rules.yml")
            return None
        if response.status_code != 200:
            print(f"  Notion: schedule fetch failed (HTTP {response.status_code}) — using local scheduling_rules.yml")
            return None

        data = response.json()
        rules = []

        DAY_MAP = {
            "Mon": "mon", "Tue": "tue", "Wed": "wed",
            "Thu": "thu", "Fri": "fri", "Sat": "sat", "Sun": "sun",
        }

        for page in data.get("results", []):
            props = page.get("properties", {})

            # Name (Title) → label (used as-is, e.g. "Monday Midday")
            title_parts = props.get("Name", {}).get("title", [])
            name = title_parts[0].get("plain_text", "") if title_parts else ""
            if not name:
                continue
            label = name

            # Day → weekday
            day_val = (props.get("Day", {}).get("select") or {}).get("name", "")
            weekday = DAY_MAP.get(day_val)
            if not weekday:
                continue

            # Start → start_time
            start_val = (props.get("Start", {}).get("select") or {}).get("name", "")
            if not start_val:
                continue

            # Slots
            slots_val = (props.get("Slots", {}).get("select") or {}).get("name", "")
            try:
                slots = int(slots_val)
            except (ValueError, TypeError):
                continue

            # Enabled
            enabled = props.get("Enabled", {}).get("checkbox", True)

            # Allow Fallbacks
            allow_fallbacks = props.get("Allow Fallbacks", {}).get("checkbox", True)

            # Teacher relations → names (resolved from local cache)
            preferred_teachers = []
            for col in ("Teacher 1", "Teacher 2", "Teacher 3"):
                relation = props.get(col, {}).get("relation", [])
                if relation:
                    page_id = relation[0].get("id", "")
                    if page_id in id_to_name:
                        preferred_teachers.append(id_to_name[page_id])

            rules.append({
                "label": label,
                "weekday": weekday,
                "enabled": enabled,
                "start_time": start_val,
                "slots": slots,
                "preferred_teachers": preferred_teachers,
                "allow_fallbacks": allow_fallbacks,
            })

        if not rules:
            print("  Notion: schedule DB returned no rows — using local scheduling_rules.yml")
            return None

        print(f"  Notion: schedule sync done {t2 - t0:.2f}s total ({len(rules)} rules)")

        return {
            "timezone": "Europe/Madrid",
            "booking": {
                "open_offset_days": 7,
                "open_offset_minutes": 30,
                "precheck_lead_seconds": 120,
            },
            "rules": rules,
        }

    except (NotionTeachersTimeoutError, NotionScheduleTimeoutError):
        raise
    except Exception as e:
        print(f"  Notion: could not fetch schedule ({e}) — using local scheduling_rules.yml")
        return None


def cache_schedule_locally(data: dict, path: str = "scheduling_rules.yml") -> None:
    """Writes a schedule dict to scheduling_rules.yml."""
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def log_run_to_notion(status: str, detail: str, rule: str = "", teacher: str = "", job: str = "") -> None:
    """
    Creates a row in the Run Log database.
    status: "Booked", "Failed", "Error", or "Synced"
    job: "RUN_DUE", "SYNC_SCHEDULE", or "POPULATE_TEACHERS"
    Silent no-op if NOTION_RUN_LOG_DATABASE_ID is not set. Never raises.
    """
    if not settings.notion_api_token or not settings.notion_run_log_database_id:
        print("  Notion: run log DB not configured — skipping log entry")
        return

    now = date.today().isoformat()
    name = f"{now} {__import__('datetime').datetime.now().strftime('%H:%M')}"

    properties = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Date": {"date": {"start": now}},
        "Status": {"select": {"name": status}},
        "Detail": {"rich_text": [{"text": {"content": detail}}]},
        "Rule": {"rich_text": [{"text": {"content": rule}}]},
        "Teacher": {"rich_text": [{"text": {"content": teacher}}]},
        "Service": {"select": {"name": settings.service_name}},
    }
    if job:
        properties["Job"] = {"select": {"name": job}}

    try:
        httpx.post(
            f"{NOTION_API_BASE}/pages",
            headers=_headers(),
            json={
                "parent": {"database_id": settings.notion_run_log_database_id},
                "properties": properties,
            },
            timeout=10,
        )
    except Exception:
        pass


def sync_teachers_to_notion(cache: dict) -> bool:
    """
    Writes the teachers cache to the Notion Teachers database.
    - Fetches all existing pages in one query upfront
    - Skips pages where nothing has changed
    - Updates changed entries (PATCH), creates new ones (POST)
    - Writes notion_page_id back into cache entries so future schedule syncs
      can resolve teacher names without an extra HTTP call
    Returns True if all writes succeeded, False on any error (never raises).
    Silently skips if NOTION_API_TOKEN or NOTION_TEACHERS_DATABASE_ID are not set.
    """
    if not settings.notion_api_token or not settings.notion_teachers_database_id:
        print("  Notion: teachers DB not configured — skipping sync")
        return False

    db_id = settings.notion_teachers_database_id
    teachers = cache.get("teachers", {})
    updated_date = cache.get("updated", date.today().isoformat())
    success = True
    cache_updated = False

    try:
        existing = _fetch_all_teacher_pages(db_id)

        # Backfill notion_page_id for any teachers already in Notion
        for name, existing_entry in existing.items():
            if name in teachers and not teachers[name].get("notion_page_id"):
                teachers[name]["notion_page_id"] = existing_entry["id"]
                cache_updated = True

        created = updated = skipped = 0
        for name, entry in teachers.items():
            properties = _teacher_properties(entry, updated_date)
            existing_entry = existing.get(name)

            if existing_entry:
                if not _has_changes(existing_entry["state"], entry, updated_date):
                    skipped += 1
                    continue
                response = httpx.patch(
                    f"{NOTION_API_BASE}/pages/{existing_entry['id']}",
                    headers=_headers(),
                    json={"properties": properties},
                    timeout=10,
                )
                if response.status_code == 200:
                    updated += 1
                    print(f"  Updated: {name}")
                else:
                    success = False
            else:
                response = httpx.post(
                    f"{NOTION_API_BASE}/pages",
                    headers=_headers(),
                    json={
                        "parent": {"database_id": db_id},
                        "properties": {
                            "Name": {"title": [{"text": {"content": name}}]},
                            **properties,
                        },
                    },
                    timeout=10,
                )
                if response.status_code in (200, 201):
                    created += 1
                    print(f"  Added:   {name}")
                    page_id = response.json().get("id")
                    if page_id:
                        teachers[name]["notion_page_id"] = page_id
                        cache_updated = True
                else:
                    success = False

        print(f"  Notion: {created} created, {updated} updated, {skipped} unchanged")

        if cache_updated:
            from app.teachers import save_teacher_cache
            save_teacher_cache(cache)

    except Exception as e:
        print(f"  Notion: teachers sync failed ({e})")
        return False

    return success
