package com.worktrack.mobile;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URL;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

final class MonitorApi {
    static final class Result {
        final int status;
        final VisitSnapshot visit;

        Result(int status, VisitSnapshot visit) {
            this.status = status;
            this.visit = visit;
        }
    }

    private MonitorApi() {}

    static Result activeVisit(String cookie) throws Exception {
        return get("/api/employee/active-visit", cookie, null);
    }

    static Result monitor(String token) throws Exception {
        return get("/api/visit-monitor", null, token);
    }

    private static Result get(String path, String cookie, String token) throws Exception {
        HttpsURLConnection connection = (HttpsURLConnection) new URL(AppConfig.SERVER + path).openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(10_000);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Cache-Control", "no-store");
        if (token != null) connection.setRequestProperty("Authorization", "Bearer " + token);
        if (cookie != null && !cookie.isEmpty()) connection.setRequestProperty("Cookie", cookie);
        try {
            int status = connection.getResponseCode();
            if (status != 200) return new Result(status, null);
            try (InputStream input = connection.getInputStream();
                    ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                long deadline = System.nanoTime() + 20_000_000_000L;
                byte[] buffer = new byte[2048];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (System.nanoTime() >= deadline) throw new java.io.IOException("Response timed out");
                    if (output.size() + count > 32768) throw new IllegalArgumentException("Response too large");
                    output.write(buffer, 0, count);
                }
                return new Result(status, VisitSnapshot.parse(
                    new JSONObject(output.toString("UTF-8")), token));
            }
        } finally {
            connection.disconnect();
        }
    }
}
