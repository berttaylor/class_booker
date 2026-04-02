from collections import defaultdict
from datetime import datetime as dt, timedelta

import pytz

from app.config import app_config


def format_calendar(slots: list) -> str:
    """
    Formats a list of availability slots into a readable calendar grid for the terminal,
    with times adjusted to the configured local timezone.
    """
    if not slots:
        return "No slots found for this teacher."

    local_tz = pytz.timezone(app_config.timezone)

    days = defaultdict(list)
    for slot in slots:
        try:
            start_utc = dt.fromisoformat(slot["start_time"].replace('Z', '+00:00'))
            start_local = start_utc.astimezone(local_tz)
            day_str = start_local.strftime("%Y-%m-%d")
            slot_copy = slot.copy()
            slot_copy["_local_start"] = start_local
            days[day_str].append(slot_copy)
        except Exception:
            continue

    if not days:
        return "No valid slots to display."

    sorted_existing_days = sorted(days.keys())
    first_day = dt.strptime(sorted_existing_days[0], "%Y-%m-%d").date()
    last_day = dt.strptime(sorted_existing_days[-1], "%Y-%m-%d").date()

    full_date_range = []
    curr = first_day
    while curr <= last_day:
        full_date_range.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)

    times = set()
    for day in sorted_existing_days:
        for slot in days[day]:
            times.add(slot["_local_start"].strftime("%H:%M"))

    sorted_times = sorted(list(times))

    time_col_label = f"Time ({app_config.timezone})"
    time_col_width = len(time_col_label)

    header_row_1 = f"{' ' * time_col_width} |"
    header_row_2 = f"{time_col_label} |"

    for day_str in full_date_range:
        dt_obj = dt.strptime(day_str, "%Y-%m-%d")
        weekday = dt_obj.strftime("%a")
        short_date = day_str[5:]
        header_row_1 += f"  {weekday}  |"
        header_row_2 += f" {short_date} |"

    output = header_row_1 + "\n"
    output += header_row_2 + "\n"
    output += "-" * len(header_row_2) + "\n"

    for time in sorted_times:
        row = f"{time:<{time_col_width}} |"
        for day in full_date_range:
            slot_on_day = next(
                (s for s in days.get(day, []) if s["_local_start"].strftime("%H:%M") == time),
                None
            )
            if slot_on_day:
                status = slot_on_day.get("status", "")
                if status == "available":
                    row += " \033[92m[AVA]\033[0m |"
                elif status == "booked":
                    row += " \033[90m[BKD]\033[0m |"
                else:
                    row += " [---] |"
            else:
                row += "       |"
        output += row + "\n"

    return output
