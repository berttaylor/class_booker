import httpx

from tests.base import BaseTest
from app.utils import normalize_datetime, get_server_time


class TestNormalizeDatetime:
    def test_utc_z_suffix(self):
        assert normalize_datetime("2026-04-08T13:00:00Z") == "2026-04-08T13:00:00+00:00"

    def test_positive_offset_cest(self):
        # 13:00 CEST (UTC+2) → 11:00 UTC
        assert (
            normalize_datetime("2026-04-08T13:00:00+02:00")
            == "2026-04-08T11:00:00+00:00"
        )

    def test_positive_offset_cet(self):
        # 13:00 CET (UTC+1) → 12:00 UTC
        assert (
            normalize_datetime("2026-01-08T13:00:00+01:00")
            == "2026-01-08T12:00:00+00:00"
        )

    def test_negative_offset(self):
        # 08:00 UTC-5 → 13:00 UTC
        assert (
            normalize_datetime("2026-04-08T08:00:00-05:00")
            == "2026-04-08T13:00:00+00:00"
        )

    def test_already_utc(self):
        assert (
            normalize_datetime("2026-04-08T11:00:00+00:00")
            == "2026-04-08T11:00:00+00:00"
        )

    def test_seconds_zeroed(self):
        assert (
            normalize_datetime("2026-04-08T11:00:45+00:00")
            == "2026-04-08T11:00:00+00:00"
        )

    def test_invalid_string_passthrough(self):
        assert normalize_datetime("not-a-date") == "not-a-date"

    def test_dst_summer_cest(self):
        assert (
            normalize_datetime("2026-07-15T13:00:00+02:00")
            == "2026-07-15T11:00:00+00:00"
        )

    def test_dst_winter_cet(self):
        assert (
            normalize_datetime("2026-01-15T13:00:00+01:00")
            == "2026-01-15T12:00:00+00:00"
        )

    def test_midnight_utc(self):
        assert normalize_datetime("2026-04-08T00:00:00Z") == "2026-04-08T00:00:00+00:00"


class TestGetServerTime(BaseTest):
    """Server time comes from the HTTP Date header — the API has no time endpoint."""

    def test_parses_rfc7231_date_header(self):
        self.router.get("/students/me/quota").mock(
            return_value=httpx.Response(
                200,
                json={"data": {}},
                headers={"Date": "Sat, 01 Aug 2026 10:30:45 GMT"},
            )
        )

        assert get_server_time(self.mock_client) == {"datetime": "2026-08-01T10:30:45"}

    def test_non_gmt_offset_converted_to_utc(self):
        self.router.get("/students/me/quota").mock(
            return_value=httpx.Response(
                200,
                json={"data": {}},
                headers={"Date": "Sat, 01 Aug 2026 12:30:45 +0200"},
            )
        )

        assert get_server_time(self.mock_client)["datetime"] == "2026-08-01T10:30:45"

    def test_error_on_http_failure(self):
        self.router.get("/students/me/quota").mock(
            return_value=httpx.Response(500, text="Server Error")
        )

        assert get_server_time(self.mock_client)["status"] == "error"

    def test_error_when_date_header_missing(self):
        self.router.get("/students/me/quota").mock(
            return_value=httpx.Response(200, json={"data": {}}, headers={})
        )

        result = get_server_time(self.mock_client)
        assert result["status"] == "error"
