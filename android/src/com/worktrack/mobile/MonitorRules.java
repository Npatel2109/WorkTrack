package com.worktrack.mobile;

final class MonitorRules {
    static final long MINUTE_MS = 60_000;
    static final long FIX_TIMEOUT_MS = 25_000;
    static final long MAX_FIX_AGE_MS = 60_000;

    enum Position { INSIDE, OUTSIDE, UNCERTAIN }

    private MonitorRules() {}

    static boolean freshResponse(long started, long now) {
        return now >= started && now - started <= 30_000;
    }

    static boolean currentFix(long requestedNanos, long fixNanos, long nowNanos) {
        return fixNanos >= requestedNanos && fixNanos <= nowNanos
            && nowNanos - fixNanos <= MAX_FIX_AGE_MS * 1_000_000;
    }

    static boolean validSite(double latitude, double longitude, double radius) {
        return Double.isFinite(latitude) && latitude >= -90 && latitude <= 90
            && Double.isFinite(longitude) && longitude >= -180 && longitude <= 180
            && Double.isFinite(radius) && radius > 0;
    }

    static Position classify(double distance, double accuracy, double radius, long ageMs) {
        if (!Double.isFinite(distance) || distance < 0 || !Double.isFinite(accuracy)
                || accuracy < 0 || !Double.isFinite(radius) || radius <= 0
                || ageMs < 0 || ageMs > MAX_FIX_AGE_MS) return Position.UNCERTAIN;
        if (distance + accuracy <= radius) return Position.INSIDE;
        if (distance - accuracy > radius) return Position.OUTSIDE;
        return Position.UNCERTAIN;
    }

    static long nextDelay(long cycleStarted, long nowElapsed, long remainingMs) {
        return Math.max(0, Math.min(remainingMs,
            Math.max(0, MINUTE_MS - Math.max(0, nowElapsed - cycleStarted))));
    }
}
