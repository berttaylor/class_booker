# Spanish Class Booking Automation

Python CLI tool that automates Spanish class booking by calling the booking platform's backend APIs directly.

## Setup

1.  Install Python 3.12+
2.  Create and activate a virtual environment:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```
3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
4.  Run the setup script — this creates your `.env`, `scheduling_rules/bert.yml`, and installs three scheduled jobs:
    ```bash
    ./setup.sh
    ```
5.  Fill in `.env` with the master credentials (used by `populate-teachers`) and optional Pushover tokens. Then fill in your per-account booking credentials in `scheduling_rules/bert.yml` under `credentials:`.
6.  Fetch the teacher list and create `data/teachers.json` (required before `run-due` will work):
    ```bash
    python main.py populate-teachers
    ```
7.  Configure `config.yaml` if needed (defaults provided for worldsacross.com).

### Scheduled jobs

`setup.sh` installs two independent launchd jobs:

| Job | Schedule | Responsibility |
|---|---|---|
| `run-due` | Every hour at :29 and :59 | Reads local `scheduling_rules/bert.yml`, checks for due bookings, books. |
| `populate-teachers` | Daily at 03:00 | Fetches tutors from the booking API, merges into `data/teachers.json`. |

### Rule format

Each rule books 1 or 2 consecutive 30-minute slots on a given weekday. Edit `scheduling_rules/bert.yml` directly — teacher names must match exactly as they appear in `data/teachers.json`. Use `python web.py` to edit and validate via a browser UI.

```yaml
timezone: Europe/Madrid

settings:
  is_active: true        # set to false to pause this schedule without deleting it

credentials:
  email: user@example.com
  password: yourpassword  # per-account credentials used for booking

rules:
  # MONDAY
  - weekday: mon
    start_time: "13:00"
    enabled: true
    slots: 2
    preferred_teachers:
      - "Teacher Name"
      - "Another Teacher"

  - weekday: mon
    start_time: "18:00"
    enabled: false
    slots: 2
    preferred_teachers:
      - "Teacher Name"

  # Add more rules following the same pattern.
  # weekday:            one of mon, tue, wed, thu, fri, sat, sun
  # enabled:            true/false
  # start_time:         "HH:MM" - must be on the hour or half-hour
  # slots:              1 or 2 consecutive 30-min bookings starting at start_time
  # preferred_teachers: teacher names in priority order - must match names in data/teachers.json exactly
  #                     run `python main.py populate-teachers` to generate data/teachers.json.
  # label (optional):   short name for the rule (e.g. "midday", "evening")
```

## Usage

Check availability for a specific datetime:

```bash
python main.py check-availability --datetime "2026-04-08T13:30:00+02:00"
```

View a teacher's availability calendar:
```bash
python main.py teacher-calendar --teacher-id "81"
```

Fetch all teachers and update `data/teachers.json`:
```bash
python main.py populate-teachers
```

List all tutors (also refreshes `data/teachers.json`):
```bash
python main.py list-tutors
```

Book a class:
```bash
python main.py book-class --teacher-id "81" --datetime "2026-04-08T18:30:00+02:00"
```

List upcoming classes:
```bash
python main.py list-classes
```

List all classes (including past and cancelled):
```bash
python main.py list-classes --all
```

Cancel a class:
```bash
python main.py cancel-class --booking-id "221939"
```

Run automated bookings for due rules:
```bash
python main.py run-due
```

Force the next upcoming rule to be processed now (actual booking):
```bash
python main.py run-due --force
```

Soft-force (dry run) the next upcoming rule — simulates everything but doesn't book:
```bash
python main.py run-due --force-soft
```

Check server time synchronization:
```bash
python main.py server-time
```

Edit and validate the schedule in a browser:
```bash
python web.py
# then open http://localhost:5001
```

## Features

*   Authentication against the booking backend.
*   Availability check for a target lesson datetime.
*   Listing available teachers.
*   **Teacher Calendar View**: Visual grid of all slots (available/booked) for a specific teacher.
*   **Automated Booking**: Perform lesson booking for a specific teacher and time.
*   **Booking Management**: List upcoming classes and cancel existing bookings.
*   **Automated Scheduling**: Automatically book lessons based on rules when the booking window opens using `run-due`.
*   **Schedule Editor**: Browser-based YAML editor with validation at `python web.py`.
