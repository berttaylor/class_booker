import pytest
import yaml

from app.rules import (
    load_scheduling_rules,
    load_active_schedules,
    SchedulingRules,
    BookingRule,
    ScheduleSettings,
    ScheduleCredentials,
    sort_rules,
)
from app import logger

FIXTURE_YAML = """\
timezone: Europe/Madrid

settings:
  is_active: true

credentials:
  email: test@example.com
  password: secret

rules:
  - weekday: mon
    enabled: true
    start_time: "13:00"
    slots: 2
    preferred_teachers:
      - "Maria Garcia"
      - "Carlos Lopez"

  - label: evening
    weekday: mon
    enabled: true
    start_time: "18:00"
    slots: 1
    preferred_teachers:
      - "Ana Lopez"
"""


class TestLoadSchedulingRules:
    def _fixture_file(self, tmp_path):
        f = tmp_path / "fixture.yml"
        f.write_text(FIXTURE_YAML)
        return f

    def test_loads_without_error(self, tmp_path):
        rules = load_scheduling_rules(str(self._fixture_file(tmp_path)))
        assert isinstance(rules, SchedulingRules)

    def test_timezone(self, tmp_path):
        rules = load_scheduling_rules(str(self._fixture_file(tmp_path)))
        assert rules.timezone == "Europe/Madrid"

    def test_rule_count(self, tmp_path):
        rules = load_scheduling_rules(str(self._fixture_file(tmp_path)))
        assert len(rules.rules) == 2

    def test_all_rules_enabled(self, tmp_path):
        rules = load_scheduling_rules(str(self._fixture_file(tmp_path)))
        assert all(r.enabled for r in rules.rules)

    def test_mon_1300_rule(self, tmp_path):
        rules = load_scheduling_rules(str(self._fixture_file(tmp_path)))
        rule = next(
            r for r in rules.rules if r.weekday == "mon" and r.start_time == "13:00"
        )
        assert rule.weekday == "mon"
        assert rule.start_time == "13:00"
        assert rule.slots == 2
        assert isinstance(rule.preferred_teachers, list)

    def test_rule_preferred_teachers_are_strings(self, tmp_path):
        rules = load_scheduling_rules(str(self._fixture_file(tmp_path)))
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
        bad_file.write_text(yaml.dump({"rules": []}))  # missing timezone
        with pytest.raises(Exception):
            load_scheduling_rules(str(bad_file))


class TestBookingRuleModel:
    def _valid_kwargs(self, **overrides):
        base = dict(
            enabled=True,
            weekday="mon",
            start_time="13:00",
            slots=1,
            preferred_teachers=["Maria Garcia", "Carlos Lopez"],
        )
        base.update(overrides)
        return base

    def test_valid_rule(self):
        rule = BookingRule(**self._valid_kwargs())
        assert rule.id == "mon_13:00"
        assert rule.weekday == "mon"
        assert rule.slots == 1

    def test_id_computed_from_weekday_and_start_time_by_default(self):
        rule = BookingRule(**self._valid_kwargs(weekday="fri", start_time="12:00"))
        assert rule.id == "fri_12:00"

    def test_id_computed_from_weekday_and_label_if_provided(self):
        rule = BookingRule(
            **self._valid_kwargs(weekday="fri", label="custom", start_time="12:00")
        )
        assert rule.id == "fri_custom"

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
        rule = BookingRule(**self._valid_kwargs(enabled=False))
        assert isinstance(rule.enabled, bool)
        assert isinstance(rule.preferred_teachers, list)


class TestBookingRuleValidation:
    def _valid_kwargs(self, **overrides):
        base = dict(
            label="midday",
            enabled=True,
            weekday="mon",
            start_time="13:00",
            slots=1,
            preferred_teachers=["Maria Garcia"],
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

    def test_empty_teachers_raises(self):
        with pytest.raises(Exception, match="preferred_teachers"):
            BookingRule(**self._valid_kwargs(preferred_teachers=[]))

    def test_invalid_timezone_raises(self):
        with pytest.raises(Exception, match="timezone"):
            SchedulingRules(
                timezone="Not/ATimezone",
                rules=[],
            )


class TestScheduleSettings:
    def test_defaults_to_active(self):
        s = ScheduleSettings()
        assert s.is_active is True

    def test_can_be_set_inactive(self):
        s = ScheduleSettings(is_active=False)
        assert s.is_active is False

    def test_scheduling_rules_defaults_settings(self):
        rules = SchedulingRules(timezone="Europe/Madrid", rules=[])
        assert rules.settings.is_active is True

    def test_scheduling_rules_respects_is_active_false(self):
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            settings=ScheduleSettings(is_active=False),
            rules=[],
        )
        assert rules.settings.is_active is False


class TestScheduleCredentials:
    def test_valid_credentials(self):
        c = ScheduleCredentials(email="user@example.com", password="secret")
        assert c.email == "user@example.com"
        assert c.password == "secret"

    def test_credentials_none_by_default(self):
        rules = SchedulingRules(timezone="Europe/Madrid", rules=[])
        assert rules.credentials is None

    def test_credentials_parsed_from_yaml(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "timezone: Europe/Madrid\n"
            "credentials:\n  email: a@b.com\n  password: pw\n"
            "rules: []\n"
        )
        rules = load_scheduling_rules(str(yml))
        assert rules.credentials is not None
        assert rules.credentials.email == "a@b.com"


class TestLoadActiveSchedules:
    def _make_yml(self, tmp_path, name, is_active=True, include_credentials=True):
        creds = (
            "credentials:\n  email: a@b.com\n  password: pw\n"
            if include_credentials
            else ""
        )
        active_str = "true" if is_active else "false"
        (tmp_path / f"{name}.yml").write_text(
            f"timezone: Europe/Madrid\n"
            f"settings:\n  is_active: {active_str}\n"
            f"{creds}"
            f"rules: []\n"
        )

    def test_returns_active_schedules(self, tmp_path):
        self._make_yml(tmp_path, "alice")
        self._make_yml(tmp_path, "bob")
        result = load_active_schedules(str(tmp_path))
        assert len(result) == 2
        names = [name for name, _ in result]
        assert "alice" in names
        assert "bob" in names

    def test_skips_inactive_schedules(self, tmp_path):
        self._make_yml(tmp_path, "active")
        self._make_yml(tmp_path, "inactive", is_active=False)
        result = load_active_schedules(str(tmp_path))
        assert len(result) == 1
        assert result[0][0] == "active"

    def test_skips_schedules_without_credentials(self, tmp_path, capsys):
        self._make_yml(tmp_path, "nocreds", include_credentials=False)
        logger.set_enabled(True)
        try:
            result = load_active_schedules(str(tmp_path))
            assert result == []
            captured = capsys.readouterr()
            assert "no credentials" in captured.out
        finally:
            logger.set_enabled(False)

    def test_skips_invalid_yaml(self, tmp_path, capsys):
        (tmp_path / "bad.yml").write_text("timezone: [[[invalid")
        logger.set_enabled(True)
        try:
            result = load_active_schedules(str(tmp_path))
            assert result == []
            captured = capsys.readouterr()
            assert "bad" in captured.out
        finally:
            logger.set_enabled(False)

    def test_empty_directory(self, tmp_path):
        result = load_active_schedules(str(tmp_path))
        assert result == []

    def test_returns_rules_data(self, tmp_path):
        self._make_yml(tmp_path, "bert")
        result = load_active_schedules(str(tmp_path))
        name, rules = result[0]
        assert name == "bert"
        assert rules.timezone == "Europe/Madrid"
        assert rules.credentials.email == "a@b.com"


class TestSortRules:
    def test_sort_rules_by_day_and_time(self):
        data = {
            "rules": [
                {"weekday": "tue", "start_time": "13:00"},
                {"weekday": "mon", "start_time": "18:00"},
                {"weekday": "mon", "start_time": "13:00"},
                {"weekday": "wed", "start_time": "10:00"},
            ]
        }
        sorted_data = sort_rules(data)
        expected = [
            {"weekday": "mon", "start_time": "13:00"},
            {"weekday": "mon", "start_time": "18:00"},
            {"weekday": "tue", "start_time": "13:00"},
            {"weekday": "wed", "start_time": "10:00"},
        ]
        assert sorted_data["rules"] == expected

    def test_sort_rules_handles_missing_rules(self):
        data = {"timezone": "UTC"}
        assert sort_rules(data) == data

    def test_sort_rules_handles_invalid_weekday(self):
        data = {
            "rules": [
                {"weekday": "unknown", "start_time": "13:00"},
                {"weekday": "mon", "start_time": "13:00"},
            ]
        }
        sorted_data = sort_rules(data)
        assert sorted_data["rules"][0]["weekday"] == "mon"
        assert sorted_data["rules"][1]["weekday"] == "unknown"
