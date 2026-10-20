package com.worktrack.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayInputStream;

public class MainActivity extends Activity {
    private static final int CAMERA_REQUEST = 10;
    private static final int LOCATION_REQUEST = 11;
    private SharedPreferences preferences;
    private WebView webView;
    private LinearLayout root;
    private TextView status;
    private String server;
    private PermissionRequest cameraRequest;
    private GeolocationPermissions.Callback locationCallback;
    private String locationOrigin;
    private boolean pageFailed;

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences("worktrack", MODE_PRIVATE);
        server = preferences.getString("server", "");
        if (server.isEmpty()) showSetup();
        else showApp();
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

    private String validatedServer(String input) {
        return ServerAddress.validate(input);
    }

    private void showSetup() {
        makeRoot();
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(padding, padding, padding, padding);
        TextView title = new TextView(this);
        title.setText("Welcome to WorkTrack");
        title.setTextSize(24);
        form.addView(title);
        TextView help = new TextView(this);
        help.setText("Enter the HTTPS domain or local HTTP address shown on your administrator's login page. "
            + "Local HTTP is for trusted same-Wi-Fi testing only: passwords/data are unencrypted and camera/GPS require HTTPS.");
        form.addView(help);
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT
            | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://worktrack.example.com");
        input.setText(server);
        form.addView(input);
        form.addView(button("Connect", view -> {
            String candidate = validatedServer(input.getText().toString());
            if (candidate == null) {
                input.setError("Use an HTTPS domain or a private IPv4 HTTP address, without a path, query or credentials.");
                return;
            }
            if (candidate.startsWith("http:")) {
                new AlertDialog.Builder(this).setTitle("Unencrypted local test connection")
                    .setMessage("Passwords and data will travel unencrypted on this network. Camera/GPS workflows require HTTPS. Use only trusted Wi-Fi and test accounts.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Connect for testing", (dialog, which) -> connect(candidate)).show();
                return;
            }
            connect(candidate);
        }));
        root.addView(form);
    }

    private void connect(String candidate) {
        server = candidate;
        preferences.edit().putString("server", server).apply();
        showApp();
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
        toolbar.addView(button("Reload", view -> webView.reload()));
        toolbar.addView(button("Server", view -> new AlertDialog.Builder(this)
            .setTitle("Change server?")
            .setMessage("This signs you out and clears local web data. Downloads are kept.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Change", (dialog, which) -> changeServer()).show()));
        root.addView(toolbar);
        if (server.startsWith("http:")) {
            TextView warning = new TextView(this);
            warning.setText("LOCAL HTTP TEST: unencrypted. Camera/GPS require HTTPS.");
            warning.setTextColor(Color.RED);
            root.addView(warning);
        }
        status = new TextView(this);
        status.setPadding(12, 4, 12, 4);
        status.setText("Loading WorkTrack...");
        root.addView(status);
        webView = new WebView(this);
        root.addView(webView, new LinearLayout.LayoutParams(-1, 0, 1));
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
                        new ByteArrayInputStream("Only the configured local HTTP server is allowed.".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
                }
                return null;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                pageFailed = false;
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
                if (!pageFailed) status.setText("WorkTrack");
                CookieManager.getInstance().flush();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    pageFailed = true;
                    status.setText("Could not connect. Check internet/server settings, then tap Reload.");
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                pageFailed = true;
                status.setText("Untrusted HTTPS certificate. Ask your administrator to fix the server certificate.");
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
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

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
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

    private void changeServer() {
        if (cameraRequest != null) {
            cameraRequest.deny();
            cameraRequest = null;
        }
        if (locationCallback != null) {
            locationCallback.invoke(locationOrigin, false, false);
            locationCallback = null;
        }
        webView.stopLoading();
        webView.clearHistory();
        webView.clearCache(true);
        webView.destroy();
        webView = null;
        android.webkit.WebStorage.getInstance().deleteAllData();
        GeolocationPermissions.getInstance().clearAll();
        CookieManager.getInstance().removeAllCookies(value -> {
            CookieManager.getInstance().flush();
            preferences.edit().remove("server").apply();
            server = "";
            showSetup();
        });
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onDestroy() {
        if (cameraRequest != null) cameraRequest.deny();
        if (locationCallback != null) locationCallback.invoke(locationOrigin, false, false);
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
