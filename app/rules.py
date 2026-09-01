import re
import yaml
import pytz
from datetime import datetime as dt
from pathlib import Path
from app import logger
from pydantic import BaseModel, field_validator
from typing import List

VALID_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Absolute so schedule discovery works regardless of working directory.
SCHEDULING_RULES_DIR = Path(__file__).parent.parent / "scheduling_rules"

# The platform opens a whole day's slots at once: any lesson on day D becomes
# bookable at 00:00 local time on D - BOOKING_OPEN_OFFSET_DAYS.
BOOKING_OPEN_OFFSET_DAYS = 7

# Aim a hair past midnight rather than exactly on it. The window is a
# server-side day boundary now, not a slot-time offset, so a server that flips
# the day a moment late would reject a request sent at 00:00:00.000. Tune this
# if the first attempt of a run starts failing on timing.
BOOKING_OPEN_BUFFER_SECONDS = 2

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
        """
        Returns the HH:MM start times to book — always a single entry.

        `slots` is a duration multiplier, not a repeat count: the API takes
        duration_minutes, so a 2-slot rule is one 60-minute booking rather than
        two consecutive 30-minute ones. Kept as a list so callers that enumerate
        it are unaffected.
        """
        return [self.start_time]

    @property
    def duration_minutes(self) -> int:
        """Total lesson length — 30 minutes per slot."""
        return 30 * self.slots


class ScheduleSettings(BaseModel):
    is_active: bool = True


class ScheduleCredentials(BaseModel):
    email: str
    password: str


class SchedulingRules(BaseModel):
    timezone: str
    rules: List[BookingRule] = []
    holidays: List[str] = []

    @field_validator("rules", mode="before")
    @classmethod
    def coerce_none_to_empty(cls, v):
        return v or []

    @field_validator("holidays", mode="before")
    @classmethod
    def coerce_holidays_none_to_empty(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item) for item in v]
        return v

    @field_validator("holidays")
    @classmethod
    def validate_holidays(cls, v):
        for date_str in v:
            try:
                dt.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"Holiday '{date_str}' must be in YYYY-MM-DD format")
        return v

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
    directory: str | Path = SCHEDULING_RULES_DIR,
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
