import typer
from app.client import BookingClient
from app.config import app_config
from app.auth import login, get_cached_token
from app.availability import get_available_teachers, get_teacher_slots, format_calendar

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
        typer.echo(f"Fetching calendar for Teacher ID: {teacher_id}")
        token = login(client, use_cache=use_cache)
        if token:
            client.set_token(token)
            slots = get_teacher_slots(client, teacher_id)
            if not slots and use_cache:
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
            typer.echo(f"Found {len(teachers)} available teachers:")
            for t in teachers:
                typer.echo(f" - {t.get('name')} (ID: {t.get('id')}) at {t.get('start_time_local')} ({app_config.timezone})")
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

if __name__ == "__main__":
    app()
