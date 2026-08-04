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
4.  Copy `.env.example` to `.env` and fill in the master credentials (used by `populate-teachers`), plus optional Pushover and Healthchecks tokens. Then create `scheduling_rules/<name>.yml` with your per-account booking credentials under `credentials:`.
5.  Fetch the teacher list and create `data/teachers.json` (required before `run-due` will work):
    ```bash
    python main.py populate-teachers
    ```
6.  Configure `config.yaml` if needed (defaults provided for worldsacross.com).

## Deployment

Runs as three Docker Compose services. Both locally and on the VPS, the whole schedule lives in the repo — nothing is installed on the host but Docker.

| Service | What it does |
|---|---|
| `web` | gunicorn serving the editor and status panel. Not port-published; only Caddy reaches it. |
| `cron` | supercronic running `crontab`: `run-due` at :29/:59, `populate-teachers` at 03:00. |
| `caddy` | HTTPS (automatic Let's Encrypt) and Basic Auth on ports 80/443. |

### Local

```bash
make up        # http://localhost:8080 — same Basic Auth as production
make dry-run   # exercise the scheduler without booking anything
make down
```

Local goes through Caddy with the same password as production. **There is no way to reach the web service without authenticating**, in either mode: the `web` service publishes no host port, so Caddy is the only entrance. The app has no auth of its own, so a second unauthenticated door would only stay shut as long as every future deploy remembered to exclude it.

The only difference from production is plain HTTP on `127.0.0.1:8080` instead of HTTPS on the real domain — a laptop can't answer the ACME challenge for `classes.bertbert.work`.

`cron` sits behind a `manual` profile locally so `make up` doesn't book real classes from a development machine.

`compose.dev.yml` is **gitignored**, because compose loads `compose.override.yml` automatically by name and a stray overlay on a server could start the stack with no Caddy in front of it. `make` creates it from `compose.dev.yml.example` on first use.

### VPS (one-time)

1.  Create the box (Hetzner CX22 is plenty) with a firewall allowing **only 22, 80, 443**. Port 8008 is never published.
2.  Harden SSH: keys only (`PasswordAuthentication no`), root login disabled, and enable `unattended-upgrades`.
3.  Point DNS at it: an `A` record for `classes.bertbert.work`. On Cloudflare set it to **DNS-only (grey cloud)** — the orange-cloud proxy terminates TLS itself and breaks Caddy's ACME challenge.
4.  Install Docker, then `git clone` this repo to `/srv/class_booker`.
5.  Generate a Basic Auth hash and put it in `.env` as `BASIC_AUTH_HASH`:
    ```bash
    docker run --rm caddy:2 caddy hash-password --plaintext 'a-long-generated-password'
    ```
    Use a password manager. **Not** the same password as the booking site — this is what guards those credentials.

    **Double every `$` in the hash** when writing it to `.env`: compose reads a single `$` as variable interpolation and silently truncates the hash, which breaks authentication. `$2a$14$abc...` becomes `$$2a$$14$$abc...`.

    Compose refuses to start Caddy if `BASIC_AUTH_HASH` is missing or empty — an empty hash matches nothing and would publish the schedules unauthenticated.
6.  Copy the secrets and schedules across (they're gitignored, so they aren't in the clone):
    ```bash
    scp .env booker:/srv/class_booker/
    scp scheduling_rules/*.yml booker:/srv/class_booker/scheduling_rules/
    scp data/teachers.json booker:/srv/class_booker/data/
    ```
7.  Start it: `ssh booker 'cd /srv/class_booker && docker compose -f compose.yml up -d --build'`

Thereafter, deploy with `./deploy.sh` (needs an `ssh` alias named `booker`).

### Monitoring

Three layers, because each catches what the others can't:

- **Pushover** — booking failures, auth failures, and crashes, pushed immediately.
- **Healthchecks.io** — set `HC_PING_URL` in `.env`; the crontab pings it after every successful `run-due`. If pings stop, Healthchecks alerts you. This is the only layer that catches the whole box being dead, since a dead container can't notify anything.
- **Status panel** — the index page flags a stale last-run, but only when you look at it.

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
docker compose up web
# then open http://localhost:8008
```

The index page also shows the run history: last run and outcome, when the next
check is due, the next booking window, and a table of recent runs. Refresh for
current state — it's server-rendered with no JavaScript.

## Features

*   Authentication against the booking backend.
*   Availability check for a target lesson datetime.
*   Listing available teachers.
*   **Teacher Calendar View**: Visual grid of all slots (available/booked) for a specific teacher.
*   **Automated Booking**: Perform lesson booking for a specific teacher and time.
*   **Booking Management**: List upcoming classes and cancel existing bookings.
*   **Automated Scheduling**: Automatically book lessons based on rules when the booking window opens using `run-due`.
*   **Schedule Editor**: Browser-based YAML editor with validation.
*   **Run History**: Every run records its outcome to `logs/runs.jsonl`, surfaced on the status panel with a warning when the scheduler goes quiet.
*   **Notifications**: Pushover alerts on failures and crashes; optional Healthchecks.io dead-man's switch for whole-host failure.
