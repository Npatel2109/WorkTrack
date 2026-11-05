# WorkTrack on Hostinger Single (PHP/Laravel)

This is the separate PHP replacement for the existing Flask application. EC2
is unchanged. Use PHP **8.3+** with PDO MySQL, mbstring, OpenSSL, tokenizer,
XML/DOM, fileinfo, GD, ZIP and intl enabled in hPanel. No Python/Node server,
Redis or persistent queue worker is required. Jobs run synchronously.

## Before switching

- Keep EC2 and its backups until all acceptance checks below pass.
- The imported MySQL database must contain the complete latest Flask schema:
  18 application tables, including `user` (not `users`), `attendance`,
  `submitted_report_version` and `attendance_policy`.
- Test against a separate MySQL copy, not the only production database.
- Copy **all EC2 `instance/uploads` files**, including selfies, signatures,
  submitted PDFs, resumes and logos, into private uploads. SQL backups do not
  contain these files. Copy the Android APK separately to private downloads.
- Record EC2 row counts and verify encoding, UTC datetimes, IDs, foreign keys
  and decimals after import. A schema check cannot prove data completeness.
- Flask sessions, password-reset links and Android monitoring tokens are
  invalidated. Users must sign in again. End active visits before final backup.

## Upload layout (preserves the main website)

Extract the ZIP into the domain directory, **not inside public_html**:

```text
domains/blackpurple.in/
  worktrack/                         PRIVATE Laravel code and vendor
    .env.example
    artisan
    bin/setup.php
    storage/app/private/uploads/
    storage/app/private/downloads/
  public_html/
    worktrack/                       subdomain document root ONLY
      index.php
      .htaccess
      static/
```

In hPanel configure `worktrack.blackpurple.in` with document root
`public_html/worktrack`. Do not change the main website root or email DNS.
The packaged front controller expects the sibling private directory above.
If the account layout differs, update its three private-directory references.
The source `public/index.php` remains standard Laravel for local development;
the build generates the shared-hosting front controller.

Never put the entire Laravel app, `.env`, SQL exports, setup scripts or private
storage in a publicly accessible directory.

## Configure and initialize

1. Copy private `worktrack/.env.example` to `worktrack/.env`.
2. Set `APP_URL=https://worktrack.blackpurple.in`, `APP_ENV=production`,
   `APP_DEBUG=false` and exact hPanel MySQL database/user/password (including
   account prefixes). Keep `SESSION_DRIVER=file`, `CACHE_STORE=file`,
   `QUEUE_CONNECTION=sync`, `SESSION_SECURE_COOKIE=true`.
3. Configure real SMTP values. Imported `app_settings` can supply SMTP too.
   Verify recovery email before rollout; logging mail is not delivery.
4. Make `storage` and `bootstrap/cache` writable by your account. Typically
   directories use 755, files 644 and private `.env` 600. Never use 777.
5. Run with hPanel's PHP 8.3+ CLI executable:

   ```sh
   php /home/ACCOUNT/domains/blackpurple.in/worktrack/bin/setup.php
   ```

   This generates APP_KEY, adopts a complete existing schema without replacing
   rows, creates the password-reset table if needed, and checks schema,
   relationships and private files. Partial imports fail explicitly.
   Never run `migrate:fresh`, `db:wipe`, seeders or automatic rollback against
   imported data. Empty-database schema creation is supported separately.
   Back up the generated `APP_KEY` privately and keep it stable after rollout.

   If Single has no SSH, run this through a temporary **hPanel custom cron
   job**, inspect the output, then remove that setup cron. There is deliberately
   no public browser installer. If PHP CLI cron is unavailable, obtain Hostinger
   assistance before cutover.
6. Configure permanent cron every minute (adjust PHP/account paths):

   ```cron
   * * * * * /usr/bin/php /home/ACCOUNT/domains/blackpurple.in/worktrack/artisan schedule:run >> /home/ACCOUNT/domains/blackpurple.in/worktrack/storage/logs/scheduler.log 2>&1
   ```

   Verify execution and monitor errors. Visits close at exactly 23:59 IST;
   authenticated requests catch up after downtime.

### Migrated passwords

Werkzeug PBKDF2-SHA256 passwords are verified and upgraded to bcrypt on login.
Werkzeug **scrypt accounts require password reset**: hashes cannot be converted
without plaintext passwords. Use SMTP Forgot password. With CLI access:

```sh
php artisan worktrack:reset-password admin@example.com
```

The new password is prompted privately, not passed on the command line.
For a genuinely empty installation only, `php artisan worktrack:create-admin`
creates the first administrator interactively. Imported admins are preserved.
Never put default/plaintext passwords in SQL or the ZIP.

## Acceptance and final cutover

Test on staging HTTPS with a separate database copy:

- Admin/employee login, recovery, inactive-account rejection, idle timeout,
  role/ownership checks and password-reset session revocation.
- Employees, optional personal fields, sites, projects and assignments.
- GPS start, outside-radius warning, live selfie, current/last-known stop,
  pending report, billing, voice typing and exactly-once completion.
- Android WebView login, native voice/robot, authenticated downloads and
  foreground reminders. Flask monitoring tokens do not survive migration.
- Full/half-day leave, overlaps, review/edit, active-visit blocking, work
  confirmations, automatic leave, joining balances and carry-forward.
- Historical PDFs/signatures, combined PDFs, SAR-R0 Excel, archived-site
  history, submitted PDF revisions and optimistic edit conflict.
- Purchase orders, invoices, statuses, referrals/resumes, SMTP and logo.
- `php artisan worktrack:database-check --files`, comparing every row count
  with EC2's final backup. Validate on MySQL, not only SQLite.
- Actual cron execution, 23:59 IST cutoff and physical Android-device testing.

Resolve failures before cutover. Pause EC2 writes, take final database/uploads
backups, import/sync that state, repeat validation, then change **only the
`worktrack` A record** to the Hostinger IPv4 address shown in hPanel.
Remove conflicting worktrack AAAA records if present; do not copy the main
domain's IPv6 blindly. Enable and verify Hostinger HTTPS first.
During DNS propagation keep the old app maintenance/read-only, not accepting
divergent writes. Keep `https://worktrack.blackpurple.in` for the installed
Android fixed server address; a staging domain does not redirect that app.

Decommission EC2 only after stable verified rollout and an independent,
restore-tested backup. A post-cutover rollback must reconcile new MySQL
rows/uploads; simply changing DNS back would lose new writes.

## Verify and build

```sh
composer install
php artisan test
php scripts/build.php /path/to/composer.phar
```

Output: `dist/worktrack-hostinger.zip` and a SHA256 manifest. The build installs
locked production dependencies in isolated temporary staging and includes
prebuilt CSS/JS and the Excel template. No npm build is required.
Credentials, database rows, logs and private uploads are excluded.

Local tests do not prove that the real Hostinger account, imported data, SMTP,
HTTPS, cron or physical Android device is working.
Tests create/drop schema: run them only against disposable databases, never
the transferred production database. To exercise the MySQL dialect locally,
use a dedicated empty MySQL/MariaDB database and run the suite with
`--exclude-group=schema-ddl`; that excluded test deliberately drops a table and
is covered by the normal SQLite test run.
