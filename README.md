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
| `UPLOAD_FOLDER` | `instance/uploads` | Private persistent storage for selfies, signatures and submitted report PDFs (including previous versions) |
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
- The live employee table also shows each employee's daily site, WFH/training/travel, leave request decision, or no-submission status. Past records remain available under Leave, Work confirmations and Attendance.
- Employee account creation, editing, password reset, and delete/deactivate behavior
- Site assignment with WGS84 coordinates and configurable geofence radius (10–5,000 m; default 100 m)
- **Sites** is a reusable catalog: create each site once without selecting an employee. **Edit site** changes its shared customer/address, map coordinates, radius and report-header details. Edits are blocked while any engineer has an active visit at that site. Historical attendance coordinate/distance snapshots remain unchanged; displayed site names/header details use the updated catalog entry.
- Site addresses can be searched on the map with OpenStreetMap's Nominatim service; the selected pin fills latitude and longitude and can be moved manually. This free public service is subject to its usage policy and is not an unlimited, high-volume geocoding service. Google Maps can also be integrated, but typically requires a Google Maps Platform key and billing setup.
- **Projects** have a required, unique project number and name. Existing projects are assigned a temporary `LEGACY-<id>` project number by migration; administrators can replace it.
- **Assign sites** is a separate tab: select an active engineer, click **Load assignments**, tick any number of catalog sites and **Save assignments**. The same site can be assigned to multiple engineers without copying its details. Saving again does not duplicate memberships. Unticking removes future start access but preserves earlier visits, reports and membership history; an active visit must be stopped before its site can be unticked.
- The engineer picker is a scrollable radio list with name/email search and wrapping text, avoiding clipped native dropdown options on desktop and Android.
- Completed report review, PDF download, Excel export, selfie viewing, attendance history, and account access audit log
- **Submitted reports**: read-only access to all employee-uploaded reports, their dates/manual man-days, complete change history, and downloads of current and previous PDF versions
- Clickable dashboard totals that open their matching employee/site/visit/report lists
- Invoice generation with an explicit engineer/site selection, required service dates, editable billable days, daily rate, currency, customer, and description. Optional **PO number** and **PO date** are manually entered on invoices only. Amount is calculated as `billable days × daily rate`.
- Invoice PDFs and invoice history; invoices preserve the employee/site details if assignments later change

### Employee

- View assigned site and open Google Maps directions
- Choose from all assigned sites when starting a visit (only one visit can be active at a time)
- Start with a fresh GPS capture and live selfie, even outside the assigned site's reference radius
- Stop with current GPS, or the last server-recorded location when GPS is unavailable; complete the activity/billing report now or later
- Review completed visits and download site-activity Excel reports
- Generated site-visit PDF downloads (individual and merged) are admin-only. Employee-uploaded PDFs in **Submitted reports** can be downloaded by their owner and admins.
- **Submitted reports**: select an active assigned site, then submit an employee-level PDF with start/end dates and manually entered whole-number man-days; edit the site, dates/man-days or replace the PDF later

### Employee-submitted PDF reports

Apply the latest migration before restarting web workers:

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi db upgrade
```

Both roles have a **Submitted reports** navigation tab, separate from generated
site-visit reports. Employees select **Submit report**, choose one of their active
assigned sites, enter an inclusive start/end date range and a positive whole-number
**Total man-days**, then upload a PDF. Man-days are manual, not calculated from
attendance or capped by the number of dates selected. Reports created before site
selection was added remain marked as **Not recorded** until edited.

Uploads must be readable, unencrypted PDFs with at least one page, up to 5 MB
each (the configured request-size limit also applies). File contents are checked;
renaming another file to `.pdf` is not sufficient. Files use generated private
storage names; downloads retain a sanitized original filename.

Employees can use **Edit / replace PDF** to change dates/man-days, replace the
PDF, or both. Leaving the replacement upload blank keeps the current PDF.
Every change creates a retained version with its timestamp, dates, man-days
and PDF reference; changing details alone does not duplicate the file.
Unchanged submissions do not create extra versions. Conflicting/stale edits
are rejected with a prompt to reload rather than overwrite newer changes.

Employees can view and download only their own submissions and versions.
Admins can see every employee's reports and full submission/replacement/edit
history, and download any version, but cannot create or edit an employee's
submission. The list is paginated, with history available from each report.
Actions also appear in the existing administrator audit log. Deactivating an
employee preserves their reports and downloads for admins.

Back up the database **and** the persistent private `UPLOAD_FOLDER` together.
Previous PDFs are intentionally retained, so plan storage accordingly.
The new migration creates empty tables and does not change existing visit
reports or their download permissions.

The sign-in password field includes a **Show / Hide** button; it starts hidden.
PO number/date are not collected on catalog sites or included in activity Excel
reports for either role. Invoice PO fields start blank and are manually entered;
they are included in the invoice PDF.

### Upgrading to reusable sites

Back up the database and stop all web workers before running
`flask --app wsgi db upgrade`, then restart them with the new code. The migration
preserves each existing site ID separately (even similar-looking sites) and
creates its existing engineer membership with its previous active/inactive
state. Attendance, reports and invoices keep their site and engineer references.
No existing sites are automatically merged.

Legacy site PO values remain in the database for historical recovery, but are
not displayed or exported and are not copied onto invoices. Old invoices get
blank PO fields rather than fabricated values. The shared-site migration cannot
be safely downgraded to the former one-engineer-per-site model; restore the
pre-upgrade database backup and old code if a rollback is required.

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
Attendance and site-report PDFs display both start and stop distances in **km**
(three decimal places); the database and GPS API continue to store/use meters.
Site-report PDFs retain stop-location provenance too. Historical
visits use the assigned coordinates available at migration time; missing
historical distances and capture provenance are not invented.

Attendance, report pages, admin live check-in times (including polling), dashboard
details and site-report PDF timestamps are displayed in **IST (UTC+05:30)**.
Stored timestamps remain UTC; naive database timestamps are treated as UTC
before conversion. Admin **Completed today** counts/chart use India calendar
days, and merged PDF date ranges use India checkout dates. The site-activity
Excel's date grouping remains UTC as documented below.

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

In the site catalog, enter an address and use **Find address on map** to look it up, or click the map to drop a pin and drag it to the exact point. The resulting latitude and longitude are saved with the site. Address lookup uses OpenStreetMap Nominatim; if it is unavailable, place the pin manually.

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
they were billable. Individual/merged PDFs remain available to admins.

Admins manage the reusable project-name list in **Projects** and select a
project in site details. Existing site project names are transferred into this
list by the migration. The existing **Sites** page appears as **Project** in
admin navigation, and its **Add Project** form still creates a site entry.
Reporting company is fixed to **Blackpurple automation**. Commissioning
manager, project manager and up to five remarks remain optional site report
details; updates apply to future exports.

Under **Reports**, choose a site/employee and start/end dates, then **Download
Excel**. The `SAR-R0` worksheet follows the supplied report structure:
project/customer/site/engineer headers (no PO fields), SR NO / SITE / DATE / DAY / ACTIVITY DETAIL,
five numbered remarks lines, total days and three offline sign-off boxes
(commissioning manager, project manager and engineer), retaining the supplied
Excel layout with grayscale headers and portrait printing. The report update
date is the UTC export date. Customer and manager details come from the selected
site and employee; reporting company is always Blackpurple automation.
Employees can export only their own sites; admins can select any site, including
archived assignments. Dates use the completed visit's India-local checkout date.
Admin **Export Excel** buttons open this same report form rather than a separate
raw-data spreadsheet. Admins select an employee first, then one of that employee's
sites and the date range. Both roles use the same workbook generator and layout;
engineer/site membership or visit history is checked on the server as well as in
the selector. Each export is scoped to the selected engineer's visits and leave,
even when several engineers share one site. Unassigned engineers can still
export their own historical activity but cannot start new visits there.

Each completed visit keeps its own activity row; non-billable visits are 0.
Only the first billable visit per engineer/site/India-local date counts as 1;
additional billable visits on that date count as 0. Approved leave
is 0 with the employee's reason (including half-day labels); pending/rejected
leave is excluded. Leave is employee-wide and appears even if a visit also exists
that date. Empty dates show 0 and **Sunday** on Sundays or **No activity recorded**
otherwise. Range boundaries are inclusive, limited to 367 days. Historical
assignment periods are not inferred: choose the appropriate site/date range.
Full text is stored as Excel strings, not formulas. Sign-off areas are blank,
not fabricated approvals or copies of signatures from unrelated visits.

### Daily work confirmation and current-month attendance

Apply the latest migration before restarting the app:

```powershell
python -m flask --app wsgi db upgrade
```

The employee dashboard shows **this month's** approved, pending and rejected
leave requests, plus automatic leave dates/days. Cross-month requests count only
their dates in the current India-calendar month. Approved + automatic days are
shown separately from pending/rejected request days.

Admins can upload or remove the company logo under **Settings > Company logo**.
It replaces the G brand mark in the navigation bar, sign-in page and password
recovery pages. PNG, JPEG and WebP files up to 2 MB are supported, with optional
square cropping before upload.

The employee dashboard has one **Today's Work Status** box with **On Site**,
**WFH**, **Leave**, **Training** and **Travelling**. On Site reveals assigned
sites; selecting one reveals **Start Site**, which continues through the existing
GPS and live-selfie confirmation before activating attendance. WFH, Training and
Travelling require a comment; Leave requires a reason and full/half-day duration.
Approved full-day leave automatically displays **On leave** without a submission
and blocks new site starts. One approved half day still permits site work; two
opposite approved halves block starts just like a full day. Stop Site remains
available for existing active visits.

On a day without a recorded site visit, employees choose **WFH**, **Travelling**
or **Training** and supply a reason before **23:59 IST**. Each employee may
submit one confirmation for today, not for another employee or a past date.
Admins review these under **Work confirmations**, including historical pending
requests; employees see their decision on the dashboard. Work confirmation does
not prevent starting a site later. Employees cannot replace their confirmation;
admins can correct its type/comment. A real correction resets approval to pending
and records before/after details in the audit log; an unchanged save preserves approval.

At/after 23:59 IST on Monday-Saturday, an employee with no site start that day
and no approved work confirmation is counted as automatic leave. Pending and
rejected confirmations do not prevent it. Approved leave is not double-counted;
an approved half-day covers 0.5 and only the uncovered 0.5 is automatic.
Approval of a pending work confirmation removes its date's automatic leave.
Sundays do not generate automatic leave. No holidays are inferred.

Automatic leave is derived from these persisted records when the dashboard or
admin summary is viewed; no nightly scheduler is required and missed page views
do not skip days. The policy begins on the migration's India-local activation
date (or the employee's creation date, if later), so it does not mark historical
pre-upgrade days absent. This is a separate attendance summary, not a fabricated
approved leave application. Automatic dates are also included in the existing
employee/admin leave totals and as zero-billable-day rows in site activity Excel.
Generated visit billing remains unchanged.

Visit controls are now **Start Site** and **Stop Site** (including location and
selfie confirmation screens).

### Android outside-site reminders

Install the updated bundled APK (1.3.0 / versionCode 5). While a site is active,
the Android app uses a visible foreground location service and checks once per
minute against the site's saved reference coordinates and radius. Outside-site
notifications remind the employee to use **Stop Site**. The browser does not
send these reminders. The app must have precise location and notification
permission, and location services must be enabled.

Monitoring continues in the background/sign-out while the visit is active and
stops when the server confirms the site stopped or at the visit's 23:59 IST
cutoff. Its private, signed, per-visit monitoring capability expires at cutoff;
it cannot download reports, access account data or submit attendance. It is
sent only as an HTTPS Authorization header, not as a URL query parameter.
API responses are not cacheable. New visits snapshot the radius as well as the
coordinates; legacy active visits use their site's existing reference.

Android battery restrictions, force-stop, permission revocation, unavailable
GPS/network and device power-off can interrupt tracking or notifications, so
exact once-per-minute delivery is not guaranteed. Physical-device acceptance
testing is required before employee distribution; the bundled APK remains
development-signed for testing. See [Android instructions](android/README.md).

### Leave applications

Apply the latest migration (`flask --app wsgi db upgrade`) before starting the updated app.
Employees can open **My leave** or the leave section on their dashboard to apply for
full-day date ranges or a single morning/afternoon half day, with a required reason.
Leave dates use IST; full-day ranges include weekends. Employee past start
dates and overlapping pending/approved requests are rejected. Opposite half days
on the same date are allowed.

Administrators can add dated leave records (including past dates), edit dates,
full/half-day duration and reasons, and separately approve/reject pending requests
under **Leave**. A changed record resets its decision/reviewer to pending; an
unchanged save preserves the decision. Ownership cannot be changed. All changes
and decisions are audited; employees see their status and reviewer.
Past requests may be approved; inactive employees' requests cannot. Full-day
leave for today cannot be approved while the employee has an active site, including
approval of a second half day that would complete a full day. Employees
with leave history are deactivated rather than deleted.

Both roles can filter the leave page by an inclusive date range and see **Total
leaves taken (days)**. Totals include only approved days within the range through
today (IST), counting half days as 0.5 and including weekends. Future days, pending
requests and rejections are excluded. Blank boundaries mean no date limit.
The request list includes requests overlapping the range and displays their full
duration; the total counts only the overlapping elapsed days. Admin status tabs
filter that list, not the approved-leave totals.

Admins can search employees by name or email, select an exact employee, and see
per-employee totals (including inactive employees' history). Employee searches
use literal text, not wildcard patterns. Employees can only view their own
requests and totals, regardless of employee filters supplied in a URL.

The **Leave allowance & carry-forward balance** section automatically credits
**2 days in the joining month**, then 2 days on the first of every following
India-calendar month. There is no joining-month proration. The joining date
defaults to the employee's account-creation date; admins can correct it to the
actual joining date and enter an optional opening unused-leave balance. Opening
days are credited once, in addition to monthly accrual; do not enter unused days
already covered by that accrual.

Admins can set a different monthly rate (0 to 31 days in 0.5-day increments)
effective from a selected month. The rate continues until the next rate change;
credits before that effective month are unchanged. Existing monthly entitlement
entries are retained and now act as effective-from rate changes, not isolated
one-month allowances. Joining/opening/rate changes are audited.

Both roles see **total leaves assigned**, **total leaves taken + absence** and
**remaining leaves**, plus month-by-month credit/deduction/closing-balance history
and rate changes. The dashboard shows cumulative totals through today; the leave
page allows a historical month, capped at today so future credits are not awarded
early. Unused days carry forward across every month and year, without expiry or
a yearly reset. For example, a January joiner at 2 days/month with no deductions
has 2 in January, 4 in February, 24 in December and **26 next January**.

Remaining = opening balance + monthly credits since joining - approved leave
through the reporting date - automatic absence through the daily cutoff. Future
approved leave is separately **reserved**, with **available after reservations**
also shown. Pending/rejected requests do not deduct days. Half-day deductions are
0.5; approved and automatic leave are not double-counted. Negative balances remain
visible; the allowance does not impose a new hard approval limit. Automatic
absence still starts only from the attendance policy activation date, not from
before the system tracked attendance.

These are allowance credits, not actual dated leave records. Actual leave
applications and their separate approval workflow remain available. These web
changes also appear in Android 1.3.0 without reinstalling. Apply migration
`17bc2d3e4f50` (`flask --app wsgi db upgrade`) before restarting the server.

The admin dashboard includes clickable **Pending leave requests** and **On leave
today** cards, refreshed with the existing 15-second live status update. The latter
counts distinct active employees with approved leave covering today's IST date,
including either half day (not a real-time morning/afternoon schedule). An employee
is shown as **On leave** unless they have an active site visit, which retains
**Onsite** status. Full-day approved leave blocks new site starts; half-day leave
does not. Existing attendance records are not changed by leave approval.

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
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_submitted_reports.py -v
node --test tests\location_workflow.test.js
node --test tests\login_visibility.test.js
```

## Public access

The former **Temporary global access** panel has been removed. Its start/stop
and status routes are unavailable, and the app no longer initializes or starts
a Cloudflare tunnel. Restart old app processes when upgrading so their owned
tunnels are closed. Use your deployed HTTPS domain for remote access instead.
Password-reset links use the public app address from Settings (or
`PUBLIC_BASE_URL`); configure it to your normal deployment address.

## Android app

The embedded Android client is a thin WebView shell around this web app, so screens,
features, roles and data always come from the server; updating the server updates
the app without reinstalling. Its server address is fixed at build time to
`https://worktrack.blackpurple.in` (`AppConfig.SERVER` in the Android sources, mirrored
by `ANDROID_SERVER_URL` in `geosite/__init__.py`). Users never enter an address. When
the server is reachable the app shows live data; otherwise it shows `-` and retries
automatically every 15 seconds (or on **Reload**/**Retry**).

See [Android build and installation instructions](android/README.md). Version 1.3.0
(versionCode 5) is bundled at `android/downloads/worktrack.apk`, so `git pull`
also obtains the APK. An administrator must still enable **Show "Download Android
app" on the login page** in **Settings**. A private
`instance/downloads/worktrack.apk` takes priority if present; replace an old private
copy with the new APK or move it to a backup name to use the bundled build.

The app opens the HTTPS login page without server setup and removes old saved
server addresses. The Android document picker supports uploading and replacing
PDFs in **Submitted reports**, without broad storage permission. Sign-out, Excel
day counts and version-history screens come from the updated server. The bundled
APK is development-signed for testing; use a private release signing key for
production. Android updates need the same signing key; if an older installation
uses a different key it must be uninstalled first. No Play Store account is required.

## Production serving

Use a production WSGI server rather than Flask's development server, for example:

```sh
gunicorn --workers 3 --bind 0.0.0.0:8000 wsgi:app
```

Serve behind an HTTPS reverse proxy. The UI loads Bootstrap, Leaflet, Chart.js, and map tiles from their public CDNs; provide an approved/self-hosted distribution if the deployment cannot access those services.
