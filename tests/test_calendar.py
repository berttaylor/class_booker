from app.ui.calendar import format_calendar


class TestFormatCalendar:
    def test_empty_slots_returns_no_slots_message(self):
        assert format_calendar([]) == "No slots found for this teacher."

    def test_single_available_slot(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        output = format_calendar(slots)
        assert "[AVA]" in output

    def test_single_booked_slot(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "booked"}]
        output = format_calendar(slots)
        assert "[BKD]" in output

    def test_unknown_status_shows_dashes(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "unknown"}]
        output = format_calendar(slots)
        assert "[---]" in output

    def test_header_contains_date(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        output = format_calendar(slots)
        assert "04-08" in output

    def test_header_contains_weekday(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        output = format_calendar(slots)
        assert "Wed" in output

    def test_time_shown_in_local_timezone_cest(self):
        # 11:00 UTC in April = 13:00 CEST (UTC+2)
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        output = format_calendar(slots)
        assert "13:00" in output

    def test_time_shown_in_local_timezone_cet(self):
        # 12:00 UTC in January = 13:00 CET (UTC+1)
        slots = [{"start_time": "2026-01-08T12:00:00+00:00", "status": "available"}]
        output = format_calendar(slots)
        assert "13:00" in output

    def test_multiple_days_in_grid(self):
        slots = [
            {"start_time": "2026-04-08T11:00:00+00:00", "status": "available"},
            {"start_time": "2026-04-09T11:00:00+00:00", "status": "booked"},
        ]
        output = format_calendar(slots)
        assert "04-08" in output
        assert "04-09" in output

    def test_gap_days_filled_in_header(self):
        slots = [
            {"start_time": "2026-04-08T11:00:00+00:00", "status": "available"},
            {"start_time": "2026-04-10T11:00:00+00:00", "status": "available"},
        ]
        output = format_calendar(slots)
        assert "04-09" in output

    def test_invalid_slot_skipped(self):
        slots = [
            {"start_time": "not-a-date", "status": "available"},
            {"start_time": "2026-04-08T11:00:00+00:00", "status": "available"},
        ]
        output = format_calendar(slots)
        assert "[AVA]" in output
