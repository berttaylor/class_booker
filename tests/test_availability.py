import httpx

from tests.base import BaseTest
from app.availability import normalize_datetime, get_available_teachers, get_teacher_slots, format_calendar

_AUTHED_TOKEN = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig"


# ---------------------------------------------------------------------------
# normalize_datetime
# ---------------------------------------------------------------------------

class TestNormalizeDatetime:
    def test_utc_z_suffix(self):
        assert normalize_datetime("2026-04-08T13:00:00Z") == "2026-04-08T13:00:00+00:00"

    def test_positive_offset_cest(self):
        # 13:00 CEST (UTC+2) → 11:00 UTC
        assert normalize_datetime("2026-04-08T13:00:00+02:00") == "2026-04-08T11:00:00+00:00"

    def test_positive_offset_cet(self):
        # 13:00 CET (UTC+1) → 12:00 UTC
        assert normalize_datetime("2026-01-08T13:00:00+01:00") == "2026-01-08T12:00:00+00:00"

    def test_negative_offset(self):
        # 08:00 UTC-5 → 13:00 UTC
        assert normalize_datetime("2026-04-08T08:00:00-05:00") == "2026-04-08T13:00:00+00:00"

    def test_already_utc(self):
        assert normalize_datetime("2026-04-08T11:00:00+00:00") == "2026-04-08T11:00:00+00:00"

    def test_seconds_zeroed(self):
        # Seconds component in output is always :00
        assert normalize_datetime("2026-04-08T11:00:45+00:00") == "2026-04-08T11:00:00+00:00"

    def test_invalid_string_passthrough(self):
        # Silent fallback: returns original string unchanged
        assert normalize_datetime("not-a-date") == "not-a-date"

    def test_dst_summer_cest(self):
        # Summer: Madrid is UTC+2
        assert normalize_datetime("2026-07-15T13:00:00+02:00") == "2026-07-15T11:00:00+00:00"

    def test_dst_winter_cet(self):
        # Winter: Madrid is UTC+1
        assert normalize_datetime("2026-01-15T13:00:00+01:00") == "2026-01-15T12:00:00+00:00"

    def test_midnight_utc(self):
        assert normalize_datetime("2026-04-08T00:00:00Z") == "2026-04-08T00:00:00+00:00"


# ---------------------------------------------------------------------------
# get_available_teachers
# ---------------------------------------------------------------------------

class TestGetAvailableTeachers(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def test_returns_available_teachers(self, calendar_response, tutors_response):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json=tutors_response)
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        # 11:00 UTC on April 8 = 13:00 CEST
        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        ids = [t["id"] for t in result]
        assert "184" in ids
        assert "159" in ids

    def test_filters_booked_slots(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json={"data": [{"id": 184, "name": "Maria", "is_favorite": False}]})
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json={
                "1": {
                    "184": [{"start_time": "2026-04-08T11:00:00+00:00", "status": "booked"}]
                }
            })
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert result == []

    def test_enriches_with_teacher_names(self, calendar_response, tutors_response):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json=tutors_response)
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        teacher_184 = next(t for t in result if t["id"] == "184")
        assert teacher_184["name"] == "Maria Garcia"

    def test_unknown_teacher_defaults_to_id(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json={"data": []})  # empty tutors map
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json={
                "1": {
                    "999": [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
                }
            })
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert len(result) == 1
        assert result[0]["name"] == "Teacher 999"

    def test_api_failure_returns_empty(self):
        self.router.get("/auth/tutors/list").mock(return_value=httpx.Response(200, json={"data": []}))
        self.router.post("/auth/booking/calendar").mock(return_value=httpx.Response(500, text="Server Error"))

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert result == []

    def test_handles_list_response_format(self):
        """
        API might return a list of services instead of a dict.
        """
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json={"data": [{"id": 184, "name": "Maria", "is_favorite": False}]})
        )
        # API returns a bare list
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=[
                {"1": {"184": [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]}}
            ])
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert len(result) == 1
        assert result[0]["id"] == "184"

    def test_local_time_in_result_cest(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json={"data": [{"id": 184, "name": "Maria", "is_favorite": False}]})
        )
        # 11:00 UTC in April = 13:00 CEST (UTC+2)
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json={
                "1": {"184": [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]}
            })
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert result[0]["start_time_local"] == "13:00"

    def test_local_time_in_result_cet(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json={"data": [{"id": 184, "name": "Maria", "is_favorite": False}]})
        )
        # 12:00 UTC in January = 13:00 CET (UTC+1)
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json={
                "1": {"184": [{"start_time": "2026-01-08T12:00:00+00:00", "status": "available"}]}
            })
        )

        result = get_available_teachers(self.mock_client, "2026-01-08T12:00:00+00:00")
        assert result[0]["start_time_local"] == "13:00"

    def test_no_match_at_different_time(self, calendar_response, tutors_response):
        self.router.get("/auth/tutors/list").mock(return_value=httpx.Response(200, json=tutors_response))
        self.router.post("/auth/booking/calendar").mock(return_value=httpx.Response(200, json=calendar_response))

        # Request a different time — no slots at 14:00 UTC
        result = get_available_teachers(self.mock_client, "2026-04-08T14:00:00+00:00")
        assert result == []


# ---------------------------------------------------------------------------
# get_teacher_slots
# ---------------------------------------------------------------------------

class TestGetTeacherSlots(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def test_fetches_slots_for_teacher_string_id(self, calendar_response):
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        slots = get_teacher_slots(self.mock_client, "184")
        assert len(slots) == 3
        assert slots[0]["start_time"] == "2026-04-08T11:00:00+00:00"

    def test_fetches_slots_for_teacher_int_id(self, calendar_response):
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        slots = get_teacher_slots(self.mock_client, 184)
        assert len(slots) == 3

    def test_returns_empty_on_http_error(self):
        self.router.post("/auth/booking/calendar").mock(return_value=httpx.Response(500))

        assert get_teacher_slots(self.mock_client, "184") == []

    def test_returns_empty_for_unknown_teacher(self, calendar_response):
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        assert get_teacher_slots(self.mock_client, "9999") == []


# ---------------------------------------------------------------------------
# format_calendar
# ---------------------------------------------------------------------------

class TestFormatCalendar:
    def test_empty_slots_returns_message(self):
        result = format_calendar([])
        assert "No slots found" in result

    def test_available_slot_marked_ava(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        result = format_calendar(slots)
        assert "[AVA]" in result

    def test_booked_slot_marked_bkd(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "booked"}]
        result = format_calendar(slots)
        assert "[BKD]" in result

    def test_unknown_status_marked_dash(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "pending"}]
        result = format_calendar(slots)
        assert "[---]" in result

    def test_timezone_label_in_header(self):
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        result = format_calendar(slots)
        assert "Europe/Madrid" in result

    def test_dst_summer_offset_display(self):
        # UTC 11:00 in April (CEST, UTC+2) → should show 13:00 in Madrid
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        result = format_calendar(slots)
        assert "13:00" in result

    def test_dst_winter_offset_display(self):
        # UTC 12:00 in January (CET, UTC+1) → should show 13:00 in Madrid
        slots = [{"start_time": "2026-01-08T12:00:00+00:00", "status": "available"}]
        result = format_calendar(slots)
        assert "13:00" in result

    def test_utc_midnight_crosses_to_madrid_next_day(self):
        # UTC 22:30 on April 7 = 00:30 April 8 Madrid (CEST, UTC+2)
        slots = [{"start_time": "2026-04-07T22:30:00+00:00", "status": "available"}]
        result = format_calendar(slots)
        # Should be grouped under April 8 in Madrid, not April 7
        assert "04-08" in result
        assert "00:30" in result

    def test_date_range_fills_gaps(self):
        # Slots on April 8 and April 10 — April 9 column should also appear
        slots = [
            {"start_time": "2026-04-08T11:00:00+00:00", "status": "available"},
            {"start_time": "2026-04-10T11:00:00+00:00", "status": "available"},
        ]
        result = format_calendar(slots)
        assert "04-08" in result
        assert "04-09" in result
        assert "04-10" in result

    def test_weekday_names_in_header(self):
        # April 8 2026 is a Wednesday
        slots = [{"start_time": "2026-04-08T11:00:00+00:00", "status": "available"}]
        result = format_calendar(slots)
        assert "Wed" in result

    def test_no_valid_slots_message(self):
        # Slot with unparseable start_time
        slots = [{"start_time": "bad-date", "status": "available"}]
        result = format_calendar(slots)
        assert "No valid slots" in result
