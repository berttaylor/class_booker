"""
Tests for CLI commands sync-schedule and populate-teachers.
"""

import pytest
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from app.cli import app


runner = CliRunner()

FAKE_CACHE = {
    "updated": "2026-04-05",
    "teachers": {f"Teacher {i}": {"id": i, "status": "ACTIVE"} for i in range(15)},
}


# ---------------------------------------------------------------------------
# populate-teachers
# ---------------------------------------------------------------------------


class TestPopulateTeachers:
    def test_success_prints_single_log_line(self):
        """On success: prints one timestamped line with timing and teacher count."""
        with (
            patch("app.cli.master_client") as mock_ctx,
            patch("app.cli.populate_teachers") as pop_fn,
            patch("app.cli.load_teacher_cache", return_value=FAKE_CACHE),
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["populate-teachers"])

        assert result.exit_code == 0
        assert "Teachers sync" in result.output
        assert "15 teachers" in result.output
        pop_fn.assert_called_once()

    def test_auth_failure_prints_failure_line(self):
        """On RuntimeError (auth failure): prints failure line."""
        with patch("app.cli.master_client") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(
                side_effect=RuntimeError("auth failed")
            )
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["populate-teachers"])

        assert result.exit_code == 0
        assert "FAILED" in result.output

    def test_skips_when_disabled(self):
        """When POPULATE_TEACHERS=false, command exits early with a skip message."""
        fake_settings = MagicMock()
        fake_settings.populate_teachers_enabled = False
        with (
            patch("app.cli.settings", fake_settings),
            patch("app.cli.master_client") as mock_ctx,
        ):
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
            Settings(
                populate_teachers_enabled=False,
                teachers_cache_path="data/teachers.json",
            )

    def test_secondary_with_absolute_cache_path_ok(self):
        """POPULATE_TEACHERS=false with an absolute path is valid."""
        from app.config import Settings

        s = Settings(
            populate_teachers_enabled=False,
            teachers_cache_path="/some/path/teachers.json",
        )
        assert s.teachers_cache_path == "/some/path/teachers.json"

    def test_primary_default_is_valid(self):
        """Default settings (primary clone) require no extra config."""
        from app.config import Settings

        s = Settings()
        assert s.populate_teachers_enabled is True
        assert s.teachers_cache_path == "data/teachers.json"
