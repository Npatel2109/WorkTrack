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
`git pull`. The included version 1.2.0 (versionCode 4) is development-signed for
testing, not a production release. Never commit signing keys.

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
