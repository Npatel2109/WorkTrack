# GeoSite Attendance & Site Report Management System

A responsive Flask application for employee site assignments, GPS-validated attendance, live selfie capture, signed visit reports, PDF generation, Excel exports, and administrator audit/history views.

## Requirements

- Python 3.10+
- A modern browser with camera and location permissions for employee visit workflows
- SQLite for local development, or PostgreSQL 13+ for a shared deployment

Camera and GPS browser APIs require HTTPS in deployment (localhost is permitted for local development).

## Local installation

Run these commands from the project directory in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set a unique `SECRET_KEY` in `.env` before deployment. The application generates a random key when none is configured, which is suitable only for a single-process development run because sessions will expire when the process restarts. Flask's CLI loads the local `.env`; production process managers should inject environment variables directly.

Apply the checked-in initial migration and create the first administrator:

```powershell
flask --app wsgi db upgrade
flask --app wsgi init-admin
```

For later model changes, create a new migration with `flask --app wsgi db migrate -m "describe change"`, review and commit it, then apply it with `flask --app wsgi db upgrade`.

Start the local development server:

```powershell
flask --app wsgi run --debug
```

Open `http://127.0.0.1:5000`. Create employee accounts and assign sites after signing in as the administrator.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | Random per process | Flask session signing and CSRF protection; set a stable, secret value for deployments |
| `DATABASE_URL` | `sqlite:///geosite.db` | SQLAlchemy URL; PostgreSQL URLs are normalized to the `psycopg` driver |
| `UPLOAD_FOLDER` | `instance/uploads` | Private storage for selfies and signatures |
| `SESSION_TIMEOUT_MINUTES` | `60` | Idle session timeout |
| `MAX_CONTENT_LENGTH` | `8388608` | Maximum size of a request, in bytes |
| `COOKIE_SECURE` | `0` | Set to `1` behind HTTPS to enable secure session/remember cookies |
| `MAIL_SERVER` | empty | SMTP host used to send password reset links |
| `MAIL_PORT` | `587` | SMTP port; port `465` uses implicit TLS |
| `MAIL_USERNAME` / `MAIL_PASSWORD` | empty | SMTP credentials |
| `MAIL_USE_TLS` | `1` | Enable STARTTLS for non-465 SMTP connections |
| `MAIL_DEFAULT_SENDER` | empty | Verified sender email address |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:5000` | Public app URL used to create password reset links |

For PostgreSQL, set for example (replace the placeholder with your database password):

```text
DATABASE_URL=postgresql://geosite_user:YOUR_PASSWORD@db-host:5432/geosite
```

Do not use SQLite on a shared multi-instance production deployment. Keep the upload directory on persistent private storage and ensure it is not served as a public static directory.

For password reset emails, set the SMTP host, port, credentials, sender, and `PUBLIC_BASE_URL` to the HTTPS URL of the deployed application. Test email delivery before allowing users to rely on self-service password recovery.

## Roles and workflows

### Administrator

- Dashboard with employee/site totals, onsite/completed status, a completed-visit chart, and assigned-site map
- Employee account creation, editing, password reset, and delete/deactivate behavior
- Site assignment with WGS84 coordinates and configurable geofence radius (10–5,000 m; default 100 m)
- Completed report review, PDF download, Excel export, selfie viewing, attendance history, and account access audit log
- Clickable dashboard totals that open their matching employee/site/visit/report lists
- Invoice generation with required service dates, editable billable days, daily rate, currency, customer, and description. Amount is calculated as `billable days × daily rate`.
- Invoice PDFs and invoice history; invoices preserve the employee/site details if assignments later change

### Employee

- View assigned site and open Google Maps directions
- Check in only after server-side Haversine distance validation and a live browser camera capture
- Check out only after a second GPS validation, a complete report, and both required digital signatures
- Review and download completed reports
- Download a single merged PDF for completed reports within a selected date range

### Site coordinates

In site assignment, enter an address and use **Find address on map** to look it up, or click the map to drop a pin and drag it to the exact point. The resulting latitude and longitude are saved with the assignment. Address lookup uses OpenStreetMap Nominatim; if it is unavailable, place the pin manually.

### Password reset

The sign-in page provides **Forgot password?** Reset links are emailed to active accounts, expire after one hour, and are invalidated after use. Configure the SMTP values below before enabling account recovery.

GPS verification is repeated on the server and expires after ten minutes. Site coordinates, report ownership, role access, uploaded image decoding/size, and required fields are validated server-side. Browser geolocation is a device/browser signal, not an anti-spoofing guarantee; deployments with high-assurance attendance requirements should add a trusted device attestation or supervised verification process.

## Security and operations

- Passwords are hashed using Werkzeug's password hashing implementation.
- CSRF protection covers HTML forms and JSON GPS requests.
- Uploads are decoded and re-encoded as PNG under generated filenames and served only to authorized users.
- Admin-only routes enforce role checks; employee report and media routes are owner-scoped.
- Login, logout, timeout, employee/site changes, and attendance actions are audited.
- Employee accounts with history are deactivated rather than hard-deleted so visit and audit records remain intact.
- Configure HTTPS, a stable random `SECRET_KEY`, secure cookies (`COOKIE_SECURE=1`), persistent uploads, database backups, and appropriate database access controls in production.

## Project layout

```text
geosite/
  __init__.py           Flask app factory, security setup, and CLI
  extensions.py         SQLAlchemy, LoginManager, and migration extensions
  models.py             Users, sites, attendance, reports, invoices, and audit records
  routes.py             Authentication, admin, employee, and export routes
  services.py           GPS math, safe image handling, audit helper, PDF builder
  static/
    css/app.css
    js/app.js
    js/workflow.js
  templates/            Bootstrap 5 pages
instance/               Local database and private uploads (not committed)
migrations/              Generated and version-controlled with Flask-Migrate
wsgi.py                  WSGI entry point
requirements.txt
```

## Production serving

Use a production WSGI server rather than Flask's development server, for example:

```sh
gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app
```

Serve behind an HTTPS reverse proxy. The UI loads Bootstrap, Leaflet, Chart.js, and map tiles from their public CDNs; provide an approved/self-hosted distribution if the deployment cannot access those services.
