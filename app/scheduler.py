import os
import sys
import time
import fcntl
import pytz
from datetime import datetime as dt, timedelta, timezone
from typing import List, Dict, Any, Optional

from app.rules import load_scheduling_rules, BookingRule
from app.config import app_config
from app.client import BookingClient
from app.auth import login
from app.utils import get_server_time
from app.booking import get_bookings, book_lesson
from app.availability import get_available_teachers

LOCK_FILE = ".run_due.lock"

def acquire_lock():
    """
    Acquires a simple file lock.
    """
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
        except:
            pass

def get_synced_now(client: BookingClient) -> tuple[dt, float]:
    """
    Fetches server time and calculates current local time synced with server.
    Returns (synced_now_utc, drift_seconds).
    """
    local_before = dt.now(timezone.utc)
    server_res = get_server_time(client)
    drift = 0.0
    if "datetime" in server_res:
        try:
            # Expected format: "2026-04-01 21:03:00"
            server_dt = dt.fromisoformat(server_res["datetime"].replace(' ', 'T')).replace(tzinfo=timezone.utc)
            drift = (server_dt - local_before).total_seconds()
            return server_dt, drift
        except:
            pass
    return dt.now(timezone.utc), 0.0

def run_due_process(verbose: bool = False, force: bool = False, force_soft: bool = False):
    lock_f = acquire_lock()
    if not lock_f:
        print("Another instance is already running. Exiting.")
        return

    # If force_soft is on, we'll implicitly act like force is on too for the next rule
    actual_force = force or force_soft

    client = BookingClient(base_url=app_config.base_url)
    try:
        rules_data = load_scheduling_rules()
        local_tz = pytz.timezone(rules_data.timezone)
        
        # 1. Sync server time and Auth check
        print("Checking authentication...")
        token = login(client)
        if not token:
            print("Authentication: FAILURE. Check your credentials.")
            return
        print("Authentication: SUCCESS.")
        client.set_token(token)
        
        print("Syncing with server time...")
        now_utc, drift = get_synced_now(client)
        now_local = now_utc.astimezone(local_tz)
        
        status_icon = "✓" if abs(drift) < 5 else "✗"
        print(f"Current server time: {now_local.strftime('%Y-%m-%d %H:%M:%S')} ({rules_data.timezone})")
        print(f"Server drift: {drift:+.3f}s {status_icon}")
        
        due_rules = []
        rule_lesson_times = {} # rule_id -> lesson_dt_iso
        rule_open_times = {}   # rule_id -> booking_open_dt
        
        all_upcoming_rules = []

        for rule in rules_data.rules:
            if not rule.enabled:
                continue
                
            # Calculate next occurrence of this rule
            # The cron runs every 30 mins, we check if booking window is in next few minutes
            # Booking window = lesson_start - 7 days - 30 minutes
            
            # We check for lessons in the next 14 days to find the very next one even if it's far
            for days_ahead in range(15):
                target_date = (now_local + timedelta(days=days_ahead)).date()
                weekday_str = target_date.strftime("%a").lower()
                if weekday_str not in rule.weekdays:
                    continue
                
                lesson_time = dt.strptime(rule.start_time, "%H:%M").time()
                lesson_dt = local_tz.localize(dt.combine(target_date, lesson_time))
                
                booking_open_dt = lesson_dt - timedelta(days=rules_data.booking.open_offset_days, 
                                                        minutes=rules_data.booking.open_offset_minutes)
                
                # Filter out those that already passed
                if booking_open_dt < now_local:
                    continue

                all_upcoming_rules.append((booking_open_dt, rule, lesson_dt))
                
                # If booking_open_dt is within precheck_lead_seconds from now
                diff = (booking_open_dt - now_local).total_seconds()
                
                if 0 <= diff <= rules_data.booking.precheck_lead_seconds:
                    due_rules.append(rule)
                    rule_lesson_times[rule.id] = lesson_dt.isoformat()
                    rule_open_times[rule.id] = booking_open_dt
                
                # Found the next occurrence for this specific rule, move to next rule
                break

        if actual_force and not due_rules and all_upcoming_rules:
            all_upcoming_rules.sort(key=lambda x: x[0])
            next_open_dt, next_rule, next_lesson_dt = all_upcoming_rules[0]
            if force_soft:
                print(f"Soft force flag active. Simulating next rule: {next_rule.id}")
            else:
                print(f"Force flag active. Forcing next rule: {next_rule.id}")
            due_rules.append(next_rule)
            rule_lesson_times[next_rule.id] = next_lesson_dt.isoformat()
            rule_open_times[next_rule.id] = next_open_dt

        if verbose and all_upcoming_rules:
            all_upcoming_rules.sort(key=lambda x: x[0])
            next_open_dt, next_rule, next_lesson_dt = all_upcoming_rules[0]
            time_until = next_open_dt - now_local
            
            # Format time until
            hours, remainder = divmod(int(time_until.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"

            print(f"--- Next Rule Info ---")
            print(f"Next rule: {next_rule.id}")
            print(f"Lesson time: {next_lesson_dt.strftime('%Y-%m-%d %H:%M')} ({rules_data.timezone})")
            print(f"Booking opens at: {next_open_dt.strftime('%Y-%m-%d %H:%M')} ({rules_data.timezone})")
            print(f"Time until booking opens: {time_str}")
            print(f"-------------------------------")

        if not due_rules:
            if not verbose:
                print("No rules are due for booking.")
            return

        print(f"Due rules found: {[r.id for r in due_rules]}")
        
        # 2. Authenticate and fetch bookings once
        print("Fetching current bookings...")
        bookings = get_bookings(client)
        approved_bookings = [b for b in bookings if b.get("status") == "approved"]
        print(f"Found {len(approved_bookings)} active bookings.")
        
        for rule in due_rules:
            target_slot_iso = rule_lesson_times[rule.id]
            booking_open_dt = rule_open_times[rule.id]
            target_dt = dt.fromisoformat(target_slot_iso)
            target_date_str = target_dt.strftime("%Y-%m-%d")
            target_start_time_str = target_dt.strftime("%H:%M:00")
            
            print(f"\n--- Processing Rule: {rule.id} ---")
            print(f"Target: {target_date_str} {target_start_time_str} ({rules_data.timezone})")

            # Check if already booked
            already_booked = False
            for b in approved_bookings:
                if b.get("date") == target_date_str and b.get("start_time") == target_start_time_str:
                    already_booked = True
                    break
            
            if already_booked:
                print(f"Status: Already booked. Skipping.")
                continue

            # Fetch availability
            print("Checking teacher availability...")
            available_teachers = get_available_teachers(client, target_slot_iso)
            available_teacher_ids = [str(t["id"]) for t in available_teachers]
            print(f"Teachers available at this slot: {available_teacher_ids}")
            
            # Build candidate list from rule teacher_ids (intersect with available)
            candidates = [str(tid) for tid in rule.teacher_ids if str(tid) in available_teacher_ids]
            print(f"Preferred teachers available: {candidates}")
            
            if not candidates:
                print(f"Status: No preferred teachers available. Skipping.")
                continue
            
            # Filter candidates by 60min daily limit
            final_candidates = []
            for tid in candidates:
                booked_minutes = 0
                for b in approved_bookings:
                    if str(b.get("staff_id")) == tid and b.get("date") == target_date_str:
                        # Assume each booking is 30 mins based on spec
                        booked_minutes += 30
                if booked_minutes < 60:
                    final_candidates.append(tid)
                else:
                    print(f"Teacher {tid}: Limit reached (60m already booked on {target_date_str}).")

            if not final_candidates:
                print(f"Status: All preferred teachers reached daily limit. Skipping.")
                continue

            # Priority for adjacent slot
            prev_slot_start = (target_dt - timedelta(minutes=30)).strftime("%H:%M:00")
            prev_teacher = None
            for b in approved_bookings:
                if b.get("date") == target_date_str and b.get("start_time") == prev_slot_start:
                    prev_teacher = str(b.get("staff_id"))
                    break
            
            if prev_teacher and prev_teacher in final_candidates:
                # Move to front
                final_candidates.remove(prev_teacher)
                final_candidates.insert(0, prev_teacher)
                print(f"Teacher {prev_teacher}: Prioritized (taught previous adjacent slot).")

            print(f"Final candidate order: {final_candidates}")

            # 3. Wait until exact booking open time
            # Re-sync time just before waiting to be as accurate as possible
            now_utc, _ = get_synced_now(client)
            now_local = now_utc.astimezone(local_tz)
            
            wait_seconds = (booking_open_dt - now_local).total_seconds()
            if wait_seconds > 0:
                print(f"Waiting {wait_seconds:.2f}s for booking window to open at {booking_open_dt.strftime('%H:%M:%S')}...")
                # Simple countdown for visibility
                while wait_seconds > 0.5:
                    time.sleep(min(wait_seconds, 1.0))
                    now_utc, _ = get_synced_now(client)
                    now_local = now_utc.astimezone(local_tz)
                    wait_seconds = (booking_open_dt - now_local).total_seconds()
                    if wait_seconds > 0:
                        print(f"  T-minus: {wait_seconds:.1f}s...", end="\r")
                
                # Small final sleep to ensure we are definitely over the mark
                if wait_seconds > 0:
                    time.sleep(wait_seconds + 0.1)
                print("\nWindow OPEN! Attempting booking...")

            # Attempt booking
            success = False
            for tid in final_candidates:
                if force_soft:
                    print(f"[DRY RUN] Would attempt Teacher {tid} booking for {target_slot_iso}")
                    success = True # Consider it a "success" for the dry run flow
                    continue

                print(f"Attempting Teacher {tid}...")
                res = book_lesson(client, tid, target_slot_iso)
                if res.get("status") == "success":
                    print(f"SUCCESS! Booked Teacher {tid}.")
                    success = True
                    # Update local bookings list so subsequent rules know about this booking
                    approved_bookings.append({
                        "staff_id": tid,
                        "date": target_date_str,
                        "start_time": target_start_time_str,
                        "status": "approved"
                    })
                    break
                else:
                    print(f"Failed for Teacher {tid}: {res.get('message')}")
            
            if not success:
                print(f"All booking attempts failed for rule {rule.id}.")

    finally:
        client.close()
        release_lock(lock_f)
