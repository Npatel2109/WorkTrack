package com.worktrack.mobile;

final class AppConfig {
    private AppConfig() {}

    // Fixed WorkTrack server. Must match ANDROID_SERVER_URL in geosite/__init__.py.
    static final String SERVER = "http://worktrack.blackpurple.in";
    static final String OFFLINE_TEXT = "-";
    static final long RETRY_MILLIS = 15000;
}
