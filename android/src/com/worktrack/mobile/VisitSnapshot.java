package com.worktrack.mobile;

import org.json.JSONObject;
import java.time.Instant;

final class VisitSnapshot {
    final long attendanceId;
    final double latitude;
    final double longitude;
    final double radius;
    final long cutoff;
    final String token;

    VisitSnapshot(long attendanceId, double latitude, double longitude, double radius,
            long cutoff, String token) {
        if (attendanceId <= 0 || !MonitorRules.validSite(latitude, longitude, radius)
                || cutoff <= 0 || token == null || token.isEmpty() || token.length() > 8192) {
            throw new IllegalArgumentException("Invalid visit snapshot");
        }
        this.attendanceId = attendanceId;
        this.latitude = latitude;
        this.longitude = longitude;
        this.radius = radius;
        this.cutoff = cutoff;
        this.token = token;
    }

    static VisitSnapshot parse(JSONObject json, String existingToken) throws Exception {
        if (!json.getBoolean("active")) return null;
        String token = json.optString("monitoring_token", existingToken);
        return new VisitSnapshot(json.getLong("attendance_id"), json.getDouble("latitude"),
            json.getDouble("longitude"), json.getDouble("radius_m"),
            Instant.parse(json.getString("cutoff_time")).toEpochMilli(), token);
    }

    boolean sameVisit(VisitSnapshot other) {
        return other != null && attendanceId == other.attendanceId
            && Double.compare(latitude, other.latitude) == 0
            && Double.compare(longitude, other.longitude) == 0
            && Double.compare(radius, other.radius) == 0 && cutoff == other.cutoff;
    }
}
