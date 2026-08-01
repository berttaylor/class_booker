import json as _json

import httpx

from tests.base import BaseTest
from app.api.booking import book_lesson, get_bookings, cancel_booking, get_focus

_AUTHED_TOKEN = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig"

_HOLD_OK = {"message": "Slot held successfully", "expires_at": "2026-08-01T10:36:59Z"}
_CONFIRM_OK = {
    "message": "Booking confirmed",
    "booking_id": "408605",
    "session_id": "333515",
}


# ---------------------------------------------------------------------------
# book_lesson — hold-then-confirm flow and payload construction
# ---------------------------------------------------------------------------


class TestBookLessonPayload(BaseTest):
    """Verify the exact payloads sent to the API for each timezone scenario."""

    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)
        self.captured = {}

    def _mock_flow(self, hold_status=200, confirm_status=200):
        def capture_hold(request):
            self.captured["hold"] = _json.loads(request.content)
            return httpx.Response(hold_status, json=_HOLD_OK)

        def capture_confirm(request):
            self.captured["confirm"] = _json.loads(request.content)
            return httpx.Response(confirm_status, json=_CONFIRM_OK)

        self.router.post("/booking/favorites/hold-slot").mock(side_effect=capture_hold)
        self.router.post("/booking/favorites/confirm").mock(side_effect=capture_confirm)

    def _confirm_payload(self, lesson_datetime, **kwargs):
        self._mock_flow()
        book_lesson(self.mock_client, "4584", lesson_datetime, **kwargs)
        return self.captured["confirm"]

    def test_payload_summer_cest(self):
        """13:00 CEST (UTC+2) → 11:00 UTC, end 11:30."""
        payload = self._confirm_payload("2026-08-03T13:00:00+02:00")

        assert payload["start_time"] == "2026-08-03T11:00:00.000Z"
        assert payload["end_time"] == "2026-08-03T11:30:00.000Z"

    def test_payload_winter_cet(self):
        """13:00 CET (UTC+1) → 12:00 UTC."""
        payload = self._confirm_payload("2026-01-08T13:00:00+01:00")

        assert payload["start_time"] == "2026-01-08T12:00:00.000Z"
        assert payload["end_time"] == "2026-01-08T12:30:00.000Z"

    def test_payload_utc_z_input(self):
        payload = self._confirm_payload("2026-08-03T11:00:00Z")

        assert payload["start_time"] == "2026-08-03T11:00:00.000Z"

    def test_payload_crosses_utc_midnight(self):
        """23:45 UTC + 30 min rolls the end_time into the next day."""
        payload = self._confirm_payload("2026-08-03T23:45:00+00:00")

        assert payload["start_time"] == "2026-08-03T23:45:00.000Z"
        assert payload["end_time"] == "2026-08-04T00:15:00.000Z"

    def test_duration_defaults_to_30_minutes(self):
        payload = self._confirm_payload("2026-08-03T13:00:00+02:00")
        assert payload["duration_minutes"] == 30

    def test_60min_booking_spans_a_full_hour(self):
        """One request covers both half-hours rather than two bookings."""
        payload = self._confirm_payload(
            "2026-08-03T13:00:00+02:00", duration_minutes=60
        )

        assert payload["duration_minutes"] == 60
        assert payload["start_time"] == "2026-08-03T11:00:00.000Z"
        assert payload["end_time"] == "2026-08-03T12:00:00.000Z"

    def test_60min_hold_also_spans_the_hour(self):
        """The hold must reserve the whole hour, not just the first half."""
        self._mock_flow()
        book_lesson(
            self.mock_client, "4584", "2026-08-03T13:00:00+02:00", duration_minutes=60
        )

        assert self.captured["hold"]["end_time"] == "2026-08-03T12:00:00.000Z"

    def test_teacher_id_coerced_to_string(self):
        self._mock_flow()
        book_lesson(self.mock_client, 4584, "2026-08-03T13:00:00+02:00")
        assert self.captured["confirm"]["tutor_id"] == "4584"

    def test_hold_payload_has_no_focus_fields(self):
        """hold-slot takes only the slot identity; focus belongs to confirm."""
        self._mock_flow()
        book_lesson(
            self.mock_client,
            "4584",
            "2026-08-03T13:00:00+02:00",
            focus_type="Simple Past",
            activity_suggestion_id=2718,
        )

        assert set(self.captured["hold"]) == {"tutor_id", "start_time", "end_time"}

    def test_focus_fields_included_when_provided(self):
        payload = self._confirm_payload(
            "2026-08-03T13:00:00+02:00",
            focus_type="Simple Past",
            activity_suggestion_id=2718,
        )

        assert payload["focus_type"] == "Simple Past"
        assert payload["activity_suggestion_id"] == 2718

    def test_focus_fields_omitted_when_absent(self):
        """A failed activities lookup must not send null focus fields."""
        payload = self._confirm_payload("2026-08-03T13:00:00+02:00")

        assert "focus_type" not in payload
        assert "activity_suggestion_id" not in payload

    def test_confirm_not_sent_when_hold_fails(self):
        self._mock_flow(hold_status=409)

        result = book_lesson(self.mock_client, "4584", "2026-08-03T13:00:00+02:00")

        assert result["status"] == "error"
        assert "confirm" not in self.captured

    def test_returns_booking_id_on_success(self):
        self._mock_flow()

        result = book_lesson(self.mock_client, "4584", "2026-08-03T13:00:00+02:00")

        assert result["status"] == "success"
        assert result["booking_id"] == "408605"

    def test_returns_error_when_hold_fails(self):
        self._mock_flow(hold_status=409)

        result = book_lesson(self.mock_client, "4584", "2026-08-03T13:00:00+02:00")

        assert result["status"] == "error"
        assert "409" in result["message"]

    def test_returns_error_when_confirm_fails(self):
        self._mock_flow(confirm_status=500)

        result = book_lesson(self.mock_client, "4584", "2026-08-03T13:00:00+02:00")

        assert result["status"] == "error"
        assert "500" in result["message"]


# ---------------------------------------------------------------------------
# get_focus
# ---------------------------------------------------------------------------


class TestGetFocus(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def test_returns_top_activity(self, activities_response):
        self.router.get("/students/me/activities").mock(
            return_value=httpx.Response(200, json=activities_response)
        )

        focus_type, activity_id = get_focus(self.mock_client)

        assert activity_id == 2718
        assert focus_type.startswith("Narrate personal")

    def test_degrades_on_http_error(self):
        self.router.get("/students/me/activities").mock(
            return_value=httpx.Response(500)
        )

        assert get_focus(self.mock_client) == (None, None)

    def test_degrades_on_empty_list(self):
        self.router.get("/students/me/activities").mock(
            return_value=httpx.Response(200, json={"data": []})
        )

        assert get_focus(self.mock_client) == (None, None)


# ---------------------------------------------------------------------------
# get_bookings — normalisation to the scheduler's flat shape
# ---------------------------------------------------------------------------


class TestGetBookings(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def _mock_classes(self, classes):
        self.router.get("/students/me/my-classes").mock(
            return_value=httpx.Response(200, json={"data": classes})
        )

    def test_normalises_to_scheduler_shape(self, my_classes_response):
        self.router.get("/students/me/my-classes").mock(
            return_value=httpx.Response(200, json=my_classes_response)
        )

        result = get_bookings(self.mock_client)

        assert len(result) == 4
        first = result[0]
        assert first["staff_id"] == "4609"
        assert first["status"] == "approved"
        assert first["past"] is False
        assert first["booking_id"] == "405226"

    def test_converts_utc_to_local_time(self):
        """11:00 UTC in August = 13:00 Madrid (CEST)."""
        self._mock_classes(
            [
                {
                    "id": "1",
                    "tutor_id": 4609,
                    "date_time": "2026-08-02T11:00:00+00:00",
                    "status": "upcoming",
                }
            ]
        )

        result = get_bookings(self.mock_client)
        assert result[0]["date"] == "2026-08-02"
        assert result[0]["start_time"] == "13:00:00"

    def test_utc_to_local_date_rollover(self):
        """22:30 UTC on Aug 3 is 00:30 on Aug 4 in Madrid — the date must roll."""
        self._mock_classes(
            [
                {
                    "id": "1",
                    "tutor_id": 4609,
                    "date_time": "2026-08-03T22:30:00+00:00",
                    "status": "upcoming",
                }
            ]
        )

        result = get_bookings(self.mock_client)
        assert result[0]["date"] == "2026-08-04"
        assert result[0]["start_time"] == "00:30:00"

    def test_non_upcoming_status_passed_through(self):
        self._mock_classes(
            [
                {
                    "id": "1",
                    "tutor_id": 4609,
                    "date_time": "2026-08-02T11:00:00+00:00",
                    "status": "cancelled",
                }
            ]
        )

        assert get_bookings(self.mock_client)[0]["status"] == "cancelled"

    def test_returns_empty_on_http_error(self):
        self.router.get("/students/me/my-classes").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )

        assert get_bookings(self.mock_client) == []

    def test_returns_empty_on_missing_data_key(self):
        self.router.get("/students/me/my-classes").mock(
            return_value=httpx.Response(200, json={})
        )

        assert get_bookings(self.mock_client) == []


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------


class TestCancelBooking(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def test_cancel_success(self):
        self.router.post("/students/me/bookings/408605/cancel").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": "Booking cancelled successfully",
                    "data": {"booking_id": 408605, "is_late_cancellation": False},
                },
            )
        )

        result = cancel_booking(self.mock_client, "408605")

        assert result["status"] == "success"
        assert result["message"] == "Booking cancelled successfully"

    def test_cancel_http_error(self):
        self.router.post("/students/me/bookings/9999/cancel").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        result = cancel_booking(self.mock_client, "9999")

        assert result["status"] == "error"
        assert "404" in result["message"]
