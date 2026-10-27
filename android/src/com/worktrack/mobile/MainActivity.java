package com.worktrack.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Build;
import android.os.SystemClock;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayInputStream;
import java.util.ArrayList;
import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int CAMERA_REQUEST = 10;
    private static final int LOCATION_REQUEST = 11;
    private static final int FILE_REQUEST = 12;
    private static final int MONITOR_PERMISSION_REQUEST = 13;
    private final String server = AppConfig.SERVER;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable retry = this::retryConnection;
    private WebView webView;
    private LinearLayout root;
    private LinearLayout offline;
    private TextView status;
    private PermissionRequest cameraRequest;
    private GeolocationPermissions.Callback locationCallback;
    private String locationOrigin;
    private String lastUrl;
    private boolean pageFailed;
    private ValueCallback<Uri[]> fileCallback;
    private final ExecutorService monitorNetwork = Executors.newSingleThreadExecutor();
    private TextView monitorStatus;
    private VisitSnapshot pendingVisit;
    private boolean foreground;
    private boolean monitorSyncRunning;
    private boolean monitorPermissionAsked;
    private boolean monitorPermissionPending;
    private boolean monitorDestroyed;
    private int navigationGeneration;
    private final Runnable monitorPoll = new Runnable() {
        @Override public void run() {
            if (!foreground || monitorDestroyed) return;
            syncVisit();
            handler.postDelayed(this, 10_000);
        }
    };

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        // Older builds let users type a server address; it is now fixed.
        getSharedPreferences("worktrack", MODE_PRIVATE).edit().remove("server").apply();
        showApp();
    }

    private Button button(String text, View.OnClickListener action) {
        Button button = new Button(this);
        button.setText(text);
        button.setOnClickListener(action);
        return button;
    }

    private void makeRoot() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        setContentView(root);
        root.requestApplyInsets();
    }

    private boolean sameOrigin(String value) {
        return ServerAddress.sameOrigin(server, value);
    }

    private void openExternal(String url) {
        if (!"https".equals(Uri.parse(url).getScheme())) {
            Toast.makeText(this, "Only secure HTTPS links can be opened.", Toast.LENGTH_LONG).show();
            return;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (ActivityNotFoundException error) {
            Toast.makeText(this, "Install a browser or maps app to open this link.", Toast.LENGTH_LONG).show();
        }
    }

    private void showApp() {
        makeRoot();
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.addView(button("Back", view -> {
            if (webView.canGoBack()) webView.goBack();
        }));
        toolbar.addView(button("Reload", view -> retryConnection()));
        toolbar.addView(button("Reminders", view -> {
            monitorPermissionAsked = false;
            syncVisit();
        }));
        root.addView(toolbar);
        status = new TextView(this);
        status.setPadding(12, 4, 12, 4);
        status.setText("Loading WorkTrack...");
        root.addView(status);
        monitorStatus = new TextView(this);
        monitorStatus.setPadding(12, 4, 12, 4);
        monitorStatus.setText(VisitMonitorService.status());
        root.addView(monitorStatus);
        FrameLayout content = new FrameLayout(this);
        webView = new WebView(this);
        content.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        offline = offlineView();
        content.addView(offline, new FrameLayout.LayoutParams(-1, -1));
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setGeolocationEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if ("http".equals(request.getUrl().getScheme()) && !sameOrigin(url)) {
                    return new WebResourceResponse("text/plain", "UTF-8", 403,
                        "HTTP blocked", java.util.Collections.emptyMap(),
                        new ByteArrayInputStream("Only the WorkTrack server is allowed.".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
                }
                return null;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                navigationGeneration++;
                pageFailed = false;
                if (sameOrigin(url)) lastUrl = url;
                status.setText("Loading WorkTrack...");
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (sameOrigin(url)) return false;
                openExternal(url);
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (pageFailed) showOffline();
                else showOnline();
                CookieManager.getInstance().flush();
                if (sameOrigin(url)) syncVisit();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    pageFailed = true;
                    showOffline();
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                // Proxy/server outages (502/503/504) mean the server is not reachable.
                if (request.isForMainFrame() && response.getStatusCode() >= 502 && response.getStatusCode() <= 504) {
                    pageFailed = true;
                    showOffline();
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                pageFailed = true;
                showOffline();
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                    FileChooserParams params) {
                if (!sameOrigin(view.getUrl())) return false;
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("application/pdf");
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                try {
                    startActivityForResult(intent, FILE_REQUEST);
                } catch (ActivityNotFoundException error) {
                    fileCallback.onReceiveValue(null);
                    fileCallback = null;
                    Toast.makeText(MainActivity.this, "Install or enable a document picker to select your PDF report.", Toast.LENGTH_LONG).show();
                }
                return true;
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> {
                    boolean cameraOnly = request.getResources().length == 1
                        && PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(request.getResources()[0]);
                    if (!server.startsWith("https:") || !sameOrigin(request.getOrigin().toString()) || !cameraOnly || cameraRequest != null) {
                        request.deny();
                        return;
                    }
                    if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                        request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                    } else {
                        cameraRequest = request;
                        requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_REQUEST);
                    }
                });
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                if (cameraRequest == request) cameraRequest = null;
            }

            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                if (!server.startsWith("https:") || !sameOrigin(origin) || locationCallback != null) {
                    callback.invoke(origin, false, false);
                    return;
                }
                if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                    callback.invoke(origin, true, false);
                } else {
                    locationOrigin = origin;
                    locationCallback = callback;
                    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION}, LOCATION_REQUEST);
                }
            }

            @Override
            public void onGeolocationPermissionsHidePrompt() {
                locationCallback = null;
                locationOrigin = null;
            }

            @Override
            public boolean onCreateWindow(WebView view, boolean dialog, boolean gesture, android.os.Message result) {
                if (!gesture) return false;
                WebView popup = new WebView(MainActivity.this);
                popup.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView child, WebResourceRequest request) {
                        String url = request.getUrl().toString();
                        if (sameOrigin(url)) webView.loadUrl(url);
                        else openExternal(url);
                        child.destroy();
                        return true;
                    }
                });
                ((WebView.WebViewTransport) result.obj).setWebView(popup);
                result.sendToTarget();
                return true;
            }
        });
        webView.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            if (!sameOrigin(url)) {
                Toast.makeText(this, "Download blocked: files must come from your WorkTrack server.", Toast.LENGTH_LONG).show();
                return;
            }
            String fileName = URLUtil.guessFileName(url, disposition, mime)
                .replaceAll("[^a-zA-Z0-9._-]", "_");
            DownloadManager.Request download = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) download.addRequestHeader("Cookie", cookie);
            download.addRequestHeader("User-Agent", userAgent);
            download.setMimeType(mime);
            download.setTitle(fileName);
            download.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            download.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS,
                System.currentTimeMillis() + "-" + fileName);
            try {
                ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(download);
                Toast.makeText(this, "Downloading " + fileName + ". Open it from Android Downloads.", Toast.LENGTH_LONG).show();
            } catch (IllegalArgumentException | IllegalStateException | SecurityException error) {
                new AlertDialog.Builder(this).setTitle("Download failed")
                    .setMessage("Android could not start the download. Check available storage and that Download Manager is enabled.")
                    .setPositiveButton("OK", null).show();
            }
        });
        webView.loadUrl(server + "/login");
    }

    private LinearLayout offlineView() {
        LinearLayout view = new LinearLayout(this);
        view.setOrientation(LinearLayout.VERTICAL);
        view.setGravity(Gravity.CENTER);
        view.setBackgroundColor(Color.WHITE);
        view.setVisibility(View.GONE);
        TextView dash = new TextView(this);
        dash.setText(AppConfig.OFFLINE_TEXT);
        dash.setTextSize(64);
        dash.setGravity(Gravity.CENTER);
        dash.setContentDescription("Server not reachable");
        view.addView(dash);
        view.addView(button("Retry", button -> retryConnection()));
        return view;
    }

    private void showOffline() {
        if (offline == null) return;
        offline.setVisibility(View.VISIBLE);
        status.setText(AppConfig.OFFLINE_TEXT);
        handler.removeCallbacks(retry);
        handler.postDelayed(retry, AppConfig.RETRY_MILLIS);
    }

    private void showOnline() {
        handler.removeCallbacks(retry);
        if (offline != null) offline.setVisibility(View.GONE);
        status.setText("WorkTrack");
    }

    private void retryConnection() {
        handler.removeCallbacks(retry);
        if (webView == null) return;
        status.setText("Loading WorkTrack...");
        webView.loadUrl(lastUrl != null ? lastUrl : server + "/login");
    }

    private void syncVisit() {
        if (!foreground || monitorDestroyed || monitorSyncRunning || !sameOrigin(webView.getUrl())) return;
        monitorStatus.setText(VisitMonitorService.status());
        final String cookie = CookieManager.getInstance().getCookie(server + "/api/employee/active-visit");
        final int requestNavigation = navigationGeneration;
        final long requestStarted = SystemClock.elapsedRealtime();
        monitorSyncRunning = true;
        monitorNetwork.execute(() -> {
            MonitorApi.Result result = null;
            try {
                result = MonitorApi.activeVisit(cookie);
            } catch (Exception ignored) {
                // API failures must not be interpreted as a stopped visit or logged with credentials.
            }
            final MonitorApi.Result response = result;
            handler.post(() -> {
                monitorSyncRunning = false;
                if (monitorDestroyed || !foreground || !sameOrigin(webView.getUrl())
                        || requestNavigation != navigationGeneration
                        || !MonitorRules.freshResponse(requestStarted, SystemClock.elapsedRealtime())
                        || !Objects.equals(cookie, CookieManager.getInstance()
                            .getCookie(server + "/api/employee/active-visit"))) return;
                if (response == null || response.status != 200) {
                    pendingVisit = null;
                    monitorStatus.setText(VisitMonitorService.status()
                        + " Visit sync unavailable; an existing monitor continues until stop/cutoff.");
                    return;
                }
                pendingVisit = response.visit;
                if (pendingVisit == null || pendingVisit.cutoff <= System.currentTimeMillis()) {
                    VisitMonitorService.stopForInactiveVisit(this);
                    monitorStatus.setText("No active visit monitoring.");
                    return;
                }
                ensureVisitMonitoring();
            });
        });
    }

    private void ensureVisitMonitoring() {
        if (!foreground || pendingVisit == null || monitorPermissionPending) return;
        ArrayList<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.ACCESS_FINE_LOCATION);
            missing.add(Manifest.permission.ACCESS_COARSE_LOCATION);
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            monitorStatus.setText("Active visit: reminders OFF. Allow precise location and notifications. Tap Reminders to retry.");
            // Avoid overlapping the WebView's location/camera permission prompts.
            if (!monitorPermissionAsked && locationCallback == null && cameraRequest == null) {
                monitorPermissionAsked = true;
                monitorPermissionPending = true;
                new AlertDialog.Builder(this).setTitle("Active visit reminders")
                    .setMessage("WorkTrack uses precise location in a visible foreground service during your active visit, even after sign-out. It checks once a minute and reminds you only outside the site's allowed radius. Location stays on this device. Stop the visit to stop monitoring; it also stops at the daily cutoff.")
                    .setPositiveButton("Continue", (dialog, which) -> {
                        if (foreground) {
                            requestPermissions(missing.toArray(new String[0]), MONITOR_PERMISSION_REQUEST);
                        } else {
                            monitorPermissionPending = false;
                        }
                    })
                    .setNegativeButton("Not now", (dialog, which) -> monitorPermissionPending = false)
                    .setOnCancelListener(dialog -> monitorPermissionPending = false).show();
            }
            return;
        }
        NotificationManager notifications = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (!notifications.areNotificationsEnabled()
                || (notifications.getNotificationChannel("visit_tracking") != null
                    && notifications.getNotificationChannel("visit_tracking").getImportance() == NotificationManager.IMPORTANCE_NONE)
                || (notifications.getNotificationChannel("outside_site") != null
                    && notifications.getNotificationChannel("outside_site").getImportance() == NotificationManager.IMPORTANCE_NONE)) {
            monitorStatus.setText("Active visit: reminders OFF. Enable WorkTrack notifications and both reminder/tracking channels in Android settings.");
            return;
        }
        try {
            startForegroundService(VisitMonitorService.intent(this, pendingVisit));
            monitorStatus.setText(VisitMonitorService.status());
        } catch (SecurityException | IllegalStateException error) {
            monitorStatus.setText("Active visit: reminders OFF. Keep WorkTrack open and enable precise location and notifications in Android settings.");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            Uri uri = resultCode == RESULT_OK && data != null ? data.getData() : null;
            boolean allowed = uri != null && "content".equals(uri.getScheme())
                && sameOrigin(webView.getUrl());
            fileCallback.onReceiveValue(allowed ? new Uri[]{uri} : null);
            fileCallback = null;
            if (uri != null && !allowed) {
                Toast.makeText(this, "Choose a PDF using the Android document picker.", Toast.LENGTH_LONG).show();
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == MONITOR_PERMISSION_REQUEST) {
            monitorPermissionPending = false;
            // Refresh the authenticated visit after the dialog; never start from a stale account snapshot.
            syncVisit();
        }
        if (code == CAMERA_REQUEST && cameraRequest != null) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED
                && sameOrigin(cameraRequest.getOrigin().toString())) {
                cameraRequest.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            } else {
                cameraRequest.deny();
                Toast.makeText(this, "Camera permission is required for live selfies. Enable it in Android app settings.", Toast.LENGTH_LONG).show();
            }
            cameraRequest = null;
        }
        if (code == LOCATION_REQUEST && locationCallback != null) {
            boolean allowed = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
            locationCallback.invoke(locationOrigin, allowed, false);
            locationCallback = null;
            locationOrigin = null;
            if (!allowed) Toast.makeText(this, "Allow precise location in Android app settings, then retry GPS verification.", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onPause() {
        foreground = false;
        handler.removeCallbacks(monitorPoll);
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
        foreground = true;
        handler.removeCallbacks(monitorPoll);
        handler.post(monitorPoll);
    }

    @Override
    protected void onDestroy() {
        monitorDestroyed = true;
        handler.removeCallbacks(monitorPoll);
        monitorNetwork.shutdownNow();
        handler.removeCallbacks(retry);
        if (cameraRequest != null) cameraRequest.deny();
        if (locationCallback != null) locationCallback.invoke(locationOrigin, false, false);
        if (fileCallback != null) {
            fileCallback.onReceiveValue(null);
            fileCallback = null;
        }
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
