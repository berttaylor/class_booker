import os
import sys
import time
import fcntl
import pytz
from datetime import datetime as dt, timedelta, timezone

from pathlib import Path

from app.api.auth import login, is_token_expired
from app.notifications import send_push
from app.api.availability import (
    get_available_teachers,
    get_favorite_tutor_ids,
    get_tutors_map,
)
from app.api.booking import get_bookings, book_lesson
from app.client import BookingClient
from app.config import app_config
from app.rules import (
    load_active_schedules,
    SchedulingRules,
    BOOKING_OPEN_OFFSET_DAYS,
    BOOKING_OPEN_BUFFER_SECONDS,
    BOOKING_OPEN_GRACE_MINUTES,
    BOOKING_PRECHECK_LEAD_SECONDS,
)
from app.teachers import load_teacher_cache, validate_rules_against_cache
from app.utils import get_server_time
from app import logger, runstate

BASE_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = BASE_DIR / "cache"

# Absolute, so the lock is the same file regardless of working directory —
# a cwd-relative path silently gives concurrent runs separate locks.
LOCK_FILE = str(BASE_DIR / ".run_due.lock")

# Per-run tallies, reset at the top of run_due_process. Incremented at the
# points that already know the outcome, so the panel never has to infer it by
# re-reading log messages.
_counts: dict = {}


def _reset_counts() -> None:
    _counts.clear()
    _counts.update({"booked": 0, "failed": 0, "next_window": None, "bookings": []})


_reset_counts()

# ---------------------------------------------------------------------------
# Lock management
# ---------------------------------------------------------------------------


def acquire_lock():
    f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (IOError, OSError):
        return None


def release_lock(f):
    if f:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Server time sync
# ---------------------------------------------------------------------------


def get_synced_now(client: BookingClient) -> tuple[dt, float]:
    """
    Fetches server time and calculates current UTC synced with the server.
    Accounts for network latency by assuming half-RTT.
    Returns (synced_now_utc, drift_seconds).
    """
    local_before = dt.now(timezone.utc)
    server_res = get_server_time(client)
    local_after = dt.now(timezone.utc)

    rtt = (local_after - local_before).total_seconds()
    half_rtt = rtt / 2.0

    drift = 0.0
    if "datetime" in server_res:
        try:
            server_dt_raw = dt.fromisoformat(
                server_res["datetime"].replace(" ", "T")
            ).replace(tzinfo=timezone.utc)
            server_dt_synced = server_dt_raw + timedelta(seconds=half_rtt)
            drift = (server_dt_synced - local_after).total_seconds()
            return server_dt_synced, drift
        except Exception:
            pass
    return dt.now(timezone.utc), 0.0


# ---------------------------------------------------------------------------
# run_due_process helpers
# ---------------------------------------------------------------------------


def _evaluate_rules(rules_data, now_local):
    """
    Iterates all enabled rules over the next 22 days, expanding each rule's
    slots into individual booking entries.
    Returns (due_rules, rule_lesson_times, rule_open_times, all_upcoming_rules).
    due_rules entries are (rule, slot_key) tuples; dicts are keyed by slot_key.
    """
    local_tz = pytz.timezone(rules_data.timezone)
    due_rules = []
    rule_lesson_times = {}
    rule_open_times = {}
    all_upcoming_rules = []

    for rule in rules_data.rules:
        if not rule.enabled:
            continue

        found_occurrence = False
        # 22 days, not 15: the nearest still-bookable occurrence is up to 8 days
        # out (today's midnight has usually passed already), so two consecutive
        # holidays on the same weekday put the next viable lesson at +22.
        for days_ahead in range(22):
            target_date = (now_local + timedelta(days=days_ahead)).date()
            if target_date.isoformat() in rules_data.holidays:
                continue

            weekday_str = target_date.strftime("%a").lower()
            if weekday_str != rule.weekday:
                continue

            for slot_time_str in rule.slot_times():
                lesson_time = dt.strptime(slot_time_str, "%H:%M").time()
                lesson_dt = local_tz.localize(dt.combine(target_date, lesson_time))

                # Midnight is a wall-clock concept, so localise the date
                # rather than subtracting an absolute offset from the lesson.
                # The gap to the lesson is therefore 7d ± 1h across a DST
                # boundary, and that is correct: the platform opens the day, not
                # a fixed number of hours before the class.
                open_date = target_date - timedelta(days=BOOKING_OPEN_OFFSET_DAYS)
                booking_open_dt = local_tz.localize(
                    dt.combine(open_date, dt.min.time())
                ) + timedelta(seconds=BOOKING_OPEN_BUFFER_SECONDS)

                if booking_open_dt < now_local - timedelta(
                    minutes=BOOKING_OPEN_GRACE_MINUTES
                ):
                    continue

                found_occurrence = True
                # One booking per rule occurrence — a 2-slot rule is a single
                # 60-minute lesson, so there is no slot index to disambiguate.
                slot_key = rule.id
                all_upcoming_rules.append((booking_open_dt, rule, lesson_dt))

                diff = (booking_open_dt - now_local).total_seconds()
                if diff <= BOOKING_PRECHECK_LEAD_SECONDS:
                    due_rules.append((rule, slot_key))
                    rule_lesson_times[slot_key] = lesson_dt.isoformat()
                    rule_open_times[slot_key] = booking_open_dt

            if found_occurrence:
                break  # Found next occurrence with future booking window

    return due_rules, rule_lesson_times, rule_open_times, all_upcoming_rules


def _apply_force_flag(
    actual_force,
    force_soft,
    due_rules,
    all_upcoming_rules,
    rule_lesson_times,
    rule_open_times,
):
    """
    When --force or --force-soft is active and no rules are due, injects the
    next upcoming rule into due_rules so it runs immediately.
    Mutates due_rules, rule_lesson_times, rule_open_times in place.
    """
    if actual_force and not due_rules and all_upcoming_rules:
        all_upcoming_rules.sort(key=lambda x: x[0])
        next_open_dt, next_rule, next_lesson_dt = all_upcoming_rules[0]
        slot_key = next_rule.id
        due_rules.append((next_rule, slot_key))
        rule_lesson_times[slot_key] = next_lesson_dt.isoformat()
        rule_open_times[slot_key] = next_open_dt


def _is_already_booked(
    approved_bookings, date_str, start_time_str, duration_minutes=30
) -> bool:
    """
    Returns True if an existing booking overlaps the requested time range.

    Compares ranges rather than exact start times: recurring classes booked via
    the website are 60 minutes long, so a rule starting halfway through one
    would not match on start time alone and would double-book.
    """
    target_start = dt.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M:%S")
    target_end = target_start + timedelta(minutes=duration_minutes)

    for b in approved_bookings:
        try:
            other_start = dt.strptime(
                f"{b['date']} {b['start_time']}", "%Y-%m-%d %H:%M:%S"
            )
        except (KeyError, ValueError, TypeError):
            continue
        # `or 30` rather than a get() default: the API can send an explicit null.
        other_end = other_start + timedelta(minutes=b.get("duration_minutes") or 30)
        if target_start < other_end and other_start < target_end:
            return True

    return False


def _get_candidates(
    rule,
    available_teachers,
    approved_bookings,
    target_date_str,
    duration_minutes=30,
):
    """
    Builds a priority-sorted candidate list for one rule:
      1. Intersect rule.teacher_ids with available teachers
      2. Filter out teachers who have reached the 60-min daily limit
    Returns candidates in the rule's preferred order.
    """
    available_teacher_ids = [str(t["id"]) for t in available_teachers]

    # Resolve preferred teacher names → IDs via cache. REMOVED names are
    # skipped: populate_teachers only refreshes the id of a name the API still
    # returns, so a REMOVED entry keeps its last known id — which the platform
    # may since have reassigned to a different tutor. Resolving it would book
    # someone the rule never named.
    teachers_cache = load_teacher_cache().get("teachers", {})
    preferred_ids = [
        str(teachers_cache[name]["id"])
        for name in rule.preferred_teachers
        if teachers_cache.get(name, {}).get("status") == "ACTIVE"
    ]

    # Preferred teachers intersection
    candidates = [
        next(t for t in available_teachers if str(t["id"]) == tid)
        for tid in preferred_ids
        if tid in available_teacher_ids
    ]

    candidate_info = ", ".join([f"{c['name']} ({c['id']})" for c in candidates])
    logger.info(f"Preferred: {candidate_info} (of {len(available_teachers)} available)")

    if not candidates:
        return []

    # Daily 60-min limit filter. Counts each booking's real length — a single
    # 60-minute class already exhausts the day's allowance for that teacher.
    final_candidates = []
    for cand in candidates:
        tid = str(cand["id"])
        booked_minutes = sum(
            b.get("duration_minutes", 30)
            for b in approved_bookings
            if str(b.get("staff_id")) == tid and b.get("date") == target_date_str
        )
        if booked_minutes + duration_minutes <= 60:
            final_candidates.append(cand)
        else:
            logger.info(f"Removed: {cand['name']} ({tid}) — 60m limit")

    return final_candidates


def _unbookable_teachers(rules_data, cache: dict, favorite_ids: set) -> set:
    """
    Names in the rules that belong to tutors who are not favourited.

    /booking/favorites/calendar is favourites-only, so these can never show
    availability however free they actually are. Without naming them the
    failure reads "No suitable teachers available", which is exactly what a
    genuinely full calendar reads like — and that ambiguity hid four dead rules
    for days.
    """
    if not favorite_ids:
        return set()  # lookup failed; do not accuse every teacher at once

    teachers = cache.get("teachers", {})
    return {
        name
        for rule in rules_data.rules
        if rule.enabled
        for name in rule.preferred_teachers
        if str(teachers.get(name, {}).get("id")) not in favorite_ids
    }


def _wait_for_window(booking_open_dt, now_local, local_tz, client, slot_key=""):
    """
    Blocks until booking_open_dt is reached, printing a live countdown.
    Raises SystemExit on KeyboardInterrupt.
    """
    wait_seconds = (booking_open_dt - now_local).total_seconds()
    if wait_seconds <= 0:
        return

    prefix = f"[{slot_key}] " if slot_key else ""
    logger.info(
        f"{prefix}Waiting... window opens {booking_open_dt.strftime('%H:%M:%S')}"
    )
    try:
        while wait_seconds > 0.1:
            hours, remainder = divmod(int(wait_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            sys.stdout.write(f"\r  T-minus:     {time_str} ")
            sys.stdout.flush()
            time.sleep(min(wait_seconds, 0.5))
            now_utc, _ = get_synced_now(client)
            now_local = now_utc.astimezone(local_tz)
            wait_seconds = (booking_open_dt - now_local).total_seconds()
    except KeyboardInterrupt:
        print("\n  Wait interrupted.")
        raise SystemExit(0)

    if wait_seconds > 0:
        time.sleep(wait_seconds + 0.1)
    logger.info(f"{prefix}Window open! Booking...")


def _refresh_schedule_token(
    client: BookingClient, credentials: dict, cache_file: Path
) -> bool:
    """Re-authenticates using this schedule's credentials. Returns True on success."""
    token = login(client, credentials, cache_file, use_cache=False)
    if token:
        client.set_token(token)
        return True
    return False


def _attempt_booking(
    client,
    candidates,
    target_slot_iso,
    force_soft,
    approved_bookings,
    target_date_str,
    target_start_time_str,
    credentials: dict,
    cache_file: Path,
    slot_key="",
    duration_minutes: int = 30,
) -> bool:
    """
    Iterates candidates and attempts to book the lesson.
    Returns True on first success. Mutates approved_bookings on success.
    """
    max_retries = 3

    for cand in candidates:
        tid = str(cand["id"])
        tname = cand["name"]
        prefix = f"[{slot_key}] " if slot_key else ""

        if force_soft:
            logger.info(f"{prefix}[DRY RUN] {tname} ({tid}) for {target_slot_iso}")
            return True

        logger.info(f"{prefix}Attempting: {tname} ({tid})")

        for attempt in range(max_retries):
            res = book_lesson(client, tid, target_slot_iso, duration_minutes)

            # Auth error — refresh token and retry once. Keyed on the real
            # status code: a bare "401" substring also matches booking ids and
            # session numbers, triggering a pointless re-auth mid-race.
            if res.get("status_code") in (401, 403):
                logger.info(f"{prefix}Re-auth: token rejected, refreshing...")
                _refresh_schedule_token(client, credentials, cache_file)
                res = book_lesson(client, tid, target_slot_iso, duration_minutes)

            if res.get("status") == "success":
                logger.info(f"{prefix}BOOKED: {tname} ({tid})")
                # Unreachable under --force-soft: that returns above, so dry
                # runs can never inflate the booked count.
                _counts["booked"] += 1
                _counts["bookings"].append(f"[{slot_key}] {tname}")
                approved_bookings.append(
                    {
                        "staff_id": tid,
                        "date": target_date_str,
                        "start_time": target_start_time_str,
                        "status": "approved",
                        "duration_minutes": duration_minutes,
                    }
                )
                return True

            error_msg = str(res.get("message", "unknown error"))
            logger.error(f"{prefix}Failed: {tname} ({tid}): {error_msg}")

            # The API returns INSUFFICIENT_AVAILABILITY both when a slot is
            # genuinely taken and when we arrive a fraction before it opens, so
            # retry briefly rather than moving straight to the next candidate.
            # (The old API signalled this with a Spanish "excede el límite"
            # message, which this backend never sends.)
            if "INSUFFICIENT_AVAILABILITY" in error_msg.upper():
                logger.info(f"{prefix}Retry {attempt + 1}/{max_retries}...")
                time.sleep(0.5)
                continue
            else:
                break  # Move to next candidate

    return False


# ---------------------------------------------------------------------------
# Single-schedule runner
# ---------------------------------------------------------------------------


def _run_schedule(
    schedule_name: str,
    rules_data: SchedulingRules,
    cache: dict,
    force: bool,
    force_soft: bool,
):
    """Runs the booking process for one schedule file end-to-end."""
    logger.set_schedule(schedule_name)
    actual_force = force or force_soft

    try:
        validate_rules_against_cache(rules_data, cache)
    except ValueError as e:
        _counts["failed"] += 1
        logger.error(f"Schedule error: {e}", schedule=schedule_name)
        send_push(f"[{schedule_name}] Schedule validation failed: {e}", priority=1)
        return

    local_tz = pytz.timezone(rules_data.timezone)
    now_local = dt.now(timezone.utc).astimezone(local_tz)

    # Phase 1: local clock only — no API calls
    due_rules, rule_lesson_times, rule_open_times, all_upcoming = _evaluate_rules(
        rules_data, now_local
    )
    _apply_force_flag(
        actual_force,
        force_soft,
        due_rules,
        all_upcoming,
        rule_lesson_times,
        rule_open_times,
    )

    if not due_rules:
        if all_upcoming:
            all_upcoming.sort(key=lambda x: x[0])
            next_open_dt, next_rule, next_lesson_dt = all_upcoming[0]
            time_until = next_open_dt - now_local
            total_seconds = int(time_until.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            countdown = f"{hours}h {minutes}m {seconds}s"
            slot_key = next_rule.id
            next_window = next_open_dt.strftime("%Y-%m-%d %H:%M")
            # Earliest window across all schedules — that's the one the panel
            # counts down to.
            if not _counts["next_window"] or next_window < _counts["next_window"]:
                _counts["next_window"] = next_window
            logger.info(
                f"Nothing to book - booking in {countdown} (for {slot_key})",
                schedule=schedule_name,
                next_window=next_window,
                slot_key=slot_key,
            )
        else:
            logger.info(
                "Nothing to book — no upcoming rules found.", schedule=schedule_name
            )
        return

    # Phase 2: something is due — authenticate and sync time
    logger.info(
        f"Booking due — processing {len(due_rules)} rule(s)", schedule=schedule_name
    )

    cache_file = CACHE_DIR / f".token_cache_{schedule_name}.json"
    credentials = {
        "email": rules_data.credentials.email,
        "password": rules_data.credentials.password,
    }

    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, credentials, cache_file)
        if not token:
            _counts["failed"] += 1
            logger.error("Auth: FAILED — check credentials", schedule=schedule_name)
            send_push(
                f"[{schedule_name}] Authentication failed — check credentials in YAML",
                priority=1,
            )
            return
        client.set_token(token)

        now_utc, drift = get_synced_now(client)
        now_local = now_utc.astimezone(local_tz)
        logger.info(
            f"Auth: {rules_data.credentials.email} ✓",
            schedule=schedule_name,
        )

        # Re-evaluate with synced time for accurate wait calculations
        due_rules, rule_lesson_times, rule_open_times, _ = _evaluate_rules(
            rules_data, now_local
        )
        _apply_force_flag(
            actual_force,
            force_soft,
            due_rules,
            all_upcoming,
            rule_lesson_times,
            rule_open_times,
        )

        bookings = get_bookings(client)
        approved_bookings = [
            b for b in bookings if b.get("status") == "approved" and not b.get("past")
        ]

        # Fetched once per run, before any window wait — everything slow and
        # window-independent belongs on this side of the countdown.
        tutor_map = get_tutors_map(client)
        unbookable = _unbookable_teachers(
            rules_data, cache, get_favorite_tutor_ids(client)
        )
        if unbookable:
            logger.warning(
                f"Not favourited on the platform, so unbookable: "
                f"{', '.join(sorted(unbookable))}",
                schedule=schedule_name,
            )

        for rule, slot_key in due_rules:
            target_slot_iso = rule_lesson_times[slot_key]
            booking_open_dt = rule_open_times[slot_key]
            target_dt = dt.fromisoformat(target_slot_iso)
            target_date_str = target_dt.strftime("%Y-%m-%d")
            target_start_time_str = target_dt.strftime("%H:%M:00")
            duration = rule.duration_minutes

            if _is_already_booked(
                approved_bookings, target_date_str, target_start_time_str, duration
            ):
                logger.info(
                    f"[{slot_key}] Already booked — skipping", schedule=schedule_name
                )
                continue

            _wait_for_window(
                booking_open_dt, now_local, local_tz, client, slot_key=slot_key
            )

            if is_token_expired(
                client.client.headers.get("Authorization", "").replace("Bearer ", ""),
                buffer_seconds=60,
            ):
                logger.info(
                    f"[{slot_key}] Re-auth: token near-expiry, refreshing...",
                    schedule=schedule_name,
                )
                if _refresh_schedule_token(client, credentials, cache_file):
                    logger.info(
                        f"[{slot_key}] Re-auth: success", schedule=schedule_name
                    )
                else:
                    logger.error(
                        f"[{slot_key}] Re-auth: FAILED — booking may fail",
                        schedule=schedule_name,
                    )
                    send_push(
                        f"[{schedule_name}] Token refresh failed before booking {slot_key} — booking may fail",
                        priority=1,
                    )

            # Availability is fetched *after* the window opens, not before.
            # The platform only lists slots inside the 7-day booking horizon, so
            # a calendar read at 23:59 for a lesson 8 days out comes back empty
            # and the run gives up without ever waiting. Costs the seconds the
            # calendar takes to page, which the all-day-open rules can afford.
            available_teachers = get_available_teachers(
                client, target_slot_iso, duration, tutor_map=tutor_map
            )

            candidates = _get_candidates(
                rule,
                available_teachers,
                approved_bookings,
                target_date_str,
                duration,
            )
            if not candidates:
                # Say which cause it was. "No teachers available" alone cannot
                # distinguish a full calendar from a tutor who is not favourited
                # and so was never in the calendar to begin with.
                dead = [n for n in rule.preferred_teachers if n in unbookable]
                reason = f" — not favourited: {', '.join(dead)}" if dead else ""
                # Logged at INFO for historical continuity, but this is a real
                # failure — the class does not get booked. The counter, not the
                # log level, is what the status panel trusts.
                _counts["failed"] += 1
                logger.info(
                    f"[{slot_key}] No suitable teachers available{reason} — skipping",
                    schedule=schedule_name,
                )
                send_push(
                    f"[{schedule_name}] No teachers available for {slot_key} on {target_date_str} at {target_start_time_str}{reason}",
                    priority=1,
                )
                continue

            success = _attempt_booking(
                client,
                candidates,
                target_slot_iso,
                force_soft,
                approved_bookings,
                target_date_str,
                target_start_time_str,
                credentials=credentials,
                cache_file=cache_file,
                slot_key=slot_key,
                duration_minutes=duration,
            )
            if not success:
                _counts["failed"] += 1
                logger.error(
                    f"[{slot_key}] FAILED: all teachers exhausted",
                    schedule=schedule_name,
                )
                send_push(
                    f"[{schedule_name}] Could not book {slot_key} on {target_date_str} at {target_start_time_str} — all teachers failed",
                    priority=1,
                )

        # Removed redundant 'Booking process completed' message

    finally:
        client.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _derive_outcome() -> str:
    """
    Classify the run from the tallies.

    Order matters: a run that booked one class and failed another reports
    'failed'. A green light that hides a failure is the only outcome that
    actually costs a lesson.
    """
    if _counts["failed"]:
        return "failed"
    if _counts["booked"]:
        return "booked"
    return "nothing_due"


def _record(
    run_id: str,
    started: dt,
    outcome: str | None,
    dry_run: bool,
    detail: str = "",
) -> None:
    """Write one run record. outcome=None means derive it from the tallies."""
    finished = dt.now()
    runstate.append(
        {
            "run_id": run_id,
            "started_at": started.strftime(runstate.TIME_FMT),
            "finished_at": finished.strftime(runstate.TIME_FMT),
            "duration_s": round((finished - started).total_seconds(), 1),
            "outcome": outcome or _derive_outcome(),
            "booked": _counts["booked"],
            "failed": _counts["failed"],
            "dry_run": dry_run,
            "next_window": _counts["next_window"],
            "detail": detail,
            "bookings": list(_counts["bookings"]),
        }
    )


def run_due_process(force: bool = False, force_soft: bool = False):
    run_id = dt.now().strftime("%y%m%d%H%M%S")
    logger.set_run_id(run_id)
    started = dt.now()
    _reset_counts()

    lock_f = acquire_lock()
    if not lock_f:
        # Deliberately no record: the run holding the lock may be mid-booking
        # inside _wait_for_window, and the loser must not overwrite the panel
        # with 'locked' while a real booking is in flight.
        logger.warning("Another instance is already running. Exiting.")
        return

    try:
        cache = load_teacher_cache()
        if not cache:
            logger.error("No teachers cache — run: python main.py populate-teachers")
            _record(run_id, started, "blocked", force_soft, detail="no teacher cache")
            return

        schedules = load_active_schedules()
        if not schedules:
            logger.warning("No active schedules found in scheduling_rules/")
            _record(
                run_id, started, "blocked", force_soft, detail="no active schedules"
            )
            return

        try:
            for schedule_name, rules_data in schedules:
                _run_schedule(schedule_name, rules_data, cache, force, force_soft)
        except Exception as e:
            # Re-raised below: the traceback still has to reach stderr and the
            # process still has to exit non-zero, so the healthcheck ping is
            # skipped and the dead-man's switch fires.
            detail = f"{type(e).__name__}: {e}"
            logger.error(f"Run crashed: {detail}")
            send_push(f"Class booker crashed: {detail}", priority=1)
            _record(run_id, started, "crashed", force_soft, detail=detail)
            raise

        _record(run_id, started, None, force_soft)

    finally:
        release_lock(lock_f)
