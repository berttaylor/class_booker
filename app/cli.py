from datetime import datetime as dt, timezone
import time
from typing import Annotated

import typer

from app.api.auth import get_cached_token, TOKEN_CACHE_FILE
from app.api.availability import (
    get_available_teachers,
    get_teacher_slots,
    get_tutors_map,
)
from app.config import app_config, settings
from app.services.scheduler import run_due_process
from app.services.session import master_client
from app.teachers import populate_teachers, load_teacher_cache
from app.utils import get_server_time

app = typer.Typer()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """
    Spanish Class Booking Automation CLI.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def show_teacher_calendar(teacher_id: str, use_cache: bool = True):
    from app.ui.calendar import format_calendar

    try:
        with master_client(use_cache=use_cache) as client:
            tutor_map = get_tutors_map(client)
            teacher_name = tutor_map.get(str(teacher_id), {}).get(
                "name", f"Teacher {teacher_id}"
            )
            typer.echo(f"Fetching calendar for: {teacher_name} (ID: {teacher_id})")
            slots = get_teacher_slots(client, teacher_id)
            typer.echo(format_calendar(slots))
    except RuntimeError:
        typer.echo("Authentication: Failure")


def run_check(datetime: str, use_cache: bool = True):
    try:
        with master_client(use_cache=use_cache) as client:
            token = client.client.headers.get("Authorization", "").replace(
                "Bearer ", ""
            )
            is_cached = use_cache and get_cached_token(TOKEN_CACHE_FILE) == token
            typer.echo(f"Target lesson datetime: {datetime}")
            typer.echo(
                "Authentication: Success (using cached token)"
                if is_cached
                else "Authentication: Success"
            )

            teachers = get_available_teachers(client, datetime)
            if teachers:
                typer.echo(f"\nFound {len(teachers)} available teachers:")
                for t in teachers:
                    typer.echo(
                        f" ✓ {t.get('name'):<30} (ID: {t.get('id'):<4}) at {t.get('start_time_local')} ({app_config.timezone})"
                    )
            else:
                typer.echo("No available teachers found for this slot.")
            typer.echo("\nRaw response summary: Fetching completed.")
    except RuntimeError:
        typer.echo("Authentication: Failure")


@app.command(name="check-availability")
def check_availability(
    datetime: Annotated[
        str, typer.Option("--datetime", help="Target lesson datetime in ISO format")
    ],
):
    """
    Check for available Spanish teachers for a target lesson datetime.
    Example: --datetime "2026-04-08T13:30:00+02:00"
    """
    run_check(datetime)


@app.command(name="teacher-calendar")
def teacher_calendar(
    teacher_id: Annotated[
        str, typer.Option("--teacher-id", help="Teacher ID for calendar view")
    ],
):
    """
    Display a teacher's availability as a calendar grid.
    """
    show_teacher_calendar(teacher_id)


@app.command(name="server-time")
def server_time():
    """
    Checks the server time and compares it with the local system time.
    """
    try:
        with master_client() as client:
            typer.echo("Checking server time synchronization...")
            local_before = dt.now(timezone.utc)
            result = get_server_time(client)

            if result.get("status") == "error":
                typer.echo(f"Failure: {result.get('message', 'Unknown error')}")
                return

            typer.echo(f"Server Response: {result}")

            server_time_str = (
                result.get("time") or result.get("datetime") or result.get("now")
            )
            if server_time_str:
                try:
                    server_dt = dt.fromisoformat(server_time_str.replace("Z", "+00:00"))
                    if server_dt.tzinfo is None:
                        server_dt = server_dt.replace(tzinfo=timezone.utc)
                    diff = (server_dt - local_before).total_seconds()
                    status_icon = "✓" if abs(diff) < 5 else "✗"
                    typer.echo(f"Server Time (UTC): {server_dt.isoformat()}")
                    typer.echo(f"Local Time (UTC):  {local_before.isoformat()}")
                    typer.echo(f"Difference: {diff:+.3f} seconds {status_icon}")
                    if abs(diff) > 5:
                        typer.echo(
                            "Warning: Server and local time are out of sync by more than 5 seconds!"
                        )
                    else:
                        typer.echo("Sync Check: OK")
                except Exception:
                    typer.echo("Could not parse server time for comparison.")
            else:
                typer.echo(
                    "Server response did not contain a recognizable time field for automatic comparison."
                )
    except RuntimeError:
        typer.echo("Authentication: Failure")


@app.command(name="run-due")
def run_due(
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Force the next upcoming rule to be processed now"
        ),
    ] = False,
    force_soft: Annotated[
        bool,
        typer.Option(
            "--force-soft",
            help="Soft force: process everything but skip the final booking request",
        ),
    ] = False,
):
    """
    Checks for due bookings and performs them automatically.
    """
    run_due_process(force=force, force_soft=force_soft)


@app.command(name="list-tutors")
def list_tutors():
    """
    List all available tutors and refresh data/teachers.json cache.
    """
    try:
        with master_client() as client:
            tutor_map = get_tutors_map(client)

            if not tutor_map:
                typer.echo("No tutors found.")
                return

            populate_teachers(client)

            typer.echo(f"{'ID':<10} | {'Name':<30}")
            typer.echo("-" * 43)
            for tid, data in sorted(tutor_map.items(), key=lambda x: x[1]["name"]):
                typer.echo(f"{tid:<10} | {data.get('name'):<30}")
    except RuntimeError:
        typer.echo("Authentication: Failure")


@app.command(name="populate-teachers")
def populate_teachers_cmd():
    """
    Fetch all teachers from the API, save to teachers.json.
    Intended to be run on a cron job / launchd as a daily sync.
    """
    if not settings.populate_teachers_enabled:
        typer.echo("populate-teachers disabled (POPULATE_TEACHERS=false) — skipping")
        return
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.monotonic()
    try:
        with master_client() as client:
            populate_teachers(client)
            elapsed = time.monotonic() - t0
            cache = load_teacher_cache()
            teacher_count = len(cache.get("teachers", {}))
            typer.echo(
                f"[{timestamp}] Teachers sync — {elapsed:.2f}s — {teacher_count} teachers"
            )
    except RuntimeError:
        typer.echo(f"[{timestamp}] Teachers sync — FAILED — authentication error")


if __name__ == "__main__":
    app()
