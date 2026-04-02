import pytest
import httpx

from app.booking import book_lesson, get_bookings, cancel_booking


# ---------------------------------------------------------------------------
# book_lesson — payload construction (most critical tests)
# ---------------------------------------------------------------------------

class TestBookLessonPayload:
    """Verify the exact payload sent to the API for each timezone scenario."""

    def _get_payload(self, router, client, lesson_datetime):
        """Helper: call book_lesson and return the captured request body."""
        import json as _json
        captured = {}

        def capture(request):
            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"status": "success", "id": "9999"})

        router.post("/auth/booking/new-add").mock(side_effect=capture)
        book_lesson(client, "184", lesson_datetime)
        return captured["body"]

    def test_payload_summer_cest(self, authed_client):
        """13:00 CEST (UTC+2) → date stays Madrid, times in UTC (11:00/11:30)."""
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T13:00:00+02:00")

        assert payload["date"] == "2026-04-08"
        assert payload["start_time"] == "11:00"
        assert payload["end_time"] == "11:30"

    def test_payload_winter_cet(self, authed_client):
        """13:00 CET (UTC+1) → UTC is 12:00."""
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-01-08T13:00:00+01:00")

        assert payload["date"] == "2026-01-08"
        assert payload["start_time"] == "12:00"
        assert payload["end_time"] == "12:30"

    def test_payload_utc_z_input(self, authed_client):
        """UTC Z input: 11:00Z in April → Madrid date April 8, times 11:00/11:30 UTC."""
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T11:00:00Z")

        assert payload["date"] == "2026-04-08"
        assert payload["start_time"] == "11:00"
        assert payload["end_time"] == "11:30"

    def test_payload_date_is_madrid_not_utc(self, authed_client):
        """
        UTC midnight edge case: 22:30 UTC on April 8 = 00:30 April 9 Madrid (CEST +2).
        The date in the payload must be April 9 (Madrid date), not April 8 (UTC date).
        """
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T22:30:00+00:00")

        assert payload["date"] == "2026-04-09"   # Madrid date
        assert payload["start_time"] == "22:30"   # UTC time
        assert payload["end_time"] == "23:00"

    def test_service_id_is_1(self, authed_client):
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T13:00:00+02:00")
        assert payload["service_id"] == 1

    def test_number_of_people_is_1(self, authed_client):
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T13:00:00+02:00")
        assert payload["number_of_people"] == 1

    def test_status_is_approved(self, authed_client):
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T13:00:00+02:00")
        assert payload["status"] == "approved"

    def test_teacher_id_coerced_to_string(self, authed_client):
        """teacher_id passed as int should become a string staff_id."""
        import json as _json
        client, router = authed_client
        captured = {}

        def capture(request):
            captured["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"status": "success", "id": "1"})

        router.post("/auth/booking/new-add").mock(side_effect=capture)
        book_lesson(client, 184, "2026-04-08T13:00:00+02:00")
        assert captured["body"]["staff_id"] == "184"

    def test_timezone_field_is_madrid(self, authed_client):
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T13:00:00+02:00")
        assert payload["timezone"] == "Europe/Madrid"

    def test_type_of_class_field(self, authed_client):
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T13:00:00+02:00")
        assert payload["type_of_class"] == "let_tutor_decide"

    def test_end_time_is_30_min_after_start(self, authed_client):
        """End time is always 30 minutes after start, both in UTC."""
        client, router = authed_client
        payload = self._get_payload(router, client, "2026-04-08T16:30:00+02:00")
        # 16:30 CEST = 14:30 UTC; end = 15:00 UTC
        assert payload["start_time"] == "14:30"
        assert payload["end_time"] == "15:00"

    def test_returns_api_response_on_success(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/new-add").mock(
            return_value=httpx.Response(200, json={"status": "success", "id": "42"})
        )

        result = book_lesson(client, "184", "2026-04-08T13:00:00+02:00")
        assert result == {"status": "success", "id": "42"}

    def test_returns_error_dict_on_http_failure(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/new-add").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        result = book_lesson(client, "184", "2026-04-08T13:00:00+02:00")
        assert result["status"] == "error"
        assert "500" in result["message"]


# ---------------------------------------------------------------------------
# get_bookings
# ---------------------------------------------------------------------------

class TestGetBookings:
    def test_returns_data_list_on_success(self, authed_client, bookings_response):
        client, router = authed_client
        router.post("/auth/booking/list").mock(
            return_value=httpx.Response(200, json=bookings_response)
        )

        result = get_bookings(client)
        assert len(result) == 3
        assert result[0]["id"] == "1001"

    def test_returns_empty_on_failure_status(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/list").mock(
            return_value=httpx.Response(200, json={"status": "error", "message": "Unauthorized"})
        )

        assert get_bookings(client) == []

    def test_returns_empty_on_http_error(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/list").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )

        assert get_bookings(client) == []

    def test_returns_empty_on_missing_data_key(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/list").mock(
            return_value=httpx.Response(200, json={"status": "success"})
        )

        assert get_bookings(client) == []


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------

class TestCancelBooking:
    def test_cancel_success(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/cancel/1001").mock(
            return_value=httpx.Response(200, json={"status": "success", "message": "Booking cancelled"})
        )

        result = cancel_booking(client, "1001")
        assert result == {"status": "success", "message": "Booking cancelled"}

    def test_cancel_http_error(self, authed_client):
        client, router = authed_client
        router.post("/auth/booking/cancel/9999").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        result = cancel_booking(client, "9999")
        assert result["status"] == "error"
        assert "404" in result["message"]
