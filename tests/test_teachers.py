import json
import pytest
from unittest.mock import patch

from app.teachers import (
    load_teacher_cache,
    save_teacher_cache,
    populate_teachers,
    validate_rules_against_cache,
)
from app.rules import BookingRule, SchedulingRules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_rules_data(names: list[str], allow_fallbacks: bool = True) -> SchedulingRules:
    return SchedulingRules(
        timezone="Europe/Madrid",
        rules=[
            BookingRule(
                label="midday",
                enabled=True,
                weekday="wed",
                start_time="13:00",
                slots=1,
                preferred_teachers=names,
                allow_fallbacks=allow_fallbacks,
            )
        ],
    )


def make_cache(*teachers: tuple) -> dict:
    """Build a cache dict from (name, id, status) tuples."""
    return {
        "updated": "2026-04-03",
        "teachers": {
            name: {"id": tid, "status": status} for name, tid, status in teachers
        },
    }


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


class TestLoadTeacherCache:
    def test_returns_empty_dict_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert load_teacher_cache() == {}

    def test_returns_cache_if_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = {"updated": "2026-01-01", "teachers": {"Maria Garcia": {"id": 184}}}
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "teachers.json").write_text(json.dumps(data))
        assert load_teacher_cache() == data


class TestSaveTeacherCache:
    def test_writes_file_with_today_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from freezegun import freeze_time

        with freeze_time("2026-04-03"):
            save_teacher_cache({"teachers": {}})
        result = json.loads((tmp_path / "data" / "teachers.json").read_text())
        assert result["updated"] == "2026-04-03"


# ---------------------------------------------------------------------------
# populate_teachers
# ---------------------------------------------------------------------------


class TestPopulateTeachers:
    def _run(self, tmp_path, monkeypatch, tutor_map, existing_cache=None):
        monkeypatch.chdir(tmp_path)
        if existing_cache is not None:
            (tmp_path / "data").mkdir(exist_ok=True)
            (tmp_path / "data" / "teachers.json").write_text(json.dumps(existing_cache))

        from app.client import BookingClient

        client = BookingClient(base_url="http://localhost:9999")

        with patch("app.teachers.get_tutors_map", return_value=tutor_map):
            populate_teachers(client)

        return json.loads((tmp_path / "data" / "teachers.json").read_text())

    def test_creates_cache_from_scratch(self, tmp_path, monkeypatch):
        tutor_map = {
            "184": {"name": "Maria Garcia"},
            "159": {"name": "Carlos Lopez"},
        }
        result = self._run(tmp_path, monkeypatch, tutor_map)
        assert "Maria Garcia" in result["teachers"]
        assert result["teachers"]["Maria Garcia"]["id"] == 184
        assert result["teachers"]["Maria Garcia"]["status"] == "ACTIVE"
        assert "Carlos Lopez" in result["teachers"]

    def test_adds_new_teacher(self, tmp_path, monkeypatch):
        existing = make_cache(("Maria Garcia", 184, "ACTIVE"))
        tutor_map = {
            "184": {"name": "Maria Garcia"},
            "159": {"name": "Carlos Lopez"},
        }
        result = self._run(tmp_path, monkeypatch, tutor_map, existing_cache=existing)
        assert "Carlos Lopez" in result["teachers"]
        assert result["teachers"]["Carlos Lopez"]["status"] == "ACTIVE"

    def test_marks_absent_teacher_removed(self, tmp_path, monkeypatch):
        existing = make_cache(
            ("Maria Garcia", 184, "ACTIVE"),
            ("Carlos Lopez", 159, "ACTIVE"),
        )
        tutor_map = {"184": {"name": "Maria Garcia"}}
        result = self._run(tmp_path, monkeypatch, tutor_map, existing_cache=existing)
        assert result["teachers"]["Carlos Lopez"]["status"] == "REMOVED"
        assert result["teachers"]["Maria Garcia"]["status"] == "ACTIVE"

    def test_marks_previously_removed_teacher_active(self, tmp_path, monkeypatch):
        existing = make_cache(("Carlos Lopez", 159, "REMOVED"))
        tutor_map = {"159": {"name": "Carlos Lopez"}}
        result = self._run(tmp_path, monkeypatch, tutor_map, existing_cache=existing)
        assert result["teachers"]["Carlos Lopez"]["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# validate_rules_against_cache
# ---------------------------------------------------------------------------


class TestValidateRulesAgainstCache:
    def test_all_active_ok(self):
        cache = make_cache(("Maria Garcia", 184, "ACTIVE"))
        rules = make_rules_data(["Maria Garcia"])
        validate_rules_against_cache(rules, cache)  # should not raise

    def test_unknown_name_raises(self):
        cache = make_cache(("Maria Garcia", 184, "ACTIVE"))
        rules = make_rules_data(["Unknown Teacher"])
        with pytest.raises(ValueError, match="Unknown teacher names"):
            validate_rules_against_cache(rules, cache)

    def test_removed_name_warns_not_raises(self, capsys):
        cache = make_cache(("Maria Garcia", 184, "REMOVED"))
        rules = make_rules_data(["Maria Garcia"])
        validate_rules_against_cache(rules, cache)  # should not raise
        captured = capsys.readouterr()
        assert "REMOVED" in captured.out
        assert "Maria Garcia" in captured.out

    def test_disabled_rule_not_validated(self):
        """Disabled rules should not be checked."""
        cache = make_cache(("Maria Garcia", 184, "ACTIVE"))
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            rules=[
                BookingRule(
                    label="midday",
                    enabled=False,
                    weekday="wed",
                    start_time="13:00",
                    slots=1,
                    preferred_teachers=["Unknown Teacher"],
                    allow_fallbacks=True,
                )
            ],
        )
        validate_rules_against_cache(rules, cache)  # disabled rule — should not raise

    def test_empty_preferred_teachers_ok(self):
        cache = make_cache(("Maria Garcia", 184, "ACTIVE"))
        rules = make_rules_data([], allow_fallbacks=True)
        validate_rules_against_cache(rules, cache)  # should not raise
