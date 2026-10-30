# WorkTrack Android

Native Java WebView client for Android 8.0+ (API 26), targeting API 35. It uses the
existing web app, so employee/admin roles, leave totals, maps, attendance,
live selfies, printable offline sign-off reports, invoices and audit history use the same server.
Internet access and an updated Android System WebView/Chrome are required.

## Build on Windows

Requires JDK 17+, Android SDK platform 35 and build-tools 35.0.0. Install official
components with Android Studio or Google's command-line tools after reviewing
the Android SDK license. No Gradle, Node dependencies or external build service
are used. Java, javac, jar and keytool must be on PATH.

From the repository root:

```powershell
.\android\build.ps1 -SdkRoot "$env:LOCALAPPDATA\WorkTrackAndroid\sdk"
```

The script compiles resources/Java/DEX, aligns the APK, signs it, verifies its
signature and publishes `instance/downloads/worktrack.apk` for Flask to serve.
Build intermediates and signing keys are not committed.

To also update the distributable included in this repository:

```powershell
.\android\build.ps1 -PublishToRepository
```

This writes `android/downloads/worktrack.apk`, which is downloaded to EC2 by
`git pull`. The included version 1.5.0 (versionCode 7) adds native robot speech
and is development-signed for testing, not a production release. Never commit signing keys.

By default this creates a **development-signed APK for testing**, with a local
development keystore under `%LOCALAPPDATA%\WorkTrackAndroid\keys`. Before real
distribution, use a private release keystore and secure password environment
variables; never commit keys/passwords or share them with a build service:

```powershell
# Set WORKTRACK_STORE_PASSWORD and WORKTRACK_KEY_PASSWORD securely in your shell.
.\android\build.ps1 -SdkRoot "C:\Android\Sdk" -KeyStore "C:\Private\worktrack-release.jks" -KeyAlias "worktrack"
```

Keep the release key backed up privately. Android updates require the same
package/signing key and an increased versionCode in AndroidManifest.xml. Moving
from a development key to a release key requires uninstalling the development app.

## Fixed server

The app always opens `AppConfig.SERVER` (`https://worktrack.blackpurple.in`), defined in
`src/com/worktrack/mobile/AppConfig.java`. There is no setup screen or server switch;
all screens and data come from that server, so web updates appear without reinstalling.
If the server cannot be reached (no internet, DNS/connection failure or a 502-504 proxy
error), the app shows `-` with a **Retry** button and retries every 15 seconds.

To change the address (for example to HTTPS), edit `AppConfig.SERVER` and
`ANDROID_SERVER_URL` in `geosite/__init__.py`, increase `versionCode` in
`AndroidManifest.xml`, rebuild and redistribute the APK.

## Publish and install

1. Apply Flask migrations on the server: `python -m flask --app wsgi db upgrade`.
2. Pull the repository to get the bundled `android/downloads/worktrack.apk`.
   A private `instance/downloads/worktrack.apk` takes priority if it exists:
   replace an old private APK with the new build or move it to a backup name.
   Alternatively publish your own signed APK at that private path.
3. Admin **Settings**: tick **Show "Download Android app" on the login page** and save.
   No download button appears if the switch is off or the APK is missing.
4. Users download on the login page and permit APK installation from their browser.

Camera and precise GPS are requested only when the fixed server uses HTTPS; over
HTTP they are denied, and requests from other origins are never permitted. WebView
HTTP requests are restricted to the fixed origin, invalid certificates and mixed
content are blocked, and maps/external HTTPS links open separately.
Server-authenticated PDF/Excel downloads pass the session cookie to Android's
Download Manager and use app-specific external storage, without broad storage
permission. Open completed files from Android's download notification/Downloads;
these files are removed when the app is uninstalled. Blob/data downloads are not
supported.

PDF upload/replacement uses Android's document picker restricted to PDF files.
Cancelling returns to the form without changing its file. The server validates
the PDF contents and size. No general device storage permission is requested.

## English report voice typing

The employee Activity detail field has a **Start microphone** button. Install
version 1.4.0 or later to use the native Android speech-input dialog; a server-only
update cannot add it to older WebViews. Enable an Android speech-input provider
that supports English (India). It may require internet access and may process
audio online. WorkTrack does not record or store audio, request microphone
permission itself, or require a speech API account/key.

Only a main-frame, user-initiated `worktrack-voice://start` navigation from the
fixed trusted server can launch speech input. Results return as safely encoded
text only to the same page/navigation. Cancelling or missing speech services
shows a message without replacing the employee's text. No JavaScript interface
is exposed to web frames. New speech is appended; employees can edit it or type
normally before submitting. Hindi/Gujarati translation is not included.

## Robot voice

Version 1.5.0 adds Android text-to-speech for the login and report-completion robot.
Rebuild and install this APK as well as deploying the web changes; old WebViews
may not provide browser speech synthesis. Enable an English Android TTS voice and
turn up media volume. No microphone permission or speech API key is needed.
Robot speech uses media audio, does not override device volume, and stops on
skip, navigation, backgrounding or Activity destruction.

The robot attempts voice on every employee login, even on the same day, and
closes when speech finishes. Failed or unavailable speech leaves **Play voice**
and **Skip robot** available rather than silently dismissing it. Only main-frame
`worktrack-robot://speak` / `stop` requests from the fixed trusted server are
accepted. Bounded text is passed to the device TTS engine; safely encoded progress
events return only to the requesting navigation. No JavaScript interface is
exposed to frames.

## Android active-visit outside-site reminders

The app polls the authenticated native API after same-origin page navigation and
every 10 seconds while its Activity is visible (including AJAX site actions). It
does not scrape HTML, inject scripts or expose a JavaScript bridge. An active
employee visit starts a **visible location foreground service**, only while the
Activity is visible and precise location and notification permissions are granted.
Android 14+ location foreground-service permissions/type are declared. No
background-location permission, boot receiver or scheduled auto-start is used.
The **Reminders** button retries permission/sync checks. Permission denial,
approximate-only location or disabled notification channels shows reminders OFF;
enable precise location, notifications and both channels in Android app settings.
The service also detects revoked permissions and stops with an explanatory status.

The service checks the visit and requests a fresh GPS/network fix once per minute.
It uses the server's immutable attendance snapshot coordinates and `radius_m`,
not the site's subsequently editable settings. Only a fresh non-mock fix whose
accuracy circle lies completely outside the allowed radius produces a reminder.
Inside, boundary-overlapping/uncertain and stale fixes never produce an outside
reminder. GPS disabled/unavailable, poor accuracy and network outages are explained
in the persistent notification; reminders are paused rather than using stale fixes.
No coordinates are uploaded or shared. The persistent tracking notification is
private on the lock screen, opens WorkTrack and remains visible while monitoring.
Outside reminders repeat on the minute cadence and expire within a minute.
Fixes predating the current location request and HTTP responses older than 30
seconds are rejected. Activity responses from before a page navigation/account
cookie change are discarded; old service responses cannot override a new visit.

Logging out or an expired login session **does not stop an existing active visit**.
The private service uses a visit-scoped bearer capability, not session cookies,
to detect a stopped visit while backgrounded/signed out. A successful inactive
response from the authenticated endpoint stops existing monitoring (including
after switching to another employee account). The service checks remote stop once
per minute when reachable; it suppresses outside reminders if remote status cannot
be verified. A monitor authorization failure stops monitoring and asks the user to
open WorkTrack. Tokens are held only in process memory, never logged, put in URLs,
backed up, persisted or exposed to other apps. The service is not exported.

The server's UTC `cutoff_time` represents the IST daily cutoff; device timezone is
irrelevant. Every cycle/fix checks it locally, with a monotonic deadline as a
second bound against moving the device clock backwards. A cutoff-bounded partial
wake lock supports minute checks with the screen off and is released on stop.
Cutoff, inactive response or authorization failure clears location requests and
outside alerts and removes the foreground notification. Returning to the foreground
resynchronizes an active visit; there is no tracking when no visit is active.

**Limitations:** device/OEM battery restrictions, Doze, connectivity and GPS quality
can delay checks; exact once-per-minute delivery is not an Android guarantee.
If stop occurs offline, monitoring cannot learn about it until reconnection, but
outside alerts stay paused and the local cutoff still bounds the visit.
Force-stop, Android's active-app Stop, process termination and reboot can end the
service. It deliberately does not silently restart; reopen WorkTrack to resume.
Do not promise force-stop/reboot survival or use this as an attendance enforcement
guarantee. Continuous active-visit location/wake-lock use consumes battery.

### Native API contract

Both endpoints must be HTTPS, non-redirecting JSON responses with
`Cache-Control: no-store`; no login HTML:

- `GET /api/employee/active-visit`: WebView session cookie, returns
  `{"active":false}` or the active fields below plus `monitoring_token`.
  Unauthenticated returns 401 (not inactive). Authenticated employees without
  active visits return inactive, so changing employee accounts clears old tracking.
- `GET /api/visit-monitor`: `Authorization: Bearer <monitoring_token>`, **no
  cookie and no query-string token**. Returns inactive after stop, otherwise the
  same active fields, without a new token. Invalid/expired capability returns 401.
- Active fields: `active: true`, positive integer `attendance_id`, numeric
  `latitude`, `longitude`, positive numeric `radius_m`, and `cutoff_time` as
  ISO-8601 UTC (e.g. `2026-10-27T18:00:00Z`). All snapshot fields, including cutoff,
  must remain unchanged for a visit; unexpected changes stop monitoring.
- Capability is signed, scoped to attendance id and expires at that visit's
  cutoff. It authorizes only this minimal visit-status lookup, not employee data
  or changes. The server must check actual attendance stop status on every poll.
  Do not log authorization headers or capability values.

The build runs independent Java origin, geofence/accuracy/freshness and minute
schedule/cutoff-boundary tests before packaging. API/device acceptance remains
necessary; build/signature checks cannot validate Android runtime behavior.

## Device acceptance checks

Build/signature verification is not a substitute for physical-device testing.
Before distributing to employees, test Android 8+ and a recent Android device:

- First launch opens the fixed server directly; airplane mode/server stopped shows
  `-`, and it recovers automatically or with Retry once the server is back.
- Admin and employee login/logout, idle timeout and role-restricted pages.
- Allow/deny camera and precise location, selfie/retake, GPS verification,
  activity/billing report and completed checkout; repeat after backgrounding/rotation.
- Dashboard updates, address maps and external Google Maps navigation.
- Full/half-day leave, approval, totals/date ranges and employee-only visibility.
- Report/invoice PDF, merged PDF and Excel downloads/notifications/opening.
- Submitted reports: select/cancel a PDF, submit it, edit dates/man-days, replace
  the PDF and download both current and previous versions as owner/admin.
- Engineer assignment search, long names/emails wrapping, and scrolling on a
  small screen; sign out with an active visit and resume after signing back in.
- Start an employee visit: grant precise location and notifications; confirm
  visible persistent location notification, inside-site silence and an outside
  reminder once per minute beyond the configured radius (not a hardcoded radius).
- Repeat with screen locked/backgrounded and after logout/session expiration.
  Stop from another browser/device: service and alerts disappear within the next
  successful minute poll. Sign in as a different employee without an active visit:
  old monitoring stops. Sign in as one with a visit: snapshot switches.
- Edit site coordinates/radius during a visit: the current visit keeps its
  immutable snapshot. Test a new visit separately.
- Disable GPS, revoke precise permission, grant approximate only, deny
  notifications/disable either channel, use a stale/uncertain location and lose
  internet: verify clear unavailable/OFF status and no stale outside alerts.
  Restore settings, reopen WorkTrack/tap Reminders and check recovery.
- Cross the server's IST cutoff while signed out/backgrounded (also change device
  timezone and move its clock backwards): no later location/reminder, foreground
  notification clears. Verify expired/invalid monitor authorization fails safely.
- Swipe away the Activity, test OEM battery saver/Doze, Android active-app Stop,
  force-stop and reboot. Record actual behavior; reopen to resume active visits.
- Verify Android 14/15 service start while visible, permission denial without
  crashes/silent success, notifications private on the lock screen, and no token
  in request URLs, logs, exported components or downloads.
