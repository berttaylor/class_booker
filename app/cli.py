from datetime import datetime as dt, timezone
from typing import Annotated

import typer

from app.api.auth import get_cached_token
from app.api.availability import get_available_teachers, get_teacher_slots, get_tutors_map
from app.api.booking import book_lesson, cancel_booking, get_bookings
from app.config import app_config
from app.services.scheduler import run_due_process
from app.services.session import authed_client, ensure_fresh_token
from app.utils import get_server_time

app = typer.Typer()


def _is_auth_error(result: dict) -> bool:
    msg = str(result.get("message", ""))
    return "Unauthorized" in msg or "401" in msg


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
    from app.ui.calendar import format_calendar
    try:
        with authed_client(use_cache=use_cache) as client:
            tutor_map = get_tutors_map(client)
            teacher_name = tutor_map.get(str(teacher_id), {}).get("name", f"Teacher {teacher_id}")
            typer.echo(f"Fetching calendar for: {teacher_name} (ID: {teacher_id})")

            slots = get_teacher_slots(client, teacher_id)
            if not slots and use_cache:
                ensure_fresh_token(client)
                slots = get_teacher_slots(client, teacher_id)

            typer.echo(format_calendar(slots))
    except RuntimeError:
        typer.echo("Authentication: Failure")


def run_check(datetime: str, use_cache: bool = True):
    try:
        with authed_client(use_cache=use_cache) as client:
            token = client.client.headers.get("Authorization", "").replace("Bearer ", "")
            is_cached = use_cache and get_cached_token() == token
            typer.echo(f"Target lesson datetime: {datetime}")
            typer.echo("Authentication: Success (using cached token)" if is_cached else "Authentication: Success")

            teachers = get_available_teachers(client, datetime)
            if not teachers and use_cache:
                ensure_fresh_token(client)
                teachers = get_available_teachers(client, datetime)

            if teachers:
                typer.echo(f"\nFound {len(teachers)} available teachers:")
                for t in teachers:
                    typer.echo(f" ✓ {t.get('name'):<30} (ID: {t.get('id'):<4}) at {t.get('start_time_local')} ({app_config.timezone})")
            else:
                typer.echo("No available teachers found for this slot.")
            typer.echo("\nRaw response summary: Fetching completed.")
    except RuntimeError:
        typer.echo("Authentication: Failure")


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
    try:
        with authed_client() as client:
            result = book_lesson(client, teacher_id, datetime)
            if result.get("status") == "error" and _is_auth_error(result):
                typer.echo("Token might be expired. Retrying with fresh login...")
                ensure_fresh_token(client)
                result = book_lesson(client, teacher_id, datetime)

            if result.get("status") == "success":
                typer.echo("Booking status: Success")
                msg = result.get("message", {})
                typer.echo(f"Booking ID: {msg.get('id')}")
                typer.echo(f"Class with {msg.get('staff', {}).get('first_name')} {msg.get('staff', {}).get('last_name')} confirmed.")
            else:
                typer.echo("Booking status: Error")
                typer.echo(f"Message: {result.get('message')}")
    except RuntimeError:
        typer.echo("Authentication: Failure")


@app.command(name="list-classes")
def list_classes(
    all: Annotated[bool, typer.Option("--all", help="Show all classes, including past and cancelled")] = False
):
    """
    List your upcoming bookings.
    """
    try:
        with authed_client() as client:
            bookings = get_bookings(client)

            if not bookings:
                typer.echo("No bookings found.")
                return

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
    except RuntimeError:
        typer.echo("Authentication: Failure")


@app.command(name="cancel-class")
def cancel_class(
    booking_id: Annotated[str, typer.Option("--booking-id", help="Booking ID to cancel")]
):
    """
    Cancel a specific class booking.
    """
    try:
        with authed_client() as client:
            result = cancel_booking(client, booking_id)
            if result.get("status") == "success":
                typer.echo(f"Successfully cancelled booking {booking_id}.")
            else:
                typer.echo(f"Failed to cancel booking {booking_id}.")
                typer.echo(f"Message: {result.get('message')}")
    except RuntimeError:
        typer.echo("Authentication: Failure")


@app.command(name="server-time")
def server_time():
    """
    Checks the server time and compares it with the local system time.
    """
    try:
        with authed_client() as client:
            typer.echo("Checking server time synchronization...")
            local_before = dt.now(timezone.utc)
            result = get_server_time(client)

            if result.get("status") == "error":
                typer.echo(f"Failure: {result.get('message', 'Unknown error')}")
                return

            typer.echo(f"Server Response: {result}")

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
    except RuntimeError:
        typer.echo("Authentication: Failure")


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
    try:
        with authed_client() as client:
            tutor_map = get_tutors_map(client)

            if not tutor_map:
                typer.echo("No tutors found.")
                return

            typer.echo(f"{'ID':<10} | {'Name':<30} | {'Favorite':<10}")
            typer.echo("-" * 55)
            for tid, data in sorted(tutor_map.items(), key=lambda x: x[1]['name']):
                fav_status = "★" if data.get("is_favorite") else " "
                typer.echo(f"{tid:<10} | {data.get('name'):<30} | {fav_status:<10}")
    except RuntimeError:
        typer.echo("Authentication: Failure")


if __name__ == "__main__":
    app()
