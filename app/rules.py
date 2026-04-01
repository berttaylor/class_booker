import yaml
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path

class BookingRule(BaseModel):
    id: str
    enabled: bool
    weekdays: List[str]
    start_time: str
    teacher_ids: List[int]
    allow_fallbacks: bool

class BookingConfig(BaseModel):
    open_offset_days: int
    open_offset_minutes: int
    precheck_lead_seconds: int

class SchedulingRules(BaseModel):
    timezone: str
    booking: BookingConfig
    rules: List[BookingRule]

def load_scheduling_rules(path: str = "scheduling_rules.yml") -> SchedulingRules:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return SchedulingRules(**data)
