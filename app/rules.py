import re
import yaml
import pytz
from datetime import datetime as dt, timedelta
from pathlib import Path
from app import logger
from pydantic import BaseModel, field_validator
from typing import List

VALID_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

BOOKING_OPEN_OFFSET_DAYS = 7
BOOKING_OPEN_OFFSET_MINUTES = 30
BOOKING_PRECHECK_LEAD_SECONDS = 120


class BookingRule(BaseModel):
    weekday: str
    start_time: str
    enabled: bool
    label: str | None = None
    slots: int
    preferred_teachers: List[str] = []

    @field_validator("weekday")
    @classmethod
    def validate_weekday(cls, v):
        if v not in VALID_WEEKDAYS:
            raise ValueError(f"weekday must be one of {VALID_WEEKDAYS}, got '{v}'")
        return v

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, v):
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"start_time must be HH:MM format, got '{v}'")
        try:
            parsed = dt.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError(f"start_time '{v}' is not a valid time")
        if parsed.minute not in (0, 30):
            raise ValueError(f"start_time must be on the hour or half-hour, got '{v}'")
        return v

    @field_validator("slots")
    @classmethod
    def validate_slots(cls, v):
        if v not in (1, 2):
            raise ValueError(f"slots must be 1 or 2, got {v}")
        return v

    @field_validator("preferred_teachers")
    @classmethod
    def validate_preferred_teachers(cls, v):
        if not v:
            raise ValueError("preferred_teachers cannot be empty")
        return v

    @property
    def id(self) -> str:
        suffix = self.label or self.start_time
        return f"{self.weekday}_{suffix}"

    def slot_times(self) -> List[str]:
        """Returns list of HH:MM start times for each slot."""
        base = dt.strptime(self.start_time, "%H:%M")
        return [
            (base + timedelta(minutes=30 * i)).strftime("%H:%M")
            for i in range(self.slots)
        ]


class ScheduleSettings(BaseModel):
    is_active: bool = True


class ScheduleCredentials(BaseModel):
    email: str
    password: str


class SchedulingRules(BaseModel):
    timezone: str
    rules: List[BookingRule] = []

    @field_validator("rules", mode="before")
    @classmethod
    def coerce_none_to_empty(cls, v):
        return v or []

    settings: ScheduleSettings = ScheduleSettings()
    credentials: ScheduleCredentials | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v):
        try:
            pytz.timezone(v)
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Unknown timezone '{v}'")
        return v


def load_scheduling_rules(path: str = "scheduling_rules/bert.yml") -> SchedulingRules:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return SchedulingRules(**data)


def load_active_schedules(
    directory: str = "scheduling_rules",
) -> list[tuple[str, SchedulingRules]]:
    """
    Discovers all .yml files in directory, loads each, and returns
    (schedule_name, rules) for those with settings.is_active = True
    and a credentials block. Skips and logs any that fail validation
    or are missing credentials.
    """
    schedules = []
    for path in sorted(Path(directory).glob("*.yml")):
        name = path.stem
        try:
            rules = load_scheduling_rules(str(path))
        except Exception as e:
            logger.error(f"Skipping — failed to load: {e}", schedule=name)
            continue
        if not rules.settings.is_active:
            continue
        if rules.credentials is None:
            logger.warning("Skipping — no credentials block in YAML", schedule=name)
            continue
        schedules.append((name, rules))
    return schedules


def sort_rules(data: dict) -> dict:
    """
    Sorts the rules list in the provided data dictionary by day of week
    and then by start time.
    """
    if "rules" in data and isinstance(data["rules"], list):
        # Create a mapping for weekday sorting (mon=0, tue=1, etc.)
        weekday_order = {day: i for i, day in enumerate(VALID_WEEKDAYS)}

        data["rules"].sort(
            key=lambda r: (
                weekday_order.get(r.get("weekday"), 999),
                r.get("start_time", ""),
            )
        )
    return data
