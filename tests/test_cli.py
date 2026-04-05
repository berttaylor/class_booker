"""
Tests for CLI commands sync-schedule and populate-teachers.
"""
import pytest
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from app.cli import app
from app.notion import NotionScheduleTimeoutError


runner = CliRunner()

FAKE_SCHEDULE = {
    "timezone": "Europe/Madrid",
    "booking": {"open_offset_days": 7, "open_offset_minutes": 30, "precheck_lead_seconds": 120},
    "rules": [{"label": "Monday Midday", "weekday": "mon", "enabled": True, "start_time": "13:00", "slots": 1, "preferred_teachers": [], "allow_fallbacks": True}] * 11,
}

FAKE_CACHE = {
    "updated": "2026-04-05",
    "teachers": {f"Teacher {i}": {"id": i, "status": "ACTIVE"} for i in range(15)},
}


# ---------------------------------------------------------------------------
# sync-schedule
# ---------------------------------------------------------------------------

class TestSyncSchedule:
    def test_success_prints_single_log_line(self):
        """On success: prints one timestamped line with timing and rule count."""
        with patch("app.cli.fetch_schedule_from_notion", return_value=FAKE_SCHEDULE) as fetch_fn, \
             patch("app.cli.cache_schedule_locally") as cache_fn, \
             patch("app.cli.log_run_to_notion") as log_fn:
            result = runner.invoke(app, ["sync-schedule"])

        assert result.exit_code == 0
        assert "Schedule sync" in result.output
        assert "Rules: 11" in result.output
        fetch_fn.assert_called_once()
        cache_fn.assert_called_once_with(FAKE_SCHEDULE)
        log_fn.assert_called_once()
        status, detail = log_fn.call_args[0]
        assert status == "Synced"
        assert "11 rules" in detail

    def test_failure_when_notion_not_configured(self):
        """When fetch returns None, prints failure line and logs Error to Notion."""
        with patch("app.cli.fetch_schedule_from_notion", return_value=None), \
             patch("app.cli.cache_schedule_locally") as cache_fn, \
             patch("app.cli.log_run_to_notion") as log_fn:
            result = runner.invoke(app, ["sync-schedule"])

        assert result.exit_code == 0
        assert "FAILED" in result.output
        cache_fn.assert_not_called()
        status, detail = log_fn.call_args[0]
        assert status == "Error"

    def test_timeout_prints_failure_and_sends_push(self):
        """On NotionScheduleTimeoutError: prints failure line, sends push, logs Error."""
        err = NotionScheduleTimeoutError("Schedule fetch timed out after 5s")
        with patch("app.cli.fetch_schedule_from_notion", side_effect=err), \
             patch("app.cli.cache_schedule_locally") as cache_fn, \
             patch("app.cli.log_run_to_notion") as log_fn, \
             patch("app.cli.send_push") as push_fn:
            result = runner.invoke(app, ["sync-schedule"])

        assert result.exit_code == 0
        assert "FAILED" in result.output
        cache_fn.assert_not_called()
        push_fn.assert_called_once()
        status, detail = log_fn.call_args[0]
        assert status == "Error"
        assert "timed out" in detail


# ---------------------------------------------------------------------------
# populate-teachers
# ---------------------------------------------------------------------------

class TestPopulateTeachers:
    def test_success_prints_single_log_line(self):
        """On success: prints one timestamped line with timing and teacher count."""
        with patch("app.cli.authed_client") as mock_ctx, \
             patch("app.cli.populate_teachers") as pop_fn, \
             patch("app.cli.load_teacher_cache", return_value=FAKE_CACHE), \
             patch("app.cli.log_run_to_notion") as log_fn:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["populate-teachers"])

        assert result.exit_code == 0
        assert "Teachers sync" in result.output
        assert "15 teachers" in result.output
        pop_fn.assert_called_once()
        status, detail = log_fn.call_args[0]
        assert status == "Synced"
        assert "15 teachers" in detail

    def test_auth_failure_prints_failure_line(self):
        """On RuntimeError (auth failure): prints failure line and logs Error."""
        with patch("app.cli.authed_client") as mock_ctx, \
             patch("app.cli.log_run_to_notion") as log_fn:
            mock_ctx.return_value.__enter__ = MagicMock(side_effect=RuntimeError("auth failed"))
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["populate-teachers"])

        assert result.exit_code == 0
        assert "FAILED" in result.output
        status, detail = log_fn.call_args[0]
        assert status == "Error"

    def test_skips_when_disabled(self):
        """When POPULATE_TEACHERS=false, command exits early with a skip message."""
        fake_settings = MagicMock()
        fake_settings.populate_teachers_enabled = False
        with patch("app.cli.settings", fake_settings), \
             patch("app.cli.authed_client") as mock_ctx:
            result = runner.invoke(app, ["populate-teachers"])

        assert result.exit_code == 0
        assert "disabled" in result.output
        mock_ctx.assert_not_called()


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------

class TestSettingsValidation:
    def test_secondary_without_cache_path_raises(self):
        """POPULATE_TEACHERS=false with default teachers_cache_path must raise."""
        from pydantic import ValidationError
        from app.config import Settings
        with pytest.raises(ValidationError, match="TEACHERS_CACHE_PATH"):
            Settings(populate_teachers_enabled=False, teachers_cache_path="teachers.json")

    def test_secondary_with_absolute_cache_path_ok(self):
        """POPULATE_TEACHERS=false with an absolute path is valid."""
        from app.config import Settings
        s = Settings(populate_teachers_enabled=False, teachers_cache_path="/some/path/teachers.json")
        assert s.teachers_cache_path == "/some/path/teachers.json"

    def test_primary_default_is_valid(self):
        """Default settings (primary clone) require no extra config."""
        from app.config import Settings
        s = Settings()
        assert s.populate_teachers_enabled is True
        assert s.teachers_cache_path == "teachers.json"
