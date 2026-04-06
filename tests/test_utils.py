from app.utils import normalize_datetime


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
