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

    def test_all_20_rules_present(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert len(rules.rules) == 20

    def test_all_rules_enabled(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert all(r.enabled for r in rules.rules)

    def test_mon_midday_1_rule(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        rule = next(r for r in rules.rules if r.id == "mon_midday_1")
        assert rule.weekdays == ["mon"]
        assert rule.start_time == "13:00"
        assert rule.allow_fallbacks is True
        assert isinstance(rule.teacher_ids, list)
        assert len(rule.teacher_ids) > 0

    def test_rule_teacher_ids_are_ints(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        for rule in rules.rules:
            for tid in rule.teacher_ids:
                assert isinstance(tid, int)

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
        # Missing 'rules' key
        bad_file.write_text(yaml.dump({"timezone": "Europe/Madrid", "booking": {
            "open_offset_days": 7,
            "open_offset_minutes": 30,
            "precheck_lead_seconds": 120
        }}))
        with pytest.raises(Exception):
            load_scheduling_rules(str(bad_file))


class TestBookingRuleModel:
    def test_valid_rule(self):
        rule = BookingRule(
            id="test_rule",
            enabled=True,
            weekdays=["mon", "wed"],
            start_time="13:00",
            teacher_ids=[184, 159],
            allow_fallbacks=True,
        )
        assert rule.id == "test_rule"
        assert rule.weekdays == ["mon", "wed"]

    def test_rule_fields_typed_correctly(self):
        rule = BookingRule(
            id="x",
            enabled=False,
            weekdays=["fri"],
            start_time="18:30",
            teacher_ids=[1],
            allow_fallbacks=False,
        )
        assert isinstance(rule.enabled, bool)
        assert isinstance(rule.teacher_ids, list)
        assert isinstance(rule.allow_fallbacks, bool)
