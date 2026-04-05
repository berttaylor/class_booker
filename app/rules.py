import re
import yaml
import pytz
from datetime import datetime as dt, timedelta
from pydantic import BaseModel, field_validator, model_validator
from typing import List

VALID_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class BookingRule(BaseModel):
    label: str
    enabled: bool
    weekday: str
    start_time: str
    slots: int
    preferred_teachers: List[str] = []
    allow_fallbacks: bool

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

    @model_validator(mode="after")
    def validate_teachers_if_no_fallback(self):
        if not self.allow_fallbacks and not self.preferred_teachers:
            raise ValueError(
                f"Rule '{self.weekday}_{self.label}': preferred_teachers cannot be empty when allow_fallbacks is False"
            )
        return self

    @property
    def id(self) -> str:
        return f"{self.weekday}_{self.label}"

    def slot_times(self) -> List[str]:
        """Returns list of HH:MM start times for each slot."""
        base = dt.strptime(self.start_time, "%H:%M")
        return [(base + timedelta(minutes=30 * i)).strftime("%H:%M") for i in range(self.slots)]


class BookingConfig(BaseModel):
    open_offset_days: int
    open_offset_minutes: int
    precheck_lead_seconds: int


class SchedulingRules(BaseModel):
    timezone: str
    booking: BookingConfig
    rules: List[BookingRule]

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
