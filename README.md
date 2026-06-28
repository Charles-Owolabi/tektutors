# Tektutors Flask Website + LMS

Marketing website and Learning Hub for Tektutors, built with Flask, Jinja templates, SQLite, Flask-Mail, and Zoho Meeting integration.

## Main Features

- Public website pages: home, about, programs, registration, contact
- Learning Hub login and dashboards for admins, mentors, and students
- Classroom, assignment, attendance, material, certificate, and feedback workflows
- Contact, newsletter, and training registration forms
- Email notifications through SMTP
- Optional Zoho Meeting creation, reminders, and recording sync
- `/healthz` endpoint for uptime checks

## Project Structure

```text
.
|-- app.py
|-- passenger_wsgi.py
|-- requirements.txt
|-- zoho_meeting.py
|-- approve_candidate.py
|-- get_zoho_tokens.py
|-- static/
|   |-- css/
|   |-- js/
|   `-- images/
|-- templates/
`-- data/                 # Runtime database, uploads, and form CSVs
```

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000/`.

## Production Requirements

Set these environment variables before deploying:

```text
APP_ENV=production
SECRET_KEY=<strong-random-secret>
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
DATABASE_CONNECT_TIMEOUT_SECONDS=10
ADMIN_PASSWORD=<strong-admin-password>
ADMIN_EMAIL=admin@tektutors.com.ng
TRUST_PROXY_HEADERS=true
```

Configure SMTP values if production form notifications, registration acknowledgement emails, password reset emails, and LMS emails should be delivered.

Important runtime notes:

- `data/` must be writable and persistent on the server.
- If `DATABASE_URL` is set, the app uses PostgreSQL.
- Production requires `DATABASE_URL` by default. To deliberately run production on persistent SQLite, set `ALLOW_SQLITE_IN_PRODUCTION=true`.
- In development, if `DATABASE_URL` is not set, the app falls back to local SQLite at `data/tektutors_lms.db`.
- `.env`, SQLite databases, uploads, logs, and token caches are runtime files and should not be committed.
- Keep `passenger_wsgi.py` for cPanel/Passenger deployments.


## Deployment Readiness Check

Before deploying, set the production environment variables, install dependencies, and run:

```powershell
python scripts/check_deployment.py
```

To also verify the configured database connection:

```powershell
python scripts/check_deployment.py --check-db
```

The checker validates production config, health checks, rendered CSRF tokens, CSRF rejection, writable runtime storage, and warns if the git worktree has uncommitted changes.
## Migrate SQLite Data to PostgreSQL

Install dependencies and set `DATABASE_URL` to the PostgreSQL database you will deploy with, then run:

```powershell
python migrate_sqlite_to_postgres.py
```

By default, the migration reads `data/tektutors_lms.db`. To import the current cPanel SQLite export from Downloads:

```powershell
python migrate_sqlite_to_postgres.py --sqlite-db-path "C:\Users\hp\Downloads\tektutors_lms.db"
```

If the PostgreSQL database was already initialized or seeded by the app, replace its LMS rows with the SQLite snapshot:

```powershell
python migrate_sqlite_to_postgres.py --sqlite-db-path "C:\Users\hp\Downloads\tektutors_lms.db" --replace-target
```

For production/cPanel deployment, keep `DATABASE_URL` in the hosting environment and run the migration once against that PostgreSQL database before routing traffic to the new app version.

## Optional Admin Utility

Approve or reset a Learning Hub user from the command line:

```powershell
python approve_candidate.py --name "Jane Doe" --email "jane@example.com" --role student --send-email
```

Email sending uses the configured SMTP environment variables.

## Waitress Example

```powershell
waitress-serve --listen=0.0.0.0:8080 app:application
```

