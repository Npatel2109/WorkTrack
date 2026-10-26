# GeoSite Attendance & Site Report Management System

A responsive Flask application for employee site assignments, GPS-recorded attendance, live selfie capture, signed visit reports, PDF generation, Excel exports, and administrator audit/history views.

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
| `TRUST_PROXY` | `0` | Set to `1` only behind one trusted HTTPS proxy (such as Nginx) to honour `X-Forwarded-*` headers |
| `ADMIN_NAME` / `ADMIN_EMAIL` / `ADMIN_MOBILE` / `ADMIN_PASSWORD` | empty | Used by `flask ensure-admin` to create the first administrator when none exists |

For PostgreSQL, set for example (replace the placeholder with your database password):

```text
DATABASE_URL=postgresql://geosite_user:YOUR_PASSWORD@db-host:5432/geosite
```

Do not use SQLite on a shared multi-instance production deployment. Keep the upload directory on persistent private storage and ensure it is not served as a public static directory.

For password reset emails, an administrator can open **Settings ? Email (password reset)**, enter the SMTP host, port, security, username, password, sender and public app address, then click **Save and send test email to me**. For GoDaddy Professional Email use `smtpout.secureserver.net`, port `465`, SSL. Saved settings are stored in the database (the password is never shown again) and override the `MAIL_*`/`PUBLIC_BASE_URL` environment variables, which remain as a fallback. `wsgi.py` loads `.env`, so Gunicorn picks these up too. Run `flask --app wsgi test-email you@example.com` to check delivery; it prints the SMTP error if sending fails. Test email delivery before allowing users to rely on self-service password recovery.

## Roles and workflows

### Administrator

- Dashboard with employee/site totals, onsite/completed status, a completed-visit chart, and assigned-site map
- Employee account creation, editing, password reset, and delete/deactivate behavior
- Site assignment with WGS84 coordinates and configurable geofence radius (10–5,000 m; default 100 m)
- Multiple active site assignments per engineer; **Add assignment** always adds a separate site without replacing earlier assignments
- **Edit assignment** on each site card opens prefilled site/customer/address, map coordinates, radius and report-header fields. Edits affect only that assignment and are blocked while it has an active visit. Once attendance or invoice history exists, its engineer cannot be changed; add a separate assignment instead. Historical attendance coordinate/distance snapshots remain unchanged; site names/header details use the updated assignment when displayed/exported.
- Completed report review, PDF download, Excel export, selfie viewing, attendance history, and account access audit log
- Clickable dashboard totals that open their matching employee/site/visit/report lists
- Invoice generation with required service dates, editable billable days, daily rate, currency, customer, and description. Amount is calculated as `billable days × daily rate`.
- Invoice PDFs and invoice history; invoices preserve the employee/site details if assignments later change

### Employee

- View assigned site and open Google Maps directions
- Choose from all assigned sites when starting a visit (only one visit can be active at a time)
- Start with a fresh GPS capture and live selfie, even outside the assigned site's reference radius
- Stop with current GPS, or the last server-recorded location when GPS is unavailable; complete the activity/billing report now or later
- Review and download completed reports
- Download a single merged PDF for completed reports within a selected date range

### Visit locations and daily automatic stop

Apply the latest database migration before restarting the updated app:

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi db upgrade
```

The radius is a reference, not a block on starting or stopping. The server records
the actual start/stop coordinates and Haversine distance in meters from the
administrator's assigned coordinates. Each new visit snapshots those assigned
coordinates, so later assignment edits do not change its recorded distances.
**Admin > Attendance** shows both distances, clickable location links, capture
timestamps, current/last-known/unavailable source, and automatic-stop status.
PDF reports retain the distances and stop-location provenance too. Historical
visits use the assigned coordinates available at migration time; missing
historical distances and capture provenance are not invented.

While an engineer has an active visit and an app page open and visible, it
requests a fresh location immediately and every minute. Successful fixes replace
the visit's last captured location in the database; this is not a full movement
history. Starting still requires a fresh fix (valid for ten minutes) and a selfie.
Stopping first attempts a fresh fix. If permission is denied, GPS is off, or the
request fails/times out, the server uses the last captured fix (at least the
start location), retaining its original capture time and marking it **last known**.
If no fix exists it explicitly records **location unavailable**; it never
substitutes the assigned site's coordinates.

Stopping immediately ends attendance and leaves the report **pending**. Pending
reports appear on the engineer's dashboard and in the admin status/attendance
views. The engineer can complete them later, including after starting another
visit. Report submission does not change the saved stop time or location, and
pending reports are not included in completed-report downloads or billable exports.

Forgotten visits close at **23:59 Asia/Kolkata (India, UTC+05:30)** on their start
date, with a pending report. New starts are blocked during the final minute
until local midnight. Automatic stops use the last location actually received
from the device, clearly marked **last known**; a server cannot request fresh
device GPS when the browser/WebView is closed, suspended, offline, or denied
permission. Leaving device location on alone does not enable background GPS.
The engineer should keep the app visible for location updates. True closed-app
tracking would require a separate native background-location feature.

**Deployment requirement:** run this idempotent command **every minute** with
Windows Task Scheduler or your deployment's scheduler, even when nobody is
using the app:

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi close-overdue-visits
```

Set its working directory to the project root and use the deployment's normal
environment/database. For example, from the project directory on Windows:

```powershell
$project = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$project\.venv\Scripts\python.exe" -Argument '-m flask --app wsgi close-overdue-visits' -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "WorkTrack visit cutoff" -Action $action -Trigger $trigger -Description "Stop overdue visits at 23:59 India time"
```

Configure the task to run whether the service account is logged on or not,
using Task Scheduler's credential/security settings. Monitor nonzero exit codes;
database errors are not suppressed. The web app also catches up overdue visits
before authenticated app requests, using their original 23:59 cutoff rather than the later request
time. This covers downtime, but does not replace the scheduled command for
unattended closure. No OS task is installed automatically by the application.

### Site coordinates

In site assignment, enter an address and use **Find address on map** to look it up, or click the map to drop a pin and drag it to the exact point. The resulting latitude and longitude are saved with the assignment. Address lookup uses OpenStreetMap Nominatim; if it is unavailable, place the pin manually.

### Site activity and billable Excel reports

Apply the latest migration before running the updated application. At checkout,
employees enter **Activity detail** and select **Billable** or **Non-billable**;
the engineer name remains required. Customer representative name and digital
signatures are not collected at checkout. PDF and Excel reports retain blank
customer-name/signature/date areas for offline completion after printing.
Previously captured names/signatures remain available in historical PDFs. The six former
work-detail fields are no longer required for new reports. Existing report text
is retained; legacy reports with no billing choice are explicitly marked
**Billing not recorded** and count as 0 in the new export, rather than assuming
they were billable. Individual/merged PDFs and the original admin export remain.

Admins fill **Project**, **PO number**, **PO date**, **Reporting company**,
**Commissioning manager**, **Project manager** and up to five remarks on site assignments, or use
**Save report header** on existing site cards. These optional header details are
used at export time; updating them changes future exports, not saved visits.

Under **Reports**, choose a site/employee and start/end dates, then **Download
Excel**. The `SAR-R0` worksheet follows the supplied report structure:
project/customer/PO/engineer headers, SR NO / SITE / DATE / DAY / ACTIVITY DETAIL,
five numbered remarks lines, total days and three offline sign-off boxes
(commissioning manager, project manager and engineer), retaining the supplied
Excel layout with grayscale headers and portrait printing. The report update
date is the UTC export date. No sample customer/company/manager data is reused.
Employees can export only their own sites; admins can select any site, including
archived assignments. Dates use the completed visit's UTC checkout date.
Admin **Export Excel** buttons open this same report form rather than a separate
raw-data spreadsheet. Admins select an employee first, then one of that employee's
sites and the date range. Both roles use the same workbook generator and layout;
employee/site ownership is checked on the server as well as in the selector.

Each completed billable visit is a separate 1-day row; non-billable visits are 0.
Multiple billable visits on a date each count as 1, as requested. Approved leave
is 0 with the employee's reason (including half-day labels); pending/rejected
leave is excluded. Leave is employee-wide and appears even if a visit also exists
that date. Empty dates show 0 and **Sunday** on Sundays or **No activity recorded**
otherwise. Range boundaries are inclusive, limited to 367 days. Historical
assignment periods are not inferred: choose the appropriate site/date range.
Full text is stored as Excel strings, not formulas. Sign-off areas are blank,
not fabricated approvals or copies of signatures from unrelated visits.

### Employee leave

Apply the latest migration (`flask --app wsgi db upgrade`) before starting the updated app.
Employees can open **My leave** or the leave section on their dashboard to apply for
full-day date ranges or a single morning/afternoon half day, with a required reason.
Dates use UTC, matching attendance; full-day ranges include weekends. Past start
dates and overlapping pending/approved requests are rejected. Opposite half days
on the same date are allowed.

Administrators can approve or reject pending requests under **Leave**. Decisions
are final and recorded in the audit log; employees can see their status and reviewer.
Expired requests or requests from inactive employees cannot be approved. Employees
with leave history are deactivated rather than deleted.

Both roles can filter the leave page by an inclusive date range and see **Total
leaves taken (days)**. Totals include only approved days within the range through
today (UTC), counting half days as 0.5 and including weekends. Future days, pending
requests and rejections are excluded. Blank boundaries mean no date limit.
The request list includes requests overlapping the range and displays their full
duration; the total counts only the overlapping elapsed days. Admin status tabs
filter that list, not the approved-leave totals.

Admins can search employees by name or email, select an exact employee, and see
per-employee totals (including inactive employees' history). Employee searches
use literal text, not wildcard patterns. Employees can only view their own
requests and totals, regardless of employee filters supplied in a URL.

The admin dashboard includes clickable **Pending leave requests** and **On leave
today** cards, refreshed with the existing 15-second live status update. The latter
counts distinct active employees with approved leave covering today's UTC date,
including either half day (not a real-time morning/afternoon schedule). An employee
is shown as **On leave** unless they have an active site visit, which retains
**Onsite** status. Leave is informational and does not block site visits or change
attendance records.

### Password reset

The sign-in page provides **Forgot password?** Reset links are emailed to active accounts, expire after one hour, and are invalidated after use. Configure the SMTP values below before enabling account recovery.

Start-location capture expires after ten minutes. Coordinates and distances are validated/calculated server-side without enforcing proximity. Report ownership, role access, uploaded image decoding/size, and required fields are validated server-side. Browser geolocation is a device/browser signal, not an anti-spoofing guarantee; deployments with high-assurance attendance requirements should add a trusted device attestation or supervised verification process.

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

Attendance regression checks (JavaScript tests use Node.js's built-in runner):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_workflows.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_attendance_migration.py -v
node --test tests\location_workflow.test.js
```

## Temporary public HTTPS link (Cloudflare)

For local single-process testing, administrators can open **Settings → Temporary
global access**, confirm public exposure and click **Create public link**. Click
**Refresh tunnel status** until connected, then share the generated
`https://....trycloudflare.com` link. Admins and employees still sign in normally;
the tunnel does not bypass roles or expose private uploads directly.

Install the official `cloudflared` executable. On Windows WorkTrack looks for
`instance/tools/cloudflared.exe`; alternatively set `CLOUDFLARED_PATH` to its
installed executable path before starting Flask. Start the app **without debug**:

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi run --host 0.0.0.0 --port 5000
```

The tunnel forwards only to `http://127.0.0.1:5000`; this is server configuration,
not a user-supplied forwarding target. Keep the PC/app running. Stop it with
**Stop public link** (preferably from the local address). Graceful process exit
also stops the owned child; a hard crash may require stopping that exact
cloudflared process manually. Do not use multiple web workers or the debug
reloader: tunnel ownership/status is local to one app process.

Cloudflare proxies public traffic. Use strong passwords, test data and trusted
users; the internet can reach the login page. Quick Tunnels are development-only,
have no availability guarantee and change address on restart. No account/domain
is needed. The Android app always uses its built-in address and does not use
tunnel links. Password-reset email links use the public app address from
Settings (or `PUBLIC_BASE_URL`); configure it separately if needed.

## Android app

The embedded Android client is a thin WebView shell around this web app, so screens,
features, roles and data always come from the server; updating the server updates
the app without reinstalling. Its server address is fixed at build time to
`http://worktrack.blackpurple.in` (`AppConfig.SERVER` in the Android sources, mirrored
by `ANDROID_SERVER_URL` in `geosite/__init__.py`). Users never enter an address. When
the server is reachable the app shows live data; otherwise it shows `-` and retries
automatically every 15 seconds (or on **Reload**/**Retry**).

See [Android build and installation instructions](android/README.md). After building
and copying the signed APK to `instance/downloads/worktrack.apk` on the server, an
administrator enables **Show "Download Android app" on the login page** in **Settings**.

The fixed address uses HTTP, so passwords and data are unencrypted and Android
blocks camera/GPS site visits until the domain is served over HTTPS. To switch to
HTTPS, change `AppConfig.SERVER` and `ANDROID_SERVER_URL`, increase `versionCode`, rebuild
and redistribute the APK. The default local build is development-signed; use a private
release signing key for production. No Play Store account is required for sideloading.

## Production serving

Use a production WSGI server rather than Flask's development server, for example:

```sh
gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app
```

Serve behind an HTTPS reverse proxy. The UI loads Bootstrap, Leaflet, Chart.js, and map tiles from their public CDNs; provide an approved/self-hosted distribution if the deployment cannot access those services.
