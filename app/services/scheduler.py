import os
import sys
import time
import fcntl
import pytz
from datetime import datetime as dt, timedelta, timezone

from pathlib import Path

from app.api.auth import login, is_token_expired
from app.notifications import send_push
from app.api.availability import get_available_teachers
from app.api.booking import get_bookings, book_lesson
from app.client import BookingClient
from app.config import app_config
from app.rules import (
    load_active_schedules,
    SchedulingRules,
    BOOKING_OPEN_OFFSET_DAYS,
    BOOKING_OPEN_OFFSET_MINUTES,
    BOOKING_PRECHECK_LEAD_SECONDS,
)
from app.teachers import load_teacher_cache, validate_rules_against_cache
from app.utils import get_server_time
from app import logger

CACHE_DIR = Path(__file__).parent.parent.parent / "cache"

LOCK_FILE = ".run_due.lock"

BOOKING_DELAY_MIN_SECONDS = 15
BOOKING_DELAY_MAX_SECONDS = 30


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
    Iterates all enabled rules over the next 15 days, expanding each rule's
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
        for days_ahead in range(15):
            target_date = (now_local + timedelta(days=days_ahead)).date()
            weekday_str = target_date.strftime("%a").lower()
            if weekday_str != rule.weekday:
                continue

            for slot_index, slot_time_str in enumerate(rule.slot_times()):
                lesson_time = dt.strptime(slot_time_str, "%H:%M").time()
                lesson_dt = local_tz.localize(dt.combine(target_date, lesson_time))

                booking_open_dt = lesson_dt - timedelta(
                    days=BOOKING_OPEN_OFFSET_DAYS,
                    minutes=BOOKING_OPEN_OFFSET_MINUTES,
                )
                booking_open_dt = local_tz.localize(
                    booking_open_dt.replace(tzinfo=None)
                )

                if booking_open_dt < now_local:
                    continue

                found_occurrence = True
                slot_key = (
                    f"{rule.id}_slot{slot_index + 1}" if rule.slots > 1 else rule.id
                )
                all_upcoming_rules.append((booking_open_dt, rule, lesson_dt))

                diff = (booking_open_dt - now_local).total_seconds()
                if 0 <= diff <= BOOKING_PRECHECK_LEAD_SECONDS:
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


def _print_verbose_upcoming(all_upcoming_rules, now_local, rules_data):
    """Prints the UPCOMING RULE INFO block when --verbose is active."""
    all_upcoming_rules.sort(key=lambda x: x[0])
    next_open_dt, next_rule, next_lesson_dt = all_upcoming_rules[0]
    time_until = next_open_dt - now_local

    hours, remainder = divmod(int(time_until.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    time_str = (
        f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
    )

    print("--- UPCOMING RULE INFO ---")
    print(f"Next rule: {next_rule.id}")
    print(
        f"Lesson time: {next_lesson_dt.strftime('%Y-%m-%d %H:%M')} ({rules_data.timezone})"
    )
    print(
        f"Booking opens at: {next_open_dt.strftime('%Y-%m-%d %H:%M')} ({rules_data.timezone})"
    )
    print(f"Time until booking opens: {time_str}")
    print("--------------------------")


def _is_already_booked(approved_bookings, date_str, start_time_str) -> bool:
    """Returns True if an approved booking already exists for the given date and time."""
    return any(
        b.get("date") == date_str and b.get("start_time") == start_time_str
        for b in approved_bookings
    )


def _get_candidates(
    rule, available_teachers, approved_bookings, target_date_str, target_dt
):
    """
    Builds a priority-sorted candidate list for one rule:
      1. Intersect rule.teacher_ids with available teachers
      2. Filter out teachers who have reached the 60-min daily limit
      3. Promote the teacher from the adjacent preceding slot to the front
    Returns candidates where the list is prioritized by preferred order then adjacency.
    """
    available_teacher_ids = [str(t["id"]) for t in available_teachers]

    # Resolve preferred teacher names → IDs via cache
    teachers_cache = load_teacher_cache().get("teachers", {})
    preferred_ids = [
        str(teachers_cache[name]["id"])
        for name in rule.preferred_teachers
        if name in teachers_cache
    ]

    # Preferred teachers intersection
    candidates = [
        next(t for t in available_teachers if str(t["id"]) == tid)
        for tid in preferred_ids
        if tid in available_teacher_ids
    ]

    candidate_info = ", ".join([f"{c['name']} ({c['id']})" for c in candidates])
    logger.info(f"Preferred: {candidate_info}")

    if not candidates:
        return []

    # Daily 60-min limit filter
    final_candidates = []
    for cand in candidates:
        tid = str(cand["id"])
        booked_minutes = sum(
            30
            for b in approved_bookings
            if str(b.get("staff_id")) == tid and b.get("date") == target_date_str
        )
        if booked_minutes < 60:
            final_candidates.append(cand)
        else:
            logger.info(f"Removed: {cand['name']} ({tid}) — 60m limit")

    if not final_candidates:
        return []

    # Adjacency priority — promote teacher who taught the preceding 30-min slot
    prev_slot_start = (target_dt - timedelta(minutes=30)).strftime("%H:%M:00")
    prev_teacher = next(
        (
            str(b.get("staff_id"))
            for b in approved_bookings
            if b.get("date") == target_date_str
            and b.get("start_time") == prev_slot_start
        ),
        None,
    )
    if prev_teacher:
        prev_cand = next(
            (c for c in final_candidates if str(c["id"]) == prev_teacher), None
        )
        if prev_cand:
            final_candidates.remove(prev_cand)
            final_candidates.insert(0, prev_cand)
            logger.info(
                f"Prioritised: {prev_cand['name']} ({prev_teacher}) (adjacent slot)"
            )

    return final_candidates


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
            res = book_lesson(client, tid, target_slot_iso)

            # Auth error — refresh token and retry once
            if res.get("status") == "error" and (
                "Unauthorized" in str(res.get("message"))
                or "401" in str(res.get("message"))
            ):
                logger.info(f"{prefix}Re-auth: token rejected, refreshing...")
                _refresh_schedule_token(client, credentials, cache_file)
                res = book_lesson(client, tid, target_slot_iso)

            if res.get("status") == "success":
                logger.info(f"{prefix}BOOKED: {tname} ({tid})")
                approved_bookings.append(
                    {
                        "staff_id": tid,
                        "date": target_date_str,
                        "start_time": target_start_time_str,
                        "status": "approved",
                    }
                )
                return True

            error_msg = str(res.get("message", "unknown error"))
            logger.error(f"{prefix}Failed: {tname} ({tid}): {error_msg}")

            # If it's the specific Spanish error that happens when we're too early,
            # wait a tiny bit and retry. Otherwise, it's a "real" error, so move
            # to the next candidate.
            if (
                "excede el límite" in error_msg.lower()
                or "excede el agendamiento límite" in error_msg.lower()
            ):
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
            logger.info(
                f"Nothing to book - booking in {countdown} (for {slot_key})",
                schedule=schedule_name,
                next_window=next_open_dt.strftime("%Y-%m-%d %H:%M"),
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

        for rule, slot_key in due_rules:
            target_slot_iso = rule_lesson_times[slot_key]
            booking_open_dt = rule_open_times[slot_key]
            target_dt = dt.fromisoformat(target_slot_iso)
            target_date_str = target_dt.strftime("%Y-%m-%d")
            target_start_time_str = target_dt.strftime("%H:%M:00")

            if _is_already_booked(
                approved_bookings, target_date_str, target_start_time_str
            ):
                logger.info(
                    f"[{slot_key}] Already booked — skipping", schedule=schedule_name
                )
                continue

            available_teachers = get_available_teachers(client, target_slot_iso)

            candidates = _get_candidates(
                rule, available_teachers, approved_bookings, target_date_str, target_dt
            )
            if not candidates:
                logger.info(
                    f"[{slot_key}] No suitable teachers available — skipping",
                    schedule=schedule_name,
                )
                send_push(
                    f"[{schedule_name}] No teachers available for {slot_key} on {target_date_str} at {target_start_time_str}",
                    priority=1,
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
            )
            if not success:
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


def run_due_process(force: bool = False, force_soft: bool = False):
    run_id = dt.now().strftime("%y%m%d%H%M%S")
    logger.set_run_id(run_id)
    lock_f = acquire_lock()
    if not lock_f:
        logger.warning("Another instance is already running. Exiting.")
        return

    try:
        cache = load_teacher_cache()
        if not cache:
            logger.error("No teachers cache — run: python main.py populate-teachers")
            return

        schedules = load_active_schedules()
        if not schedules:
            logger.warning("No active schedules found in scheduling_rules/")
            return

        for schedule_name, rules_data in schedules:
            _run_schedule(schedule_name, rules_data, cache, force, force_soft)

    finally:
        release_lock(lock_f)
