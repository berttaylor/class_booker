import pytest
import yaml
from pathlib import Path

from app.rules import load_scheduling_rules, SchedulingRules, BookingRule

RULES_FILE = Path(__file__).parent.parent / "scheduling_rules.yml"


class TestLoadSchedulingRules:
    def test_loads_real_file_without_error(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert isinstance(rules, SchedulingRules)

    def test_timezone(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert rules.timezone == "Europe/Madrid"

    def test_booking_config(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert rules.booking.open_offset_days == 7
        assert rules.booking.open_offset_minutes == 30
        assert rules.booking.precheck_lead_seconds == 120

    def test_all_10_rules_present(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert len(rules.rules) == 10

    def test_all_rules_enabled(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert all(r.enabled for r in rules.rules)

    def test_mon_midday_rule(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        rule = next(r for r in rules.rules if r.id == "mon_midday")
        assert rule.weekday == "mon"
        assert rule.start_time == "13:00"
        assert rule.slots == 2
        assert rule.allow_fallbacks is True
        assert isinstance(rule.preferred_teachers, list)

    def test_rule_preferred_teachers_are_strings(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        for rule in rules.rules:
            for name in rule.preferred_teachers:
                assert isinstance(name, str)

    def test_invalid_path_raises(self, tmp_path):
        with pytest.raises(Exception):
            load_scheduling_rules(str(tmp_path / "nonexistent.yml"))

    def test_invalid_yaml_raises(self, tmp_path):
        bad_file = tmp_path / "bad.yml"
        bad_file.write_text("timezone: [[[invalid yaml")
        with pytest.raises(Exception):
            load_scheduling_rules(str(bad_file))

    def test_missing_required_field_raises(self, tmp_path):
        bad_file = tmp_path / "missing_field.yml"
        bad_file.write_text(yaml.dump({"timezone": "Europe/Madrid", "booking": {
            "open_offset_days": 7,
            "open_offset_minutes": 30,
            "precheck_lead_seconds": 120
        }}))
        with pytest.raises(Exception):
            load_scheduling_rules(str(bad_file))


class TestBookingRuleModel:
    def _valid_kwargs(self, **overrides):
        base = dict(
            label="midday",
            enabled=True,
            weekday="mon",
            start_time="13:00",
            slots=1,
            preferred_teachers=["Maria Garcia", "Carlos Lopez"],
            allow_fallbacks=True,
        )
        base.update(overrides)
        return base

    def test_valid_rule(self):
        rule = BookingRule(**self._valid_kwargs())
        assert rule.id == "mon_midday"
        assert rule.weekday == "mon"
        assert rule.slots == 1

    def test_id_computed_from_weekday_and_label(self):
        rule = BookingRule(**self._valid_kwargs(weekday="fri", label="evening"))
        assert rule.id == "fri_evening"

    def test_slot_times_single(self):
        rule = BookingRule(**self._valid_kwargs(start_time="13:00", slots=1))
        assert rule.slot_times() == ["13:00"]

    def test_slot_times_double(self):
        rule = BookingRule(**self._valid_kwargs(start_time="13:00", slots=2))
        assert rule.slot_times() == ["13:00", "13:30"]

    def test_slot_times_double_at_half_hour(self):
        rule = BookingRule(**self._valid_kwargs(start_time="18:30", slots=2))
        assert rule.slot_times() == ["18:30", "19:00"]

    def test_rule_fields_typed_correctly(self):
        rule = BookingRule(**self._valid_kwargs(enabled=False, allow_fallbacks=False))
        assert isinstance(rule.enabled, bool)
        assert isinstance(rule.preferred_teachers, list)
        assert isinstance(rule.allow_fallbacks, bool)


class TestBookingRuleValidation:
    def _valid_kwargs(self, **overrides):
        base = dict(
            label="midday",
            enabled=True,
            weekday="mon",
            start_time="13:00",
            slots=1,
            preferred_teachers=["Maria Garcia"],
            allow_fallbacks=True,
        )
        base.update(overrides)
        return base

    def test_invalid_weekday_raises(self):
        with pytest.raises(Exception, match="weekday"):
            BookingRule(**self._valid_kwargs(weekday="monday"))

    def test_invalid_weekday_raises_on_number(self):
        with pytest.raises(Exception):
            BookingRule(**self._valid_kwargs(weekday="1"))

    def test_start_time_wrong_format_raises(self):
        with pytest.raises(Exception, match="start_time"):
            BookingRule(**self._valid_kwargs(start_time="1300"))

    def test_start_time_bad_minutes_raises(self):
        with pytest.raises(Exception, match="start_time"):
            BookingRule(**self._valid_kwargs(start_time="13:15"))

    def test_start_time_hour_accepted(self):
        rule = BookingRule(**self._valid_kwargs(start_time="09:00"))
        assert rule.start_time == "09:00"

    def test_start_time_half_hour_accepted(self):
        rule = BookingRule(**self._valid_kwargs(start_time="09:30"))
        assert rule.start_time == "09:30"

    def test_slots_zero_raises(self):
        with pytest.raises(Exception, match="slots"):
            BookingRule(**self._valid_kwargs(slots=0))

    def test_slots_three_raises(self):
        with pytest.raises(Exception, match="slots"):
            BookingRule(**self._valid_kwargs(slots=3))

    def test_slots_1_accepted(self):
        rule = BookingRule(**self._valid_kwargs(slots=1))
        assert rule.slots == 1

    def test_slots_2_accepted(self):
        rule = BookingRule(**self._valid_kwargs(slots=2))
        assert rule.slots == 2

    def test_no_fallback_empty_teachers_raises(self):
        with pytest.raises(Exception, match="preferred_teachers"):
            BookingRule(**self._valid_kwargs(preferred_teachers=[], allow_fallbacks=False))

    def test_no_fallback_with_teachers_ok(self):
        rule = BookingRule(**self._valid_kwargs(preferred_teachers=["Maria Garcia"], allow_fallbacks=False))
        assert rule.allow_fallbacks is False

    def test_fallback_with_empty_teachers_ok(self):
        rule = BookingRule(**self._valid_kwargs(preferred_teachers=[], allow_fallbacks=True))
        assert rule.preferred_teachers == []

    def test_invalid_timezone_raises(self):
        with pytest.raises(Exception, match="timezone"):
            from app.rules import SchedulingRules, BookingConfig
            SchedulingRules(
                timezone="Not/ATimezone",
                booking=BookingConfig(open_offset_days=7, open_offset_minutes=30, precheck_lead_seconds=120),
                rules=[],
            )
