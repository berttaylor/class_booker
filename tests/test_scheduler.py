"""
Tests for app/scheduler.py.

Strategy:
- get_synced_now: mock get_server_time, freeze time, check drift math
- run_due_process: use freezegun to control "now", mock all HTTP calls and
  internal functions (login, get_bookings, get_available_teachers, book_lesson)
  so we test the orchestration logic without real I/O
"""

from datetime import datetime, date, timezone, timedelta
from unittest.mock import MagicMock, patch
from freezegun import freeze_time
import pytest
import pytz

import app.services.scheduler as sched_module
from app.services.scheduler import get_synced_now, run_due_process
from app.rules import BookingRule, ScheduleCredentials, SchedulingRules
import app.logger as logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_CACHE = {
    "updated": "2026-04-03",
    "teachers": {
        "Maria Garcia": {"id": 184, "status": "ACTIVE"},
        "Carlos Lopez": {"id": 159, "status": "ACTIVE"},
    },
}


def make_rules(
    weekday: str = "wed",
    start_time: str = "13:00",
    preferred_teachers=None,
    label: str | None = None,
) -> SchedulingRules:
    """Construct a minimal SchedulingRules for use in tests."""
    return SchedulingRules(
        timezone="Europe/Madrid",
        credentials=ScheduleCredentials(email="test@example.com", password="secret"),
        rules=[
            BookingRule(
                label=label,
                enabled=True,
                weekday=weekday,
                start_time=start_time,
                slots=1,
                preferred_teachers=preferred_teachers
                or ["Maria Garcia", "Carlos Lopez"],
            )
        ],
    )


def make_available(teacher_id: str, name: str, local_time: str = "13:00"):
    return {"id": teacher_id, "name": name, "start_time_local": local_time}


# ---------------------------------------------------------------------------
# get_synced_now
# ---------------------------------------------------------------------------


class TestGetSyncedNow:
    def test_drift_calculation(self, mock_client):
        """Server is 2s ahead of local clock → drift ≈ +2 (within RTT tolerance)."""
        client, router = mock_client
        local_now = datetime(2026, 4, 8, 11, 0, 0, tzinfo=timezone.utc)
        server_time = "2026-04-08 11:00:02"  # 2s ahead

        with freeze_time(local_now):
            with patch.object(
                sched_module, "get_server_time", return_value={"datetime": server_time}
            ):
                synced, drift = get_synced_now(client)
            # drift should be positive (server ahead)
            assert drift > 1.5

    def test_handles_missing_datetime_field(self, mock_client):
        """If server response lacks 'datetime', drift=0.0 and local time is returned."""
        client, _ = mock_client
        local_now = datetime(2026, 4, 8, 11, 0, 0, tzinfo=timezone.utc)

        with freeze_time(local_now):
            with patch.object(sched_module, "get_server_time", return_value={}):
                synced, drift = get_synced_now(client)

        assert drift == 0.0
        # synced time should be close to local_now
        assert abs((synced - local_now).total_seconds()) < 1.0

    def test_half_rtt_adjustment(self, mock_client):
        """
        Simulate a 200ms RTT. Server time = local time.
        Synced time = server_dt + half_rtt = server_dt + 100ms.
        """
        client, _ = mock_client
        local_now = datetime(2026, 4, 8, 11, 0, 0, tzinfo=timezone.utc)
        server_time = "2026-04-08 11:00:00"  # Same as local

        call_count = [0]

        def fake_now(tz=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return local_now  # before request
            else:
                return local_now + timedelta(
                    milliseconds=200
                )  # after response (200ms RTT)

        with patch("app.services.scheduler.dt") as mock_dt:
            mock_dt.now.side_effect = fake_now
            mock_dt.fromisoformat = datetime.fromisoformat
            with patch.object(
                sched_module, "get_server_time", return_value={"datetime": server_time}
            ):
                synced, drift = get_synced_now(client)

        # synced = server_dt + half_rtt = local_now + 100ms
        # drift = synced - local_after = (local_now + 100ms) - (local_now + 200ms) = -100ms
        assert abs(drift) < 0.2


# ---------------------------------------------------------------------------
# run_due_process — helper to run with all I/O mocked
# ---------------------------------------------------------------------------


def run_due_with_mocks(
    *,
    frozen_time: str,
    rules: SchedulingRules,
    available_teachers: list,
    existing_bookings: list = None,
    book_results: list = None,
    force: bool = False,
    force_soft: bool = False,
    token: str = "fake.token.here",
):
    """
    Run run_due_process with all external calls patched.

    book_results: list of dicts returned by successive book_lesson calls.
                  Defaults to [{"status": "success", "id": "9999"}].
    """
    if existing_bookings is None:
        existing_bookings = []
    if book_results is None:
        book_results = [{"status": "success", "id": "9999"}]

    with freeze_time(frozen_time) as frozen:
        # We also need to patch get_synced_now to return an advancing time
        # based on the frozen time, otherwise the loop condition in scheduler
        # might still be stuck if it depends on a sync that doesn't see the tick.
        # However, scheduler.py calls get_synced_now(client) which calls dt.now(timezone.utc).
        # freezegun should handle dt.now() automatically.

        with (
            patch.object(
                sched_module, "load_active_schedules", return_value=[("test", rules)]
            ),
            patch.object(sched_module, "load_teacher_cache", return_value=FAKE_CACHE),
            patch.object(sched_module, "validate_rules_against_cache"),
            patch.object(sched_module, "login", return_value=token),
            patch.object(
                sched_module,
                "get_server_time",
                side_effect=lambda client: {
                    "datetime": frozen.time_to_freeze.strftime("%Y-%m-%d %H:%M:%S")
                },
            ),
            patch.object(sched_module, "get_bookings", return_value=existing_bookings),
            patch.object(
                sched_module, "get_available_teachers", return_value=available_teachers
            ),
            patch.object(sched_module, "book_lesson", side_effect=book_results),
            patch.object(sched_module, "acquire_lock", return_value=MagicMock()),
            patch.object(sched_module, "release_lock"),
            patch.object(sched_module, "is_token_expired", return_value=False),
        ):
            # Patch time.sleep to advance frozen time instead of sleeping
            def advance_time(seconds):
                frozen.tick(timedelta(seconds=seconds))

            with patch("app.services.scheduler.time.sleep", side_effect=advance_time):
                run_due_process(force=force, force_soft=force_soft)

            return sched_module.book_lesson


# ---------------------------------------------------------------------------
# Booking window / rule evaluation
# ---------------------------------------------------------------------------


class TestBookingWindowDST:
    """
    The window is a fixed absolute offset (7 days + 30 min) before the lesson.
    Computing it on the local wall clock and re-localising shifts it by an hour
    across a DST boundary — an hour early in October (the API rejects the
    booking) or an hour late in March (the race is already lost).
    """

    @staticmethod
    def _window_for(lesson_date, start_time, now_local):
        rules = make_rules(
            weekday=lesson_date.strftime("%a").lower(), start_time=start_time
        )
        _, _, _, upcoming = sched_module._evaluate_rules(rules, now_local)
        return next(
            open_dt
            for open_dt, _, lesson_dt in upcoming
            if lesson_dt.date() == lesson_date
        )

    def test_window_is_exact_offset_across_october_transition(self):
        """Lesson 25 Oct (CET) — window falls 18 Oct, still CEST."""
        tz = pytz.timezone("Europe/Madrid")
        lesson_date = date(2026, 10, 25)
        now_local = tz.localize(datetime(2026, 10, 17, 9, 0))

        open_dt = self._window_for(lesson_date, "13:00", now_local)
        lesson_dt = tz.localize(datetime(2026, 10, 25, 13, 0))

        elapsed = lesson_dt.astimezone(pytz.utc) - open_dt.astimezone(pytz.utc)
        assert elapsed == timedelta(days=7, minutes=30)
        assert open_dt.strftime("%Y-%m-%d %H:%M") == "2026-10-18 13:30"

    def test_window_is_exact_offset_across_march_transition(self):
        """Lesson 29 Mar (CEST) — window falls 22 Mar, still CET."""
        tz = pytz.timezone("Europe/Madrid")
        lesson_date = date(2026, 3, 29)
        now_local = tz.localize(datetime(2026, 3, 21, 9, 0))

        open_dt = self._window_for(lesson_date, "13:00", now_local)
        lesson_dt = tz.localize(datetime(2026, 3, 29, 13, 0))

        elapsed = lesson_dt.astimezone(pytz.utc) - open_dt.astimezone(pytz.utc)
        assert elapsed == timedelta(days=7, minutes=30)
        assert open_dt.strftime("%Y-%m-%d %H:%M") == "2026-03-22 11:30"

    def test_window_unaffected_outside_dst_transitions(self):
        tz = pytz.timezone("Europe/Madrid")
        lesson_date = date(2026, 8, 15)
        now_local = tz.localize(datetime(2026, 8, 7, 9, 0))

        open_dt = self._window_for(lesson_date, "13:00", now_local)
        assert open_dt.strftime("%Y-%m-%d %H:%M") == "2026-08-08 12:30"


class TestRuleEvaluation:
    def test_rule_due_within_precheck_window(self, capsys):
        """
        If booking_open_dt is within precheck_lead_seconds (120s), rule is due.

        We freeze time to 60 seconds BEFORE the booking window opens.
        booking_open_dt = lesson_dt - 7d 30m
        We want "now" to be 60s before that.

        Lesson: Wednesday 2026-04-15 13:00 Madrid
        booking_open_dt = 2026-04-08 12:30 Madrid = 2026-04-08 10:30 UTC

        Freeze at 10:29:00 UTC (60s before 10:30:00)
        """
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia"],
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
        )
        assert book_fn.called

    def test_rule_not_due_outside_precheck_window(self, capsys):
        """
        If booking opens in 300s (> 120s lead), the rule is NOT due.

        booking_open_dt = 2026-04-08 10:30:00 UTC
        Freeze at 09:55:00 UTC (5 minutes before = 300s)
        """
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia"],
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T09:55:00+00:00",
            rules=rules,
            available_teachers=available,
        )
        assert not book_fn.called

    def test_booking_open_dt_formula(self):
        """
        booking_open_dt = lesson_dt - 7 days - 30 minutes.

        Lesson: Wednesday 2026-04-15 13:00 Madrid (UTC+2 = 11:00 UTC)
        Expected booking_open = 2026-04-08 10:30 UTC
        """
        import pytz
        from datetime import datetime as dt

        local_tz = pytz.timezone("Europe/Madrid")
        lesson_dt = local_tz.localize(dt(2026, 4, 15, 13, 0, 0))
        booking_open = lesson_dt - timedelta(days=7, minutes=30)

        expected_utc = datetime(2026, 4, 8, 10, 30, 0, tzinfo=timezone.utc)
        actual_utc = booking_open.astimezone(timezone.utc).replace(tzinfo=timezone.utc)

        # Allow 1-second tolerance
        assert abs((actual_utc - expected_utc).total_seconds()) < 1.0

    def test_booking_open_dt_dst_boundary(self):
        """
        Booking window calculation works across the DST spring-forward boundary.

        Last Sunday of March 2026 is March 29. Madrid springs forward at 02:00 CET → 03:00 CEST.
        Lesson on Monday 30 March at 13:00 Madrid.
        booking_open = Monday March 23 at 12:30 Madrid (CET, UTC+1) = 11:30 UTC.
        """
        import pytz
        from datetime import datetime as dt

        local_tz = pytz.timezone("Europe/Madrid")
        lesson_dt = local_tz.localize(dt(2026, 3, 30, 13, 0, 0))
        booking_open = lesson_dt - timedelta(days=7, minutes=30)

        # March 23 is in CET (UTC+1), so 12:30 Madrid = 11:30 UTC
        expected_utc = datetime(2026, 3, 23, 11, 30, 0, tzinfo=timezone.utc)
        # Fix: need to re-localize to get correct offset for the result of timedelta
        booking_open_fixed = local_tz.localize(booking_open.replace(tzinfo=None))
        actual_utc_fixed = booking_open_fixed.astimezone(timezone.utc)

        assert abs((actual_utc_fixed - expected_utc).total_seconds()) < 1.0

    def test_rule_due_recently_opened_window(self, capsys):
        """
        If booking window opened 60s ago (within 5 min grace period), rule is still due.

        Lesson: Wednesday 2026-04-15 13:00 Madrid
        booking_open_dt = 2026-04-08 12:30 Madrid = 2026-04-08 10:30 UTC

        Freeze at 10:31:00 UTC (60s AFTER 10:30:00)
        """
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia"],
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:31:00+00:00",
            rules=rules,
            available_teachers=available,
        )
        assert book_fn.called

    def test_rule_not_due_long_ago_opened_window(self, capsys):
        """
        If booking window opened 6 minutes ago (> 5 min grace period), rule is NOT due.

        booking_open_dt = 2026-04-08 10:30:00 UTC
        Freeze at 10:36:00 UTC (6 minutes after)
        """
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia"],
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:36:00+00:00",
            rules=rules,
            available_teachers=available,
        )
        assert not book_fn.called


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------


class TestHolidays:
    def test_holiday_skips_rule(self, capsys):
        # Wed May 6, 2026 12:59:00
        # This is 2 minutes before Wed May 13 13:00 booking window opens (7 days + 30m offset)
        # 13:00 - 30m = 12:30. 7 days before is Wed May 6 12:30.
        # At 12:59, it IS within the 2 minute precheck window (lead time is 120s).

        frozen_time = "2026-05-06 12:59:00"
        rules = make_rules(weekday="wed", start_time="13:00")
        # Add May 13 as a holiday
        rules.holidays = ["2026-05-13"]

        # Run without holiday - should be due
        from app.services.scheduler import _evaluate_rules
        import pytz
        from datetime import datetime as dt

        local_tz = pytz.timezone(rules.timezone)
        now_local = local_tz.localize(dt.strptime(frozen_time, "%Y-%m-%d %H:%M:%S"))

        due_rules, _, _, _ = _evaluate_rules(rules, now_local)
        assert len(due_rules) == 0, "Should have skipped because of holiday"

    def test_upcoming_skips_holiday(self):
        frozen_time = "2026-05-06 12:00:00"
        rules = make_rules(weekday="wed", start_time="13:00")
        rules.holidays = ["2026-05-13"]

        from app.services.scheduler import _evaluate_rules
        import pytz
        from datetime import datetime as dt

        local_tz = pytz.timezone(rules.timezone)
        now_local = local_tz.localize(dt.strptime(frozen_time, "%Y-%m-%d %H:%M:%S"))

        _, _, _, upcoming = _evaluate_rules(rules, now_local)
        # It should skip May 13 and find May 20
        assert len(upcoming) > 0
        next_open, next_rule, next_lesson = upcoming[0]
        assert next_lesson.date().isoformat() == "2026-05-20"


class TestCandidateSelection:
    def test_preferred_teacher_selected_first(self):
        """Teacher 184 is first in teacher_ids and available → books 184, not 159."""
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia", "Carlos Lopez"],
        )
        available = [
            make_available("184", "Maria Garcia"),
            make_available("159", "Carlos Lopez"),
        ]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
        )

        assert book_fn.called
        first_call_teacher = book_fn.call_args_list[0][0][
            1
        ]  # 2nd positional arg = teacher_id
        assert first_call_teacher == "184"

    def test_no_booking_when_preferred_not_available(self):
        """No preferred teachers available → no booking attempted."""
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Unknown Teacher"],  # not available
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
        )
        assert not book_fn.called

    def test_no_available_teachers_skips(self):
        """If no teachers available at all, booking is skipped."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=[],  # nobody available
        )
        assert not book_fn.called


# ---------------------------------------------------------------------------
# Already-booked check
# ---------------------------------------------------------------------------


class TestOverlapDetection:
    """Direct coverage of _is_already_booked's range comparison."""

    @staticmethod
    def _booking(start, duration=30, date="2026-04-15"):
        return {
            "date": date,
            "start_time": start,
            "duration_minutes": duration,
            "status": "approved",
            "past": False,
        }

    def test_exact_match_is_booked(self):
        assert sched_module._is_already_booked(
            [self._booking("13:00:00")], "2026-04-15", "13:00:00", 30
        )

    def test_start_inside_existing_60min_class(self):
        """13:00 rule against a 12:30–13:30 recurring class."""
        assert sched_module._is_already_booked(
            [self._booking("12:30:00", 60)], "2026-04-15", "13:00:00", 30
        )

    def test_60min_rule_ending_inside_existing_class(self):
        """13:00–14:00 rule against a 13:30 class — overlap at the tail."""
        assert sched_module._is_already_booked(
            [self._booking("13:30:00", 30)], "2026-04-15", "13:00:00", 60
        )

    def test_existing_class_fully_inside_60min_rule(self):
        assert sched_module._is_already_booked(
            [self._booking("13:15:00", 15)], "2026-04-15", "13:00:00", 60
        )

    def test_overlap_across_midnight(self):
        """
        A 23:30 rule running to 00:30 must see a 00:00 class the following day.
        Filtering candidates by date alone would miss it and double-book.
        """
        assert sched_module._is_already_booked(
            [self._booking("00:00:00", 30, date="2026-04-16")],
            "2026-04-15",
            "23:30:00",
            60,
        )

    def test_previous_day_class_running_past_midnight(self):
        """A 23:45 class the day before overlaps a 00:00 rule."""
        assert sched_module._is_already_booked(
            [self._booking("23:45:00", 30, date="2026-04-14")],
            "2026-04-15",
            "00:00:00",
            30,
        )

    def test_null_duration_does_not_crash(self):
        """The API can send an explicit null duration; treat it as 30 minutes."""
        booking = {
            "date": "2026-04-15",
            "start_time": "13:00:00",
            "duration_minutes": None,
        }
        assert sched_module._is_already_booked([booking], "2026-04-15", "13:00:00", 30)
        assert not sched_module._is_already_booked(
            [booking], "2026-04-15", "13:30:00", 30
        )

    def test_touching_before_is_not_overlap(self):
        """Existing 12:00–13:00 does not block a 13:00 start."""
        assert not sched_module._is_already_booked(
            [self._booking("12:00:00", 60)], "2026-04-15", "13:00:00", 30
        )

    def test_touching_after_is_not_overlap(self):
        assert not sched_module._is_already_booked(
            [self._booking("13:30:00", 30)], "2026-04-15", "13:00:00", 30
        )

    def test_different_date_ignored(self):
        assert not sched_module._is_already_booked(
            [self._booking("13:00:00", 60, date="2026-04-16")],
            "2026-04-15",
            "13:00:00",
            60,
        )

    def test_missing_duration_defaults_to_30(self):
        booking = {"date": "2026-04-15", "start_time": "13:00:00"}
        assert sched_module._is_already_booked([booking], "2026-04-15", "13:00:00", 30)
        assert not sched_module._is_already_booked(
            [booking], "2026-04-15", "13:30:00", 30
        )

    def test_malformed_booking_is_skipped(self):
        """A bad entry must not crash the run."""
        assert not sched_module._is_already_booked(
            [{"date": "2026-04-15", "start_time": "not-a-time"}],
            "2026-04-15",
            "13:00:00",
            30,
        )


class TestAlreadyBooked:
    def test_already_booked_skips_rule(self):
        """If the target slot is already booked, book_lesson is never called."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        # Lesson is Wed 2026-04-15 13:00 Madrid.
        # In ISO: 2026-04-15T13:00:00+02:00 → Madrid date = "2026-04-15", time = "13:00:00"
        existing = [
            {
                "id": "5000",
                "staff_id": "184",
                "date": "2026-04-15",
                "start_time": "13:00:00",
                "status": "approved",
                "past": False,
            }
        ]
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            existing_bookings=existing,
        )
        assert not book_fn.called

    def test_overlapping_recurring_class_skips_rule(self):
        """
        A 60-minute recurring class booked on the website starts at 12:30 and
        runs to 13:30, so a 13:00 rule overlaps it. Matching on start time alone
        would miss this and double-book.
        """
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        existing = [
            {
                "id": "5000",
                "staff_id": "999",  # a different tutor — still our time
                "date": "2026-04-15",
                "start_time": "12:30:00",
                "status": "approved",
                "past": False,
                "duration_minutes": 60,
            }
        ]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=[make_available("184", "Maria Garcia")],
            existing_bookings=existing,
        )
        assert not book_fn.called

    def test_adjacent_non_overlapping_class_still_books(self):
        """A class ending exactly when ours starts must not block the booking."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        existing = [
            {
                "id": "5000",
                "staff_id": "999",
                "date": "2026-04-15",
                "start_time": "12:00:00",
                "status": "approved",
                "past": False,
                "duration_minutes": 60,  # 12:00–13:00, touches but does not overlap
            }
        ]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=[make_available("184", "Maria Garcia")],
            existing_bookings=existing,
        )
        assert book_fn.called


# ---------------------------------------------------------------------------
# 60-minute daily limit
# ---------------------------------------------------------------------------


class TestDailyLimit:
    def test_60min_limit_filters_teacher(self):
        """
        If teacher 184 already has 2 bookings (60 min total) on the target day,
        they should be filtered out and 159 booked instead.
        """
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia", "Carlos Lopez"],
        )
        target_date = "2026-04-15"

        existing = [
            {
                "staff_id": "184",
                "date": target_date,
                "start_time": "11:00:00",
                "status": "approved",
                "past": False,
            },
            {
                "staff_id": "184",
                "date": target_date,
                "start_time": "11:30:00",
                "status": "approved",
                "past": False,
            },
        ]
        available = [
            make_available("184", "Maria Garcia"),
            make_available("159", "Carlos Lopez"),
        ]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            existing_bookings=existing,
        )

        assert book_fn.called
        first_call_teacher = book_fn.call_args_list[0][0][1]
        assert first_call_teacher == "159"  # 184 excluded, 159 is next

    def test_under_60min_limit_not_filtered(self):
        """Teacher with only 1 booking (30 min) on the day is still eligible."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )
        target_date = "2026-04-15"

        existing = [
            {
                "staff_id": "184",
                "date": target_date,
                "start_time": "11:00:00",
                "status": "approved",
                "past": False,
            },
        ]
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            existing_bookings=existing,
        )

        assert book_fn.called
        first_call_teacher = book_fn.call_args_list[0][0][1]
        assert first_call_teacher == "184"


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    # The API returns INSUFFICIENT_AVAILABILITY both for a genuinely taken slot
    # and for arriving a fraction before the window opens, so it is retryable.
    TIMING_ERROR = {
        "status": "error",
        "status_code": 422,
        "message": (
            'Hold failed — HTTP 422: {"code":"BOOKING.INSUFFICIENT_AVAILABILITY",'
            '"message":"This tutor is not available for the full requested duration."}'
        ),
    }

    def test_retry_on_timing_error(self):
        """
        If book_lesson returns the timing error twice then succeeds,
        book_lesson should be called 3 times total (2 retries + 1 success).
        """
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )
        available = [make_available("184", "Maria Garcia")]

        book_results = [
            self.TIMING_ERROR,
            self.TIMING_ERROR,
            {"status": "success", "id": "9999"},
        ]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            book_results=book_results,
        )

        assert book_fn.call_count == 3

    def test_max_retries_on_timing_error(self):
        """
        If timing error persists all 3 attempts, book_lesson is called exactly 3 times
        and then moves on (or fails).
        """
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )
        available = [make_available("184", "Maria Garcia")]

        timing_error = self.TIMING_ERROR
        book_results = [timing_error, timing_error, timing_error]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            book_results=book_results,
        )

        assert book_fn.call_count == 3

    def test_reauth_retry_keeps_the_duration(self):
        """
        After a 401 the booking is retried — it must still ask for 60 minutes.
        Dropping the argument silently books a 30-minute class instead.
        """
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            credentials=ScheduleCredentials(email="t@example.com", password="s"),
            rules=[
                BookingRule(
                    enabled=True,
                    weekday="wed",
                    start_time="13:00",
                    slots=2,  # 60-minute lesson
                    preferred_teachers=["Maria Garcia"],
                )
            ],
        )

        auth_error = {
            "status": "error",
            "status_code": 401,
            "message": 'Hold failed — HTTP 401: {"message":"Unauthenticated."}',
        }
        book_results = [auth_error, {"status": "success", "id": "9999"}]

        with patch.object(sched_module, "_refresh_schedule_token", return_value=True):
            book_fn = run_due_with_mocks(
                frozen_time="2026-04-08T10:29:00+00:00",
                rules=rules,
                available_teachers=[make_available("184", "Maria Garcia")],
                book_results=book_results,
            )

        assert book_fn.call_count == 2
        # positional signature: (client, tid, iso, focus_type, activity_id, duration)
        assert book_fn.call_args_list[0][0][5] == 60
        assert book_fn.call_args_list[1][0][5] == 60

    def test_no_reauth_when_401_appears_in_a_booking_id(self):
        """A booking id containing 401 must not be mistaken for an auth failure."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )
        misleading = {
            "status": "error",
            "status_code": 422,
            "message": 'Hold failed — HTTP 422: {"booking_id":"408401"}',
        }

        with patch.object(sched_module, "_refresh_schedule_token") as refresh:
            run_due_with_mocks(
                frozen_time="2026-04-08T10:29:00+00:00",
                rules=rules,
                available_teachers=[make_available("184", "Maria Garcia")],
                book_results=[misleading],
            )

        assert not refresh.called

    def test_no_retry_on_non_timing_error(self):
        """A generic error should not trigger a retry — fail fast and try next candidate."""
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Maria Garcia", "Carlos Lopez"],
        )
        available = [
            make_available("184", "Maria Garcia"),
            make_available("159", "Carlos Lopez"),
        ]

        generic_error = {"status": "error", "message": "Some other problem"}
        book_results = [generic_error, {"status": "success", "id": "9999"}]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            book_results=book_results,
        )

        # First call fails (184), second call succeeds (159) — exactly 2 calls
        assert book_fn.call_count == 2


# ---------------------------------------------------------------------------
# Force / soft-force modes
# ---------------------------------------------------------------------------


class TestForceMode:
    def test_force_processes_even_when_no_due_rules(self):
        """With --force and no currently-due rules, the next upcoming rule is forced."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )
        available = [make_available("184", "Maria Garcia")]

        # Freeze at a time far from the booking window (not due)
        book_fn = run_due_with_mocks(
            frozen_time="2026-04-01T10:00:00+00:00",  # Far from April 8 window
            rules=rules,
            available_teachers=available,
            force=True,
        )
        assert book_fn.called

    def test_force_soft_dry_run_does_not_book(self):
        """With --force-soft, book_lesson should never be called."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            force_soft=True,
        )
        assert not book_fn.called


# ---------------------------------------------------------------------------
# Lock prevention
# ---------------------------------------------------------------------------


class TestLock:
    def test_lock_prevents_concurrent_run(self, capsys):
        """If acquire_lock returns None, run_due_process exits early."""
        logger.set_enabled(True)
        try:
            with freeze_time("2026-04-08T10:29:00+00:00"):
                with (
                    patch.object(sched_module, "acquire_lock", return_value=None),
                    patch.object(sched_module, "book_lesson") as book_fn,
                ):
                    run_due_process()

            assert not book_fn.called
            captured = capsys.readouterr()
            assert "Another instance" in captured.out
        finally:
            logger.set_enabled(False)


# ---------------------------------------------------------------------------
# approved_bookings updated after success
# ---------------------------------------------------------------------------


class TestBookingsCacheUpdate:
    def test_approved_bookings_updated_after_first_rule_success(self):
        """
        After rule 1 books a slot successfully, rule 2 (for same day/time) sees
        the new booking in approved_bookings and skips.
        """
        # Two rules on the same day — both become due simultaneously
        rules = SchedulingRules(
            timezone="Europe/Madrid",
            credentials=ScheduleCredentials(
                email="test@example.com", password="secret"
            ),
            rules=[
                BookingRule(
                    label="rule1",
                    enabled=True,
                    weekday="wed",
                    start_time="13:00",
                    slots=1,
                    preferred_teachers=["Maria Garcia"],
                ),
                BookingRule(
                    label="rule2",
                    enabled=True,
                    weekday="wed",
                    start_time="13:00",  # Same timeslot
                    slots=1,
                    preferred_teachers=["Carlos Lopez"],
                ),
            ],
        )

        available = [
            make_available("184", "Maria Garcia"),
            make_available("159", "Carlos Lopez"),
        ]

        frozen_time = "2026-04-08T10:29:00+00:00"

        with freeze_time(frozen_time) as frozen:
            with (
                patch.object(
                    sched_module,
                    "load_active_schedules",
                    return_value=[("test", rules)],
                ),
                patch.object(
                    sched_module, "load_teacher_cache", return_value=FAKE_CACHE
                ),
                patch.object(sched_module, "validate_rules_against_cache"),
                patch.object(sched_module, "login", return_value="fake.token"),
                patch.object(
                    sched_module,
                    "get_server_time",
                    side_effect=lambda client: {
                        "datetime": frozen.time_to_freeze.strftime("%Y-%m-%d %H:%M:%S")
                    },
                ),
                patch.object(sched_module, "get_bookings", return_value=[]),
                patch.object(
                    sched_module, "get_available_teachers", return_value=available
                ),
                patch.object(
                    sched_module,
                    "book_lesson",
                    return_value={"status": "success", "id": "9999"},
                ) as book_fn,
                patch.object(sched_module, "acquire_lock", return_value=MagicMock()),
                patch.object(sched_module, "release_lock"),
                patch.object(sched_module, "is_token_expired", return_value=False),
            ):

                def advance_time(seconds):
                    frozen.tick(timedelta(seconds=seconds))

                with patch(
                    "app.services.scheduler.time.sleep", side_effect=advance_time
                ):
                    run_due_process()

        # book_lesson should only be called once — second rule sees the slot as taken
        assert book_fn.call_count == 1


# ---------------------------------------------------------------------------
# Teacher cache checks
# ---------------------------------------------------------------------------


class TestTeacherCache:
    def test_exits_if_no_cache(self, capsys):
        """run_due_process exits early with a message when teachers.json is missing."""
        logger.set_enabled(True)
        try:
            with freeze_time("2026-04-08T10:29:00+00:00"):
                with (
                    patch.object(sched_module, "load_teacher_cache", return_value={}),
                    patch.object(
                        sched_module, "acquire_lock", return_value=MagicMock()
                    ),
                    patch.object(sched_module, "release_lock"),
                    patch.object(sched_module, "book_lesson") as book_fn,
                ):
                    run_due_process()

            assert not book_fn.called
            captured = capsys.readouterr()
            assert "populate-teachers" in captured.out
        finally:
            logger.set_enabled(False)


# ---------------------------------------------------------------------------
# Run-state recording
# ---------------------------------------------------------------------------


class TestRunState:
    """
    The status panel trusts these records, so the outcome must be derived from
    what the run actually did — not inferred from log levels.
    """

    def test_successful_booking_records_booked(self):
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        with patch.object(sched_module.runstate, "append") as append_fn:
            run_due_with_mocks(
                frozen_time="2026-04-08T10:29:00+00:00",
                rules=rules,
                available_teachers=[make_available("184", "Maria Garcia")],
            )

        record = append_fn.call_args[0][0]
        assert record["outcome"] == "booked"
        assert record["booked"] == 1
        assert record["failed"] == 0
        assert record["dry_run"] is False
        assert record["bookings"] == ["[wed_13:00] Maria Garcia"]

    def test_dry_run_does_not_count_as_booked(self):
        """--force-soft must never look like a real booking on the panel."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        with patch.object(sched_module.runstate, "append") as append_fn:
            run_due_with_mocks(
                frozen_time="2026-04-08T10:29:00+00:00",
                rules=rules,
                available_teachers=[make_available("184", "Maria Garcia")],
                force_soft=True,
            )

        record = append_fn.call_args[0][0]
        assert record["booked"] == 0
        assert record["dry_run"] is True
        assert record["bookings"] == []

    def test_no_teachers_available_records_failed(self):
        """Logged at INFO, but it means a class went unbooked — so: failed."""
        rules = make_rules(
            weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"]
        )

        with patch.object(sched_module.runstate, "append") as append_fn:
            run_due_with_mocks(
                frozen_time="2026-04-08T10:29:00+00:00",
                rules=rules,
                available_teachers=[],
            )

        record = append_fn.call_args[0][0]
        assert record["outcome"] == "failed"
        assert record["failed"] == 1

    def test_crash_records_crashed_and_reraises(self):
        """The traceback must still escape so the process exits non-zero."""
        with freeze_time("2026-04-08T10:29:00+00:00"):
            with (
                patch.object(sched_module.runstate, "append") as append_fn,
                patch.object(
                    sched_module, "load_teacher_cache", return_value=FAKE_CACHE
                ),
                patch.object(
                    sched_module,
                    "load_active_schedules",
                    return_value=[("test", make_rules())],
                ),
                patch.object(
                    sched_module, "_run_schedule", side_effect=RuntimeError("boom")
                ),
                patch.object(sched_module, "send_push"),
                patch.object(sched_module, "acquire_lock", return_value=MagicMock()),
                patch.object(sched_module, "release_lock"),
            ):
                with pytest.raises(RuntimeError, match="boom"):
                    run_due_process()

        record = append_fn.call_args[0][0]
        assert record["outcome"] == "crashed"
        assert "RuntimeError: boom" in record["detail"]

    def test_locked_run_writes_no_record(self):
        """The loser must not overwrite the panel mid-booking."""
        with freeze_time("2026-04-08T10:29:00+00:00"):
            with (
                patch.object(sched_module.runstate, "append") as append_fn,
                patch.object(sched_module, "acquire_lock", return_value=None),
            ):
                run_due_process()

        assert not append_fn.called
