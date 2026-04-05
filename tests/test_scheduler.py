"""
Tests for app/scheduler.py.

Strategy:
- get_synced_now: mock get_server_time, freeze time, check drift math
- run_due_process: use freezegun to control "now", mock all HTTP calls and
  internal functions (login, get_bookings, get_available_teachers, book_lesson)
  so we test the orchestration logic without real I/O
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from freezegun import freeze_time

import app.services.scheduler as sched_module
from app.services.scheduler import get_synced_now, run_due_process
from app.rules import BookingRule, BookingConfig, SchedulingRules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_CACHE = {
    "updated": "2026-04-03",
    "teachers": {
        "Maria Garcia": {"id": 184, "is_favorite": True, "status": "ACTIVE"},
        "Carlos Lopez": {"id": 159, "is_favorite": False, "status": "ACTIVE"},
    },
}


def make_rules(
    weekday: str = "wed",
    start_time: str = "13:00",
    preferred_teachers=None,
    allow_fallbacks: bool = True,
    label: str = "test",
    open_offset_days: int = 7,
    open_offset_minutes: int = 30,
    precheck_lead_seconds: int = 120,
) -> SchedulingRules:
    """Construct a minimal SchedulingRules for use in tests."""
    return SchedulingRules(
        timezone="Europe/Madrid",
        booking=BookingConfig(
            open_offset_days=open_offset_days,
            open_offset_minutes=open_offset_minutes,
            precheck_lead_seconds=precheck_lead_seconds,
        ),
        rules=[
            BookingRule(
                label=label,
                enabled=True,
                weekday=weekday,
                start_time=start_time,
                slots=1,
                preferred_teachers=preferred_teachers or ["Maria Garcia", "Carlos Lopez"],
                allow_fallbacks=allow_fallbacks,
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
            with patch.object(sched_module, "get_server_time", return_value={"datetime": server_time}):
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
                return local_now + timedelta(milliseconds=200)  # after response (200ms RTT)

        with patch("app.services.scheduler.dt") as mock_dt:
            mock_dt.now.side_effect = fake_now
            mock_dt.fromisoformat = datetime.fromisoformat
            with patch.object(sched_module, "get_server_time", return_value={"datetime": server_time}):
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
        
        with patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
             patch.object(sched_module, "load_teacher_cache", return_value=FAKE_CACHE), \
             patch.object(sched_module, "validate_rules_against_cache"), \
             patch.object(sched_module, "login", return_value=token), \
             patch.object(sched_module, "get_server_time", side_effect=lambda client: {"datetime": frozen.time_to_freeze.strftime("%Y-%m-%d %H:%M:%S")}), \
             patch.object(sched_module, "get_bookings", return_value=existing_bookings), \
             patch.object(sched_module, "get_available_teachers", return_value=available_teachers), \
             patch.object(sched_module, "book_lesson", side_effect=book_results), \
             patch.object(sched_module, "acquire_lock", return_value=MagicMock()), \
             patch.object(sched_module, "release_lock"), \
             patch.object(sched_module, "is_token_expired", return_value=False):

            # Patch time.sleep to advance frozen time instead of sleeping
            def advance_time(seconds):
                frozen.tick(timedelta(seconds=seconds))

            with patch("app.services.scheduler.time.sleep", side_effect=advance_time):
                run_due_process(force=force, force_soft=force_soft)

            return sched_module.book_lesson


# ---------------------------------------------------------------------------
# Booking window / rule evaluation
# ---------------------------------------------------------------------------

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
            precheck_lead_seconds=120,
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
            precheck_lead_seconds=120,
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


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

class TestCandidateSelection:
    def test_preferred_teacher_selected_first(self):
        """Teacher 184 is first in teacher_ids and available → books 184, not 159."""
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia", "Carlos Lopez"])
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
        first_call_teacher = book_fn.call_args_list[0][0][1]  # 2nd positional arg = teacher_id
        assert first_call_teacher == "184"

    def test_fallback_used_when_no_preferred(self):
        """No preferred teachers available, fallback=True → books the fallback teacher."""
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Unknown Teacher"],  # not available
            allow_fallbacks=True,
        )
        available = [make_available("184", "Maria Garcia")]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
        )
        assert book_fn.called

    def test_no_fallback_when_disabled(self):
        """No preferred teachers available, fallback=False → no booking attempted."""
        rules = make_rules(
            weekday="wed",
            start_time="13:00",
            preferred_teachers=["Unknown Teacher"],
            allow_fallbacks=False,
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
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=[],  # nobody available
        )
        assert not book_fn.called


# ---------------------------------------------------------------------------
# Already-booked check
# ---------------------------------------------------------------------------

class TestAlreadyBooked:
    def test_already_booked_skips_rule(self):
        """If the target slot is already booked, book_lesson is never called."""
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])

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


# ---------------------------------------------------------------------------
# 60-minute daily limit
# ---------------------------------------------------------------------------

class TestDailyLimit:
    def test_60min_limit_filters_teacher(self):
        """
        If teacher 184 already has 2 bookings (60 min total) on the target day,
        they should be filtered out and 159 booked instead.
        """
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia", "Carlos Lopez"])
        target_date = "2026-04-15"

        existing = [
            {"staff_id": "184", "date": target_date, "start_time": "11:00:00", "status": "approved", "past": False},
            {"staff_id": "184", "date": target_date, "start_time": "11:30:00", "status": "approved", "past": False},
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
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])
        target_date = "2026-04-15"

        existing = [
            {"staff_id": "184", "date": target_date, "start_time": "11:00:00", "status": "approved", "past": False},
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
# Adjacency prioritization
# ---------------------------------------------------------------------------

class TestAdjacencyPriority:
    def test_adjacent_teacher_moved_to_front(self):
        """
        If teacher 159 taught the slot at 12:30 (30 min before 13:00),
        they should be moved to the front of candidates even if 184 is preferred.
        """
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia", "Carlos Lopez"])
        target_date = "2026-04-15"

        existing = [
            # 159 taught the 12:30 slot
            {"staff_id": "159", "date": target_date, "start_time": "12:30:00", "status": "approved", "past": False},
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
        assert first_call_teacher == "159"  # adjacency takes precedence

    def test_adjacency_only_prioritizes_if_in_candidates(self):
        """Adjacent teacher who isn't in candidates (e.g. excluded by limit) is not promoted."""
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])
        target_date = "2026-04-15"

        existing = [
            # 159 taught previous slot but is NOT in teacher_ids and fallbacks disabled
            {"staff_id": "159", "date": target_date, "start_time": "12:30:00", "status": "approved", "past": False},
        ]
        available = [make_available("184", "Maria Garcia")]  # only 184 available

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
    def test_retry_on_timing_error(self):
        """
        If book_lesson returns the Spanish timing error twice then succeeds,
        book_lesson should be called 3 times total (2 retries + 1 success).
        """
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])
        available = [make_available("184", "Maria Garcia")]

        timing_error = {"status": "error", "message": "La fecha excede el límite de agendamiento."}
        book_results = [timing_error, timing_error, {"status": "success", "id": "9999"}]

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
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])
        available = [make_available("184", "Maria Garcia")]

        timing_error = {"status": "error", "message": "excede el agendamiento límite"}
        book_results = [timing_error, timing_error, timing_error]

        book_fn = run_due_with_mocks(
            frozen_time="2026-04-08T10:29:00+00:00",
            rules=rules,
            available_teachers=available,
            book_results=book_results,
        )

        assert book_fn.call_count == 3

    def test_no_retry_on_non_timing_error(self):
        """A generic error should not trigger a retry — fail fast and try next candidate."""
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia", "Carlos Lopez"])
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
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])
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
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])
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
        rules = make_rules(weekday="wed", start_time="13:00", preferred_teachers=["Maria Garcia"])

        with freeze_time("2026-04-08T10:29:00+00:00"):
            with patch.object(sched_module, "acquire_lock", return_value=None), \
                 patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
                 patch.object(sched_module, "book_lesson") as book_fn:
                run_due_process()

        assert not book_fn.called
        captured = capsys.readouterr()
        assert "Another instance" in captured.out


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
            booking=BookingConfig(
                open_offset_days=7,
                open_offset_minutes=30,
                precheck_lead_seconds=120,
            ),
            rules=[
                BookingRule(
                    label="rule1",
                    enabled=True,
                    weekday="wed",
                    start_time="13:00",
                    slots=1,
                    preferred_teachers=["Maria Garcia"],
                    allow_fallbacks=False,
                ),
                BookingRule(
                    label="rule2",
                    enabled=True,
                    weekday="wed",
                    start_time="13:00",  # Same timeslot
                    slots=1,
                    preferred_teachers=["Carlos Lopez"],
                    allow_fallbacks=False,
                ),
            ],
        )

        available = [
            make_available("184", "Maria Garcia"),
            make_available("159", "Carlos Lopez"),
        ]

        frozen_time = "2026-04-08T10:29:00+00:00"

        with freeze_time(frozen_time) as frozen:
            with patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
                 patch.object(sched_module, "load_teacher_cache", return_value=FAKE_CACHE), \
                 patch.object(sched_module, "validate_rules_against_cache"), \
                 patch.object(sched_module, "login", return_value="fake.token"), \
                 patch.object(sched_module, "get_server_time", side_effect=lambda client: {"datetime": frozen.time_to_freeze.strftime("%Y-%m-%d %H:%M:%S")}), \
                 patch.object(sched_module, "get_bookings", return_value=[]), \
                 patch.object(sched_module, "get_available_teachers", return_value=available), \
                 patch.object(sched_module, "book_lesson",
                              return_value={"status": "success", "id": "9999"}) as book_fn, \
                 patch.object(sched_module, "acquire_lock", return_value=MagicMock()), \
                 patch.object(sched_module, "release_lock"), \
                 patch.object(sched_module, "is_token_expired", return_value=False):

                def advance_time(seconds):
                    frozen.tick(timedelta(seconds=seconds))

                with patch("app.services.scheduler.time.sleep", side_effect=advance_time):
                    run_due_process()

        # book_lesson should only be called once — second rule sees the slot as taken
        assert book_fn.call_count == 1


# ---------------------------------------------------------------------------
# Teacher cache checks
# ---------------------------------------------------------------------------

class TestTeacherCache:
    def test_exits_if_no_cache(self, capsys):
        """run_due_process exits early with a message when teachers.json is missing."""
        rules = make_rules(weekday="wed", start_time="13:00")

        with freeze_time("2026-04-08T10:29:00+00:00"):
            with patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
                 patch.object(sched_module, "load_teacher_cache", return_value={}), \
                 patch.object(sched_module, "acquire_lock", return_value=MagicMock()), \
                 patch.object(sched_module, "release_lock"), \
                 patch.object(sched_module, "book_lesson") as book_fn:
                run_due_process()

        assert not book_fn.called
        captured = capsys.readouterr()
        assert "populate-teachers" in captured.out

    def test_refreshes_stale_cache(self, capsys):
        """If cache is older than update_teachers_frequency_days, populate_teachers is called."""
        rules = make_rules(weekday="wed", start_time="13:00")
        stale_cache = {**FAKE_CACHE, "updated": "2026-01-01"}  # very old

        with freeze_time("2026-04-08T10:00:00+00:00"):
            with patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
                 patch.object(sched_module, "load_teacher_cache", return_value=stale_cache), \
                 patch.object(sched_module, "validate_rules_against_cache"), \
                 patch.object(sched_module, "populate_teachers") as pop_fn, \
                 patch.object(sched_module, "acquire_lock", return_value=MagicMock()), \
                 patch.object(sched_module, "release_lock"):
                run_due_process()

        assert pop_fn.called


# ---------------------------------------------------------------------------
# Notion schedule integration
# ---------------------------------------------------------------------------

class TestNotionScheduleIntegration:
    def test_uses_notion_schedule_when_available(self):
        """If fetch_schedule_from_notion returns data, cache_schedule_locally is called."""
        rules = make_rules(weekday="wed", start_time="13:00")
        notion_data = {"timezone": "Europe/Madrid", "booking": {}, "rules": []}

        with freeze_time("2026-04-08T10:00:00+00:00"):
            with patch.object(sched_module, "fetch_schedule_from_notion", return_value=notion_data) as fetch_fn, \
                 patch.object(sched_module, "cache_schedule_locally") as cache_fn, \
                 patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
                 patch.object(sched_module, "load_teacher_cache", return_value=FAKE_CACHE), \
                 patch.object(sched_module, "validate_rules_against_cache"), \
                 patch.object(sched_module, "acquire_lock", return_value=MagicMock()), \
                 patch.object(sched_module, "release_lock"):
                run_due_process()

        fetch_fn.assert_called_once()
        cache_fn.assert_called_once_with(notion_data)

    def test_falls_back_to_yaml_when_notion_unavailable(self):
        """If fetch_schedule_from_notion returns None, cache_schedule_locally is not called."""
        rules = make_rules(weekday="wed", start_time="13:00")

        with freeze_time("2026-04-08T10:00:00+00:00"):
            with patch.object(sched_module, "fetch_schedule_from_notion", return_value=None), \
                 patch.object(sched_module, "cache_schedule_locally") as cache_fn, \
                 patch.object(sched_module, "load_scheduling_rules", return_value=rules), \
                 patch.object(sched_module, "load_teacher_cache", return_value=FAKE_CACHE), \
                 patch.object(sched_module, "validate_rules_against_cache"), \
                 patch.object(sched_module, "acquire_lock", return_value=MagicMock()), \
                 patch.object(sched_module, "release_lock"):
                run_due_process()

        cache_fn.assert_not_called()
