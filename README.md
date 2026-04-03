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
4.  Run the setup script — this creates your `.env`, `scheduling_rules.yml`, and installs the scheduled job:
    ```bash
    ./setup.sh
    ```
5.  Fill in your credentials in `.env` and configure your lesson schedule in `scheduling_rules.yml`.
6.  Fetch the teacher list and create `teachers.json` (required before `run-due` will work):
    ```bash
    python main.py populate-teachers
    ```
7.  Update `scheduling_rules.yml` with teacher names from `teachers.json`, then run `run-due`.
8.  Configure `config.yaml` if needed (defaults provided for worldsacross.com).

### Rule format

Each rule in `scheduling_rules.yml` books 1 or 2 consecutive 30-minute slots on a given weekday. Teacher names must match exactly as they appear in `teachers.json`:

```yaml
- label: midday                                    # combined with weekday → rule ID (e.g. mon_midday)
  weekday: mon                                     # mon, tue, wed, thu, fri, sat, sun
  enabled: true
  start_time: "13:00"                              # HH:MM, must be on the hour or half-hour
  slots: 2                                         # 1 books 13:00 only; 2 books 13:00 and 13:30
  preferred_teachers: ["Maria Garcia", "Ana Lopez"] # tried in order; leave empty if allow_fallbacks: true
  allow_fallbacks: true                            # fall back to any available teacher if preferred unavailable
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

Fetch all teachers and update `teachers.json`:
```bash
python main.py populate-teachers
```

List all tutors (also refreshes `teachers.json`):
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

## Features (Prototype)

*   Authentication against the booking backend.
*   Availability check for a target lesson datetime.
*   Listing available teachers.
*   **Teacher Calendar View**: Visual grid of all slots (available/booked) for a specific teacher.
*   **Automated Booking**: Perform lesson booking for a specific teacher and time.
*   **Booking Management**: List upcoming classes and cancel existing bookings.
*   **Automated Scheduling**: Automatically book lessons based on rules when the booking window opens using `run-due`.
