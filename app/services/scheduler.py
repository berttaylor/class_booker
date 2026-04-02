import os
import sys
import time
import fcntl
import pytz
from datetime import datetime as dt, timedelta, timezone

from app.api.auth import login, is_token_expired
from app.notifications import send_push
from app.api.availability import get_available_teachers
from app.api.booking import get_bookings, book_lesson
from app.client import BookingClient
from app.config import app_config, settings
from app.rules import load_scheduling_rules
from app.services.session import ensure_fresh_token
from app.utils import get_server_time

LOCK_FILE = ".run_due.lock"


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
            server_dt_raw = dt.fromisoformat(server_res["datetime"].replace(' ', 'T')).replace(tzinfo=timezone.utc)
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
    Iterates all enabled rules over the next 15 days.
    Returns (due_rules, rule_lesson_times, rule_open_times, all_upcoming_rules).
    """
    local_tz = pytz.timezone(rules_data.timezone)
    due_rules = []
    rule_lesson_times = {}
    rule_open_times = {}
    all_upcoming_rules = []

    for rule in rules_data.rules:
        if not rule.enabled:
            continue

        for days_ahead in range(15):
            target_date = (now_local + timedelta(days=days_ahead)).date()
            weekday_str = target_date.strftime("%a").lower()
            if weekday_str not in rule.weekdays:
                continue

            lesson_time = dt.strptime(rule.start_time, "%H:%M").time()
            lesson_dt = local_tz.localize(dt.combine(target_date, lesson_time))

            booking_open_dt = lesson_dt - timedelta(
                days=rules_data.booking.open_offset_days,
                minutes=rules_data.booking.open_offset_minutes,
            )
            booking_open_dt = local_tz.localize(booking_open_dt.replace(tzinfo=None))

            if booking_open_dt < now_local:
                continue

            all_upcoming_rules.append((booking_open_dt, rule, lesson_dt))

            diff = (booking_open_dt - now_local).total_seconds()
            if 0 <= diff <= rules_data.booking.precheck_lead_seconds:
                due_rules.append(rule)
                rule_lesson_times[rule.id] = lesson_dt.isoformat()
                rule_open_times[rule.id] = booking_open_dt

            break  # Found next occurrence for this rule

    return due_rules, rule_lesson_times, rule_open_times, all_upcoming_rules


def _apply_force_flag(actual_force, force_soft, due_rules, all_upcoming_rules, rule_lesson_times, rule_open_times):
    """
    When --force or --force-soft is active and no rules are due, injects the
    next upcoming rule into due_rules so it runs immediately.
    Mutates due_rules, rule_lesson_times, rule_open_times in place.
    """
    if actual_force and not due_rules and all_upcoming_rules:
        all_upcoming_rules.sort(key=lambda x: x[0])
        next_open_dt, next_rule, next_lesson_dt = all_upcoming_rules[0]
        label = "Soft force" if force_soft else "Force"
        print(f"{label} flag active. Forcing next rule: {next_rule.id}")
        due_rules.append(next_rule)
        rule_lesson_times[next_rule.id] = next_lesson_dt.isoformat()
        rule_open_times[next_rule.id] = next_open_dt


def _print_verbose_upcoming(all_upcoming_rules, now_local, rules_data):
    """Prints the UPCOMING RULE INFO block when --verbose is active."""
    all_upcoming_rules.sort(key=lambda x: x[0])
    next_open_dt, next_rule, next_lesson_dt = all_upcoming_rules[0]
    time_until = next_open_dt - now_local

    hours, remainder = divmod(int(time_until.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    time_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"

    print("--- UPCOMING RULE INFO ---")
    print(f"Next rule: {next_rule.id}")
    print(f"Lesson time: {next_lesson_dt.strftime('%Y-%m-%d %H:%M')} ({rules_data.timezone})")
    print(f"Booking opens at: {next_open_dt.strftime('%Y-%m-%d %H:%M')} ({rules_data.timezone})")
    print(f"Time until booking opens: {time_str}")
    print("--------------------------")


def _is_already_booked(approved_bookings, date_str, start_time_str) -> bool:
    """Returns True if an approved booking already exists for the given date and time."""
    return any(
        b.get("date") == date_str and b.get("start_time") == start_time_str
        for b in approved_bookings
    )


def _get_candidates(rule, available_teachers, approved_bookings, target_date_str, target_dt):
    """
    Builds a priority-sorted candidate list for one rule:
      1. Intersect rule.teacher_ids with available teachers
      2. Fall back to all available teachers if allow_fallbacks is set
      3. Filter out teachers who have reached the 60-min daily limit
      4. Promote the teacher from the adjacent preceding slot to the front
    Returns (candidates, used_fallback) where used_fallback is True if no
    preferred teachers were available and fallback teachers are being used.
    """
    available_teacher_ids = [str(t["id"]) for t in available_teachers]

    # Preferred teachers intersection
    candidates = [
        next(t for t in available_teachers if str(t["id"]) == str(tid))
        for tid in rule.teacher_ids
        if str(tid) in available_teacher_ids
    ]

    candidate_info = ", ".join([f"{c['name']} ({c['id']})" for c in candidates])
    print(f"Preferred teachers available: {candidate_info}")

    used_fallback = False
    if not candidates and rule.allow_fallbacks:
        print("No preferred teachers available. Fallback is ENABLED. Considering all available teachers...")
        candidates = available_teachers
        used_fallback = True

    if not candidates:
        return [], False

    # Daily 60-min limit filter
    print()
    final_candidates = []
    for cand in candidates:
        tid = str(cand["id"])
        booked_minutes = sum(
            30 for b in approved_bookings
            if str(b.get("staff_id")) == tid and b.get("date") == target_date_str
        )
        if booked_minutes < 60:
            final_candidates.append(cand)
        else:
            print(f"Removed {cand['name']} ({tid}): 60m limit reached on {target_date_str}.")

    if not final_candidates:
        return []

    # Adjacency priority — promote teacher who taught the preceding 30-min slot
    prev_slot_start = (target_dt - timedelta(minutes=30)).strftime("%H:%M:00")
    prev_teacher = next(
        (str(b.get("staff_id")) for b in approved_bookings
         if b.get("date") == target_date_str and b.get("start_time") == prev_slot_start),
        None
    )
    if prev_teacher:
        prev_cand = next((c for c in final_candidates if str(c["id"]) == prev_teacher), None)
        if prev_cand:
            final_candidates.remove(prev_cand)
            final_candidates.insert(0, prev_cand)
            print(f"Prioritized: {prev_cand['name']} ({prev_teacher}) (taught previous adjacent slot).")

    return final_candidates, used_fallback


def _wait_for_window(booking_open_dt, now_local, local_tz, client):
    """
    Blocks until booking_open_dt is reached, printing a live countdown.
    Raises SystemExit on KeyboardInterrupt.
    """
    wait_seconds = (booking_open_dt - now_local).total_seconds()
    if wait_seconds <= 0:
        return

    print(f"Waiting for booking window to open at {booking_open_dt.strftime('%H:%M:%S')}...")
    try:
        while wait_seconds > 0.1:
            hours, remainder = divmod(int(wait_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            sys.stdout.write(f"\r  T-minus: {time_str}... ")
            sys.stdout.flush()
            time.sleep(min(wait_seconds, 0.5))
            now_utc, _ = get_synced_now(client)
            now_local = now_utc.astimezone(local_tz)
            wait_seconds = (booking_open_dt - now_local).total_seconds()
    except KeyboardInterrupt:
        print("\nWait interrupted by user.")
        raise SystemExit(0)

    if wait_seconds > 0:
        time.sleep(wait_seconds + 0.1)
    print("\nWindow OPEN! Attempting booking...")


def _attempt_booking(client, candidates, target_slot_iso, force_soft, approved_bookings, target_date_str, target_start_time_str, used_fallback=False) -> bool:
    """
    Iterates candidates and attempts to book the lesson.
    Returns True on first success. Mutates approved_bookings on success.
    """
    max_retries = 3

    for cand in candidates:
        tid = str(cand["id"])
        tname = cand["name"]

        if force_soft:
            print(f"[DRY RUN] Would attempt Teacher {tname} ({tid}) for {target_slot_iso}")
            return True

        print(f"Attempting Teacher {tname} ({tid})...")

        for attempt in range(max_retries):
            res = book_lesson(client, tid, target_slot_iso)

            # Auth error — refresh token and retry once
            if res.get("status") == "error" and (
                "Unauthorized" in str(res.get("message")) or "401" in str(res.get("message"))
            ):
                print(f"Token rejected for Teacher {tname} ({tid}). Retrying with fresh login...")
                ensure_fresh_token(client)
                res = book_lesson(client, tid, target_slot_iso)

            if res.get("status") == "success":
                print(f"SUCCESS! Booked Teacher {tname} ({tid}).")
                msg = f"Booked {tname} for {target_date_str} at {target_start_time_str}"
                if used_fallback:
                    msg += " (fallback — preferred teachers unavailable)"
                send_push(msg, priority=-1)
                approved_bookings.append({
                    "staff_id": tid,
                    "date": target_date_str,
                    "start_time": target_start_time_str,
                    "status": "approved",
                })
                return True

            error_msg = str(res.get("message", ""))
            # Spanish API error: booking window not yet open
            if "excede el" in error_msg and "agendamiento" in error_msg:
                if attempt < max_retries - 1:
                    print(f"Booking window not yet open. Retrying in 2s... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(2)
                    continue

            print(f"Failed for Teacher {tname} ({tid}): {error_msg}")
            break  # Move to next candidate

    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_due_process(verbose: bool = False, force: bool = False, force_soft: bool = False):
    lock_f = acquire_lock()
    if not lock_f:
        print("Another instance is already running. Exiting.")
        return

    actual_force = force or force_soft
    client = BookingClient(base_url=app_config.base_url)
    try:
        rules_data = load_scheduling_rules()
        local_tz = pytz.timezone(rules_data.timezone)

        print("-" * 50)
        print("INITIALIZING: Spanish Class Booking Automation")
        print("-" * 50)

        print(f"Checking authentication ({settings.login_email})...")
        token = login(client)
        if not token:
            print("Authentication: FAILURE. Check your credentials.")
            send_push("Authentication failed — check credentials in .env", priority=1)
            return
        print("Authentication: SUCCESS.")
        client.set_token(token)

        print("Syncing with server time...")
        now_utc, drift = get_synced_now(client)
        now_local = now_utc.astimezone(local_tz)
        status_icon = "✓" if abs(drift) < 5 else "✗"
        print(f"Current server time: {now_local.strftime('%Y-%m-%d %H:%M:%S')} ({rules_data.timezone})")
        print(f"Server drift: {drift:+.3f}s {status_icon}")
        print("-" * 50)

        due_rules, rule_lesson_times, rule_open_times, all_upcoming = _evaluate_rules(rules_data, now_local)
        _apply_force_flag(actual_force, force_soft, due_rules, all_upcoming, rule_lesson_times, rule_open_times)

        if verbose and all_upcoming:
            _print_verbose_upcoming(all_upcoming, now_local, rules_data)

        if not due_rules:
            if not verbose:
                print("Status: No rules are due for booking at this time.")
            return

        print(f"Rules to process: {', '.join([r.id for r in due_rules])}")
        print("Fetching current bookings...")
        bookings = get_bookings(client)
        approved_bookings = [
            b for b in bookings
            if b.get("status") == "approved" and not b.get("past")
        ]
        print(f"Found {len(approved_bookings)} active future bookings.")
        print("-" * 50)

        for rule in due_rules:
            target_slot_iso = rule_lesson_times[rule.id]
            booking_open_dt = rule_open_times[rule.id]
            target_dt = dt.fromisoformat(target_slot_iso)
            target_date_str = target_dt.strftime("%Y-%m-%d")
            target_start_time_str = target_dt.strftime("%H:%M:00")

            print("\n==================================================")
            print(f"--- Processing Rule: {rule.id} ---")
            print(f"Target: {target_date_str} {target_start_time_str} ({rules_data.timezone})")
            print("==================================================")

            if _is_already_booked(approved_bookings, target_date_str, target_start_time_str):
                print("Status: Already booked. Skipping.")
                continue

            print("Checking teacher availability...")
            available_teachers = get_available_teachers(client, target_slot_iso)
            available_info = ", ".join([f"{t['name']} ({t['id']})" for t in available_teachers])
            print(f"Teachers available at this slot: {available_info}")

            candidates, used_fallback = _get_candidates(rule, available_teachers, approved_bookings, target_date_str, target_dt)
            if not candidates:
                print("Status: No suitable teachers available. Skipping.")
                send_push(f"No teachers available for {rule.id} on {target_date_str} at {target_start_time_str}", priority=1)
                continue

            candidate_order = ", ".join([f"{c['name']} ({c['id']})" for c in candidates])
            print(f"Final candidate order: {candidate_order}")

            _wait_for_window(booking_open_dt, now_local, local_tz, client)

            if is_token_expired(
                client.client.headers.get("Authorization", "").replace("Bearer ", ""),
                buffer_seconds=60,
            ):
                print("Token expired or near-expiry. Re-authenticating...")
                if ensure_fresh_token(client):
                    print("Re-authentication successful.")
                else:
                    print("Re-authentication FAILED. Booking might fail.")
                    send_push(f"Token refresh failed before booking {rule.id} — booking may fail", priority=1)

            success = _attempt_booking(
                client, candidates, target_slot_iso, force_soft,
                approved_bookings, target_date_str, target_start_time_str,
                used_fallback=used_fallback,
            )
            if not success:
                print(f"All booking attempts failed for rule {rule.id}.")
                send_push(f"Could not book {rule.id} on {target_date_str} at {target_start_time_str} — all teachers failed", priority=1)

        print("\nBooking process completed.")

    finally:
        client.close()
        release_lock(lock_f)
