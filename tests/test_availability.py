import httpx

from tests.base import BaseTest
from app.api.availability import get_available_teachers, get_teacher_slots

_AUTHED_TOKEN = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig"


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
            return_value=httpx.Response(
                200, json={"data": [{"id": 184, "name": "Maria"}]}
            )
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(
                200,
                json={
                    "1": {
                        "184": [
                            {
                                "start_time": "2026-04-08T11:00:00+00:00",
                                "status": "booked",
                            }
                        ]
                    }
                },
            )
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
            return_value=httpx.Response(200, json={"data": []})
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(
                200,
                json={
                    "1": {
                        "999": [
                            {
                                "start_time": "2026-04-08T11:00:00+00:00",
                                "status": "available",
                            }
                        ]
                    }
                },
            )
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert len(result) == 1
        assert result[0]["name"] == "Teacher 999"

    def test_api_failure_returns_empty(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(500, text="Server Error")
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert result == []

    def test_handles_list_response_format(self):
        """API might return a list of services instead of a dict."""
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": 184, "name": "Maria"}]}
            )
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "1": {
                            "184": [
                                {
                                    "start_time": "2026-04-08T11:00:00+00:00",
                                    "status": "available",
                                }
                            ]
                        }
                    }
                ],
            )
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert len(result) == 1
        assert result[0]["id"] == "184"

    def test_local_time_in_result_cest(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": 184, "name": "Maria"}]}
            )
        )
        # 11:00 UTC in April = 13:00 CEST (UTC+2)
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(
                200,
                json={
                    "1": {
                        "184": [
                            {
                                "start_time": "2026-04-08T11:00:00+00:00",
                                "status": "available",
                            }
                        ]
                    }
                },
            )
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T11:00:00+00:00")
        assert result[0]["start_time_local"] == "13:00"

    def test_local_time_in_result_cet(self):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": 184, "name": "Maria"}]}
            )
        )
        # 12:00 UTC in January = 13:00 CET (UTC+1)
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(
                200,
                json={
                    "1": {
                        "184": [
                            {
                                "start_time": "2026-01-08T12:00:00+00:00",
                                "status": "available",
                            }
                        ]
                    }
                },
            )
        )

        result = get_available_teachers(self.mock_client, "2026-01-08T12:00:00+00:00")
        assert result[0]["start_time_local"] == "13:00"

    def test_no_match_at_different_time(self, calendar_response, tutors_response):
        self.router.get("/auth/tutors/list").mock(
            return_value=httpx.Response(200, json=tutors_response)
        )
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        result = get_available_teachers(self.mock_client, "2026-04-08T14:00:00+00:00")
        assert result == []


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
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(500)
        )

        assert get_teacher_slots(self.mock_client, "184") == []

    def test_returns_empty_for_unknown_teacher(self, calendar_response):
        self.router.post("/auth/booking/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        assert get_teacher_slots(self.mock_client, "9999") == []
