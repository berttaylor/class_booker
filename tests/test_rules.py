import pytest
import yaml
from pathlib import Path

from app.rules import load_scheduling_rules, load_active_schedules, SchedulingRules, BookingRule, ScheduleSettings, ScheduleCredentials

RULES_FILE = Path(__file__).parent.parent / "scheduling_rules" / "bert.yml"


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

    def test_rule_count(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert len(rules.rules) >= 10

    def test_all_rules_enabled(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        assert all(r.enabled for r in rules.rules)

    def test_mon_midday_rule(self):
        rules = load_scheduling_rules(str(RULES_FILE))
        rule = next(r for r in rules.rules if r.weekday == "mon" and "midday" in r.label.lower())
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


class TestScheduleSettings:
    def test_defaults_to_active(self):
        s = ScheduleSettings()
        assert s.is_active is True

    def test_can_be_set_inactive(self):
        s = ScheduleSettings(is_active=False)
        assert s.is_active is False

    def test_scheduling_rules_defaults_settings(self):
        from app.rules import BookingConfig
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            booking=BookingConfig(open_offset_days=7, open_offset_minutes=30, precheck_lead_seconds=120),
            rules=[],
        )
        assert rules.settings.is_active is True

    def test_scheduling_rules_respects_is_active_false(self):
        from app.rules import BookingConfig
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            booking=BookingConfig(open_offset_days=7, open_offset_minutes=30, precheck_lead_seconds=120),
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
        from app.rules import BookingConfig
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            booking=BookingConfig(open_offset_days=7, open_offset_minutes=30, precheck_lead_seconds=120),
            rules=[],
        )
        assert rules.credentials is None

    def test_credentials_parsed_from_yaml(self, tmp_path):
        yml = tmp_path / "test.yml"
        yml.write_text(
            "timezone: Europe/Madrid\n"
            "booking:\n  open_offset_days: 7\n  open_offset_minutes: 30\n  precheck_lead_seconds: 120\n"
            "credentials:\n  email: a@b.com\n  password: pw\n"
            "rules: []\n"
        )
        rules = load_scheduling_rules(str(yml))
        assert rules.credentials is not None
        assert rules.credentials.email == "a@b.com"


class TestLoadActiveSchedules:
    def _make_yml(self, tmp_path, name, is_active=True, include_credentials=True):
        creds = "credentials:\n  email: a@b.com\n  password: pw\n" if include_credentials else ""
        active_str = "true" if is_active else "false"
        (tmp_path / f"{name}.yml").write_text(
            f"timezone: Europe/Madrid\n"
            f"settings:\n  is_active: {active_str}\n"
            f"{creds}"
            f"booking:\n  open_offset_days: 7\n  open_offset_minutes: 30\n  precheck_lead_seconds: 120\n"
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
        result = load_active_schedules(str(tmp_path))
        assert result == []
        captured = capsys.readouterr()
        assert "no credentials" in captured.out

    def test_skips_invalid_yaml(self, tmp_path, capsys):
        (tmp_path / "bad.yml").write_text("timezone: [[[invalid")
        result = load_active_schedules(str(tmp_path))
        assert result == []
        captured = capsys.readouterr()
        assert "bad" in captured.out

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
