from datetime import datetime as dt, timezone, timedelta
import pytz
from app.client import BookingClient
from app.config import app_config

def normalize_datetime(dt_str: str) -> str:
    """
    Normalizes a datetime string to UTC format: YYYY-MM-DDTHH:MM:00+00:00
    """
    try:
        # Parse the input datetime
        d = dt.fromisoformat(dt_str.replace('Z', '+00:00'))
        # Convert to UTC
        d_utc = d.astimezone(timezone.utc)
        # Format back to the string format used by the API
        return d_utc.strftime('%Y-%m-%dT%H:%M:00+00:00')
    except Exception:
        return dt_str

from collections import defaultdict
from typing import Dict, Optional, Any

def get_tutors_map(client: BookingClient) -> Dict[str, Dict[str, Any]]:
    """
    Fetches the list of tutors and returns a mapping of ID to its data (name, is_favorite).
    """
    response = client.get(app_config.tutors_list_endpoint)
    if response.status_code != 200:
        return {}
    
    try:
        res_data = response.json()
        tutors = res_data.get("data", [])
        
        tutor_map = {}
        for tutor in tutors:
            tid = str(tutor.get("id"))
            name = tutor.get("name")
            is_favorite = tutor.get("is_favorite", False)
            if tid and name:
                tutor_map[tid] = {
                    "name": name,
                    "is_favorite": is_favorite
                }
        return tutor_map
    except Exception as e:
        print(f"Error fetching tutors list: {e}")
        return {}

def get_teacher_slots(client: BookingClient, teacher_id: str) -> list:
    """
    Fetches all slots for a specific teacher.
    """
    data = {"duration": 60}
    response = client.post(app_config.availability_endpoint, json=data)
    
    if response.status_code != 200:
        return []
        
    try:
        res_json = response.json()
        service_data = res_json.get("1", {})
        if not service_data and isinstance(res_json, list):
            for item in res_json:
                if isinstance(item, dict) and "1" in item:
                    service_data = item["1"]
                    break
        
        # teacher_id might be passed as int or string from CLI
        teacher_slots = service_data.get(str(teacher_id), [])
        if not teacher_slots:
             teacher_slots = service_data.get(int(teacher_id), [])
             
        return teacher_slots
    except Exception as e:
        print(f"Error fetching teacher slots: {e}")
        return []

def format_calendar(slots: list) -> str:
    """
    Formats slots into a readable calendar grid in the terminal,
    adjusted to the local timezone.
    """
    if not slots:
        return "No slots found for this teacher."

    local_tz = pytz.timezone(app_config.timezone)

    # Group slots by day
    days = defaultdict(list)
    for slot in slots:
        try:
            # Backend is UTC
            start_utc = dt.fromisoformat(slot["start_time"].replace('Z', '+00:00'))
            # Convert to local timezone
            start_local = start_utc.astimezone(local_tz)
            
            # Format day as YYYY-MM-DD in local time
            day_str = start_local.strftime("%Y-%m-%d")
            
            # Store both for grid mapping
            slot_copy = slot.copy()
            slot_copy["_local_start"] = start_local
            days[day_str].append(slot_copy)
        except Exception:
            continue

    if not days:
        return "No valid slots to display."

    # Identify the full range of dates to display
    sorted_existing_days = sorted(days.keys())
    first_day = dt.strptime(sorted_existing_days[0], "%Y-%m-%d").date()
    last_day = dt.strptime(sorted_existing_days[-1], "%Y-%m-%d").date()
    
    full_date_range = []
    curr = first_day
    while curr <= last_day:
        full_date_range.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)
    
    # Collect all unique local time slots across all days to form the Y-axis
    times = set()
    for day in sorted_existing_days:
        for slot in days[day]:
            times.add(slot["_local_start"].strftime("%H:%M"))
    
    sorted_times = sorted(list(times))
    
    # Build the header (Days)
    time_col_label = f"Time ({app_config.timezone})"
    time_col_width = len(time_col_label)
    
    header_row_1 = f"{' ' * time_col_width} |"
    header_row_2 = f"{time_col_label} |"
    
    for day_str in full_date_range:
        dt_obj = dt.strptime(day_str, "%Y-%m-%d")
        weekday = dt_obj.strftime("%a") # 3-letter weekday
        short_date = day_str[5:] # MM-DD
        
        # We need consistent column widths. 
        # [AVA] is 5 chars, with colors it's more but visible is 5.
        # Header " Mon " is 5 chars. " 04-02 " is 7 chars.
        # Let's use 7 chars for columns to fit " 04-02 " and center " Mon " and "[AVA]".
        header_row_1 += f"  {weekday}  |"
        header_row_2 += f" {short_date} |"
    
    output = header_row_1 + "\n"
    output += header_row_2 + "\n"
    output += "-" * len(header_row_2) + "\n"
    
    # Build each row (Time)
    for time in sorted_times:
        row = f"{time:<{time_col_width}} |"
        for day in full_date_range:
            # Find the slot for this day and local time
            slot_on_day = next((s for s in days.get(day, []) if s["_local_start"].strftime("%H:%M") == time), None)
            if slot_on_day:
                status = slot_on_day.get("status", "")
                if status == "available":
                    # Green for available
                    row += " \033[92m[AVA]\033[0m |"
                elif status == "booked":
                    # Grey (Dim) for booked
                    row += " \033[90m[BKD]\033[0m |"
                else:
                    row += " [---] |"
            else:
                row += "       |"
        output += row + "\n"
        
    return output

def get_available_teachers(client: BookingClient, lesson_datetime: str) -> list:
    """
    Calls the availability endpoint and returns a list of available teachers.
    Each teacher should be a dict with at least 'id' and 'name'.
    """
    tutor_map = get_tutors_map(client)
    
    # duration 60 as per example
    data = {
        "duration": 60
    }
    
    target_utc = normalize_datetime(lesson_datetime)
    
    response = client.post(app_config.availability_endpoint, json=data)
    
    if response.status_code != 200:
        print(f"Failed to fetch availability. Status: {response.status_code}")
        return []
    
    try:
        res_json = response.json()
        
        # The structure is a dict with service IDs as strings
        service_data = {}
        if isinstance(res_json, dict):
            service_data = res_json.get("1", {})
        elif isinstance(res_json, list):
            for item in res_json:
                if isinstance(item, dict) and "1" in item:
                    service_data = item["1"]
                    break
        
        available_teachers = []
        if isinstance(service_data, dict):
            for teacher_id, slots in service_data.items():
                if not isinstance(slots, list):
                    continue
                for slot in slots:
                    if slot.get("start_time") == target_utc and slot.get("status") == "available":
                        teacher_id_str = str(teacher_id)
                        tutor_data = tutor_map.get(teacher_id_str, {})
                        name = tutor_data.get("name", f"Teacher {teacher_id_str}")
                        available_teachers.append({
                            "id": teacher_id_str,
                            "name": name,
                            "start_time_local": dt.fromisoformat(slot["start_time"].replace('Z', '+00:00')).astimezone(pytz.timezone(app_config.timezone)).strftime("%H:%M")
                        })
                        break
        return available_teachers
    except Exception as e:
        print(f"Error parsing availability response: {e}")
        return []
