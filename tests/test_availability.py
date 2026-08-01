import httpx

from tests.base import BaseTest
from app.api.availability import (
    get_available_teachers,
    get_teacher_slots,
    get_tutors_map,
)

_AUTHED_TOKEN = "header.eyJleHAiOiA5OTk5OTk5OTk5fQ.sig"


def _slot(tutor_id, start, status="available"):
    """Builds a calendar slot in the API's shape."""
    return {
        "tutor_id": tutor_id,
        "tutor": None,
        "start_time": start,
        "end_time": start,
        "duration_minutes": 30,
        "status": status,
        "held_by_user_id": None,
        "expires_at": None,
    }


class TestGetTutorsMap(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def test_follows_pagination_across_all_pages(
        self, tutors_page1_response, tutors_page2_response
    ):
        """A one-page-only bug would silently truncate the roster."""
        self.router.get("/tutors", params={"page": 1}).mock(
            return_value=httpx.Response(200, json=tutors_page1_response)
        )
        self.router.get("/tutors", params={"page": 2}).mock(
            return_value=httpx.Response(200, json=tutors_page2_response)
        )

        result = get_tutors_map(self.mock_client)

        assert len(result) == 5
        assert result["4651"]["name"] == "Adan Diaz"
        assert result["4584"]["name"] == "Paula Barrientos"  # only on page 2

    def test_single_page_does_not_request_more(self):
        self.router.get("/tutors", params={"page": 1}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "current_page": 1,
                    "data": [{"id": 4609, "name": "Mc Quintero"}],
                    "last_page": 1,
                },
            )
        )

        assert get_tutors_map(self.mock_client) == {"4609": {"name": "Mc Quintero"}}

    def test_returns_empty_on_http_error(self):
        self.router.get("/tutors", params={"page": 1}).mock(
            return_value=httpx.Response(500, text="Server Error")
        )

        assert get_tutors_map(self.mock_client) == {}


class TestGetAvailableTeachers(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def _mock_calendar(self, by_date):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(200, json={"data": by_date})
        )

    def _mock_tutors(self, tutors):
        self.router.get("/tutors", params={"page": 1}).mock(
            return_value=httpx.Response(
                200, json={"current_page": 1, "data": tutors, "last_page": 1}
            )
        )

    def test_returns_available_teachers(self, calendar_response, tutors_page1_response):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )
        self.router.get("/tutors", params={"page": 1}).mock(
            return_value=httpx.Response(
                200,
                json={**tutors_page1_response, "last_page": 1},
            )
        )

        # 2026-08-03 11:00Z — two tutors free in the fixture
        result = get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00")
        ids = [t["id"] for t in result]
        assert "4468" in ids
        assert "4584" in ids

    def test_filters_non_available_status(self):
        self._mock_tutors([{"id": 4584, "name": "Paula"}])
        self._mock_calendar(
            {"2026-08-03": [_slot("4584", "2026-08-03T11:00:00.000Z", "held")]}
        )

        assert (
            get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00") == []
        )

    def test_enriches_with_teacher_names(self):
        self._mock_tutors([{"id": 4584, "name": "Paula Barrientos"}])
        self._mock_calendar({"2026-08-03": [_slot("4584", "2026-08-03T11:00:00.000Z")]})

        result = get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00")
        assert result[0]["name"] == "Paula Barrientos"

    def test_unknown_teacher_defaults_to_id(self):
        self._mock_tutors([])
        self._mock_calendar({"2026-08-03": [_slot("999", "2026-08-03T11:00:00.000Z")]})

        result = get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00")
        assert len(result) == 1
        assert result[0]["name"] == "Teacher 999"

    def test_calendar_failure_returns_empty(self):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(500, text="Server Error")
        )

        assert (
            get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00") == []
        )

    def test_deduplicates_repeated_tutor_at_same_time(self):
        """Same tutor twice at one start time yields a single candidate."""
        self._mock_tutors([{"id": 4584, "name": "Paula"}])
        self._mock_calendar(
            {
                "2026-08-03": [
                    _slot("4584", "2026-08-03T11:00:00.000Z"),
                    _slot("4584", "2026-08-03T11:00:00.000Z"),
                ]
            }
        )

        result = get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00")
        assert len(result) == 1

    def test_matches_across_date_keys(self):
        """Slots are matched on start_time, not on which date key they sit under."""
        self._mock_tutors([{"id": 4584, "name": "Paula"}])
        self._mock_calendar(
            {
                "2026-08-01": [_slot("4584", "2026-08-01T11:00:00.000Z")],
                "2026-08-03": [_slot("4584", "2026-08-03T11:00:00.000Z")],
            }
        )

        result = get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00")
        assert len(result) == 1
        assert result[0]["start_time_local"] == "13:00"

    def test_local_time_in_result_cest(self):
        # 11:00 UTC in August = 13:00 CEST (UTC+2)
        self._mock_tutors([{"id": 4584, "name": "Paula"}])
        self._mock_calendar({"2026-08-03": [_slot("4584", "2026-08-03T11:00:00.000Z")]})

        result = get_available_teachers(self.mock_client, "2026-08-03T11:00:00+00:00")
        assert result[0]["start_time_local"] == "13:00"

    def test_local_time_in_result_cet(self):
        # 12:00 UTC in January = 13:00 CET (UTC+1)
        self._mock_tutors([{"id": 4584, "name": "Paula"}])
        self._mock_calendar({"2026-01-08": [_slot("4584", "2026-01-08T12:00:00.000Z")]})

        result = get_available_teachers(self.mock_client, "2026-01-08T12:00:00+00:00")
        assert result[0]["start_time_local"] == "13:00"

    def test_accepts_local_time_input(self):
        """Caller passes Madrid local time; matching happens in UTC."""
        self._mock_tutors([{"id": 4584, "name": "Paula"}])
        self._mock_calendar({"2026-08-03": [_slot("4584", "2026-08-03T11:00:00.000Z")]})

        result = get_available_teachers(self.mock_client, "2026-08-03T13:00:00+02:00")
        assert len(result) == 1

    def test_no_match_at_different_time(self, calendar_response, tutors_page1_response):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )
        self.router.get("/tutors", params={"page": 1}).mock(
            return_value=httpx.Response(
                200,
                json={**tutors_page1_response, "last_page": 1},
            )
        )

        assert (
            get_available_teachers(self.mock_client, "2026-08-03T04:00:00+00:00") == []
        )


class TestGetTeacherSlots(BaseTest):
    def setup_method(self, method):
        super().setup_method(method)
        self.mock_client.set_token(_AUTHED_TOKEN)

    def test_fetches_slots_for_teacher_string_id(self, calendar_response):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        slots = get_teacher_slots(self.mock_client, "4605")
        assert len(slots) > 0
        assert all(s["tutor_id"] == "4605" for s in slots)

    def test_fetches_slots_for_teacher_int_id(self, calendar_response):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        assert len(get_teacher_slots(self.mock_client, 4605)) == len(
            get_teacher_slots(self.mock_client, "4605")
        )

    def test_returns_empty_on_http_error(self):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(500)
        )

        assert get_teacher_slots(self.mock_client, "4605") == []

    def test_returns_empty_for_unknown_teacher(self, calendar_response):
        self.router.get("/booking/favorites/calendar").mock(
            return_value=httpx.Response(200, json=calendar_response)
        )

        assert get_teacher_slots(self.mock_client, "9999") == []
