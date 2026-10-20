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

## Publish and install

1. Apply Flask migrations: `.\.venv\Scripts\python.exe -m flask --app wsgi db upgrade`.
2. Deploy the server using a public HTTPS origin and a trusted certificate.
3. Copy the signed APK to `instance/downloads/worktrack.apk` on that server.
4. Admin **Settings**: save this server's HTTPS origin or local private IPv4 HTTP
   test origin (for example `http://192.168.1.6:5000`). Blank disables
   the download. No download button appears if either the setting or APK is missing.
5. Users download on the login page and permit APK installation from their browser.
6. On first launch, users enter the exact server origin displayed on the login
   page. The universal APK does not embed an administrator address. Use **Server**
   to change it later; this clears cookies/cache/web storage and signs out.

Camera and precise GPS permissions are requested only when the configured HTTPS
origin asks for them. Invalid certificates, mixed content and camera/GPS requests
from other origins are not permitted. Local HTTP test mode requires confirmation
and displays a persistent warning: passwords/data are unencrypted, and camera/GPS
are unavailable. Only RFC1918 private IPv4 HTTP server addresses are accepted;
public HTTP domains/IPs are rejected. Cleartext support is enabled in the manifest
for dynamic local servers, with WebView HTTP requests restricted to the configured
origin. Use trusted Wi-Fi and test accounts only. Maps/external HTTPS links open separately.
Server-authenticated PDF/Excel downloads pass the session cookie to Android's
Download Manager and use app-specific external storage, without broad storage
permission. Open completed files from Android's download notification/Downloads;
these files are removed when the app is uninstalled. Blob/data downloads are not
supported (the existing server serves files via HTTPS).

## Device acceptance checks

Build/signature verification is not a substitute for physical-device testing.
Before distributing to employees, test Android 8+ and a recent Android device:

- Setup, wrong/unreachable server, offline/reload and invalid-certificate failure.
- Local HTTP setup confirmation/warning, same-Wi-Fi login/leave/report testing,
  and switching back to an HTTPS domain with the Server button.
- Admin and employee login/logout, idle timeout and role-restricted pages.
- Allow/deny camera and precise location, selfie/retake, GPS verification,
  activity/billing report and completed checkout; repeat after backgrounding/rotation.
- Dashboard updates, address maps and external Google Maps navigation.
- Full/half-day leave, approval, totals/date ranges and employee-only visibility.
- Report/invoice PDF, merged PDF and Excel downloads/notifications/opening.
- Server switching clears the old login; re-login and download work afterward.
