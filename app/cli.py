import typer
from app.client import BookingClient
from app.config import app_config
from app.auth import login, get_cached_token, is_token_expired
from app.availability import get_available_teachers, get_teacher_slots, format_calendar, get_tutors_map
from app.booking import book_lesson, get_bookings, cancel_booking
from app.utils import get_server_time
from app.scheduler import run_due_process

from typing import Annotated

app = typer.Typer()

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    datetime: Annotated[str, typer.Option("--datetime", help="Target lesson datetime in ISO format")] = None,
    teacher_id: Annotated[str, typer.Option("--teacher-id", help="Teacher ID for calendar view")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Disable token caching and force login")] = False,
):
    """
    Spanish Class Booking Automation CLI.
    """
    ctx.ensure_object(dict)
    ctx.obj["use_cache"] = not no_cache
    
    if ctx.invoked_subcommand is None:
        if datetime:
            run_check(datetime, use_cache=not no_cache)
        elif teacher_id:
            show_teacher_calendar(teacher_id, use_cache=not no_cache)
        else:
            typer.echo(ctx.get_help())

def show_teacher_calendar(teacher_id: str, use_cache: bool = True):
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, use_cache=use_cache)
        if token:
            client.set_token(token)
            tutor_map = get_tutors_map(client)
            tutor_data = tutor_map.get(str(teacher_id), {})
            teacher_name = tutor_data.get("name", f"Teacher {teacher_id}")
            typer.echo(f"Fetching calendar for: {teacher_name} (ID: {teacher_id})")
            
            slots = get_teacher_slots(client, teacher_id)
            if (not slots or slots == "No slots found for this teacher.") and use_cache:
                # If cached token failed (maybe expired but exp claim was missing or wrong)
                # Try once more without cache
                token = login(client, use_cache=False)
                if token:
                    client.set_token(token)
                    slots = get_teacher_slots(client, teacher_id)

            calendar_view = format_calendar(slots)
            typer.echo(calendar_view)
        else:
            typer.echo("Authentication: Failure")
    finally:
        client.close()

def run_check(datetime: str, use_cache: bool = True):
    client = BookingClient(base_url=app_config.base_url)
    try:
        typer.echo(f"Target lesson datetime: {datetime}")
        token = login(client, use_cache=use_cache)
        if token:
            is_cached = use_cache and get_cached_token() == token
            typer.echo("Authentication: Success (using cached token)" if is_cached else "Authentication: Success")
            client.set_token(token)
        else:
            typer.echo("Authentication: Failure")
            return
            
        teachers = get_available_teachers(client, datetime)
        
        # If no teachers found and we used cache, it might be an auth issue that wasn't caught
        if not teachers and use_cache:
             # Basic retry logic if it looks like token might be stale
             token = login(client, use_cache=False)
             if token:
                 client.set_token(token)
                 teachers = get_available_teachers(client, datetime)

        if teachers:
            typer.echo(f"\nFound {len(teachers)} available teachers:")
            for t in teachers:
                typer.echo(f" ✓ {t.get('name'):<30} (ID: {t.get('id'):<4}) at {t.get('start_time_local')} ({app_config.timezone})")
        else:
            typer.echo("No available teachers found for this slot.")
        typer.echo("\nRaw response summary: Fetching completed.")
    finally:
        client.close()

@app.command(name="check-availability")
def check_availability(
    datetime: Annotated[str, typer.Option("--datetime", help="Target lesson datetime in ISO format")]
):
    """
    Check for available Spanish teachers for a target lesson datetime.
    Example: --datetime "2026-04-08T13:30:00+02:00"
    """
    run_check(datetime)

@app.command(name="teacher-calendar")
def teacher_calendar(
    teacher_id: Annotated[str, typer.Option("--teacher-id", help="Teacher ID for calendar view")]
):
    """
    Display a teacher's availability as a calendar grid.
    """
    show_teacher_calendar(teacher_id)

@app.command(name="book-class")
def book_class(
    datetime: Annotated[str, typer.Option("--datetime", help="Lesson start datetime in ISO format")],
    teacher_id: Annotated[str, typer.Option("--teacher-id", help="Teacher ID to book")]
):
    """
    Book a class with a specific teacher at a specific time.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, use_cache=True)
        if not token:
            typer.echo("Authentication: Failure")
            return
            
        client.set_token(token)
        result = book_lesson(client, teacher_id, datetime)
        
        # If the booking failed with what looks like an auth error, try once with fresh login
        if result.get("status") == "error" and ("Unauthorized" in str(result.get("message")) or "401" in str(result.get("message"))):
            typer.echo("Token might be expired. Retrying with fresh login...")
            token = login(client, use_cache=False)
            if token:
                client.set_token(token)
                result = book_lesson(client, teacher_id, datetime)

        if result.get("status") == "success":
            typer.echo("Booking status: Success")
            msg = result.get("message", {})
            typer.echo(f"Booking ID: {msg.get('id')}")
            typer.echo(f"Class with {msg.get('staff', {}).get('first_name')} {msg.get('staff', {}).get('last_name')} confirmed.")
        else:
            typer.echo("Booking status: Error")
            typer.echo(f"Message: {result.get('message')}")
    finally:
        client.close()

@app.command(name="list-classes")
def list_classes(
    all: Annotated[bool, typer.Option("--all", help="Show all classes, including past and cancelled")] = False
):
    """
    List your upcoming bookings.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, use_cache=True)
        if not token:
            typer.echo("Authentication: Failure")
            return
            
        client.set_token(token)
        bookings = get_bookings(client)
        
        if not bookings:
            typer.echo("No bookings found.")
            return

        # Filter for upcoming and not cancelled unless --all is specified
        if not all:
            bookings = [b for b in bookings if b.get("status") != "cancelled" and not b.get("past")]

        if not bookings:
            typer.echo("No upcoming bookings found. Use --all to see past/cancelled classes.")
            return

        typer.echo(f"{'ID':<10} | {'Staff ID':<10} | {'Staff Name':<20} | {'Date':<10} | {'Time':<10} | {'Status':<10}")
        typer.echo("-" * 80)
        for b in bookings:
            staff = b.get("staff", {})
            staff_name = f"{staff.get('first_name', '')} {staff.get('last_name', '')}".strip()
            typer.echo(f"{b.get('id'):<10} | {b.get('staff_id'):<10} | {staff_name:<20} | {b.get('date'):<10} | {b.get('start_time'):<10} | {b.get('status'):<10}")
    finally:
        client.close()

@app.command(name="cancel-class")
def cancel_class(
    booking_id: Annotated[str, typer.Option("--booking-id", help="Booking ID to cancel")]
):
    """
    Cancel a specific class booking.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, use_cache=True)
        if not token:
            typer.echo("Authentication: Failure")
            return
            
        client.set_token(token)
        result = cancel_booking(client, booking_id)
        
        if result.get("status") == "success":
            typer.echo(f"Successfully cancelled booking {booking_id}.")
        else:
            typer.echo(f"Failed to cancel booking {booking_id}.")
            typer.echo(f"Message: {result.get('message')}")
    finally:
        client.close()

@app.command(name="server-time")
def server_time():
    """
    Checks the server time and compares it with the local system time.
    """
    from datetime import datetime as dt, timezone
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client)
        if token:
            client.set_token(token)
            typer.echo("Checking server time synchronization...")
            
            local_before = dt.now(timezone.utc)
            result = get_server_time(client)
            
            if "status" in result and result["status"] == "error":
                typer.echo(f"Failure: {result.get('message', 'Unknown error')}")
                return

            typer.echo(f"Server Response: {result}")
            
            # Common keys for time
            server_time_str = result.get("time") or result.get("datetime") or result.get("now")
            if server_time_str:
                try:
                    server_dt = dt.fromisoformat(server_time_str.replace('Z', '+00:00'))
                    if server_dt.tzinfo is None:
                        server_dt = server_dt.replace(tzinfo=timezone.utc)
                    
                    diff = (server_dt - local_before).total_seconds()
                    status_icon = "✓" if abs(diff) < 5 else "✗"
                    typer.echo(f"Server Time (UTC): {server_dt.isoformat()}")
                    typer.echo(f"Local Time (UTC):  {local_before.isoformat()}")
                    typer.echo(f"Difference: {diff:+.3f} seconds {status_icon}")
                    
                    if abs(diff) > 5:
                        typer.echo("Warning: Server and local time are out of sync by more than 5 seconds!")
                    else:
                        typer.echo("Sync Check: OK")
                except Exception:
                    typer.echo("Could not parse server time for comparison.")
            else:
                 typer.echo("Server response did not contain a recognizable time field for automatic comparison.")
        else:
            typer.echo("Authentication: Failure")
    finally:
        client.close()

@app.command(name="run-due")
def run_due(
    verbose: Annotated[bool, typer.Option("--verbose", help="Show verbose output about upcoming rules")] = False,
    force: Annotated[bool, typer.Option("--force", help="Force the next upcoming rule to be processed now")] = False,
    force_soft: Annotated[bool, typer.Option("--force-soft", help="Soft force: process everything but skip the final booking request")] = False
):
    """
    Checks for due bookings and performs them automatically.
    """
    run_due_process(verbose=verbose, force=force, force_soft=force_soft)

@app.command(name="list-tutors")
def list_tutors():
    """
    List all available tutors.
    """
    client = BookingClient(base_url=app_config.base_url)
    try:
        token = login(client, use_cache=True)
        if not token:
            typer.echo("Authentication: Failure")
            return
            
        client.set_token(token)
        tutor_map = get_tutors_map(client)
        
        if not tutor_map:
            typer.echo("No tutors found.")
            return

        typer.echo(f"{'ID':<10} | {'Name':<30} | {'Favorite':<10}")
        typer.echo("-" * 55)
        # Sort by Name for readability
        sorted_tutors = sorted(tutor_map.items(), key=lambda x: x[1]['name'])
        for tid, data in sorted_tutors:
            fav_status = "★" if data.get("is_favorite") else " "
            typer.echo(f"{tid:<10} | {data.get('name'):<30} | {fav_status:<10}")
    finally:
        client.close()

if __name__ == "__main__":
    app()
