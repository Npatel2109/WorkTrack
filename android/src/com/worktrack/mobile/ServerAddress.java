package com.worktrack.mobile;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

final class ServerAddress {
    private ServerAddress() {}

    static boolean isPrivateIpv4(String host) {
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) return false;
        int[] numbers = new int[4];
        for (int index = 0; index < parts.length; index++) {
            if (!parts[index].matches("0|[1-9][0-9]{0,2}")) return false;
            numbers[index] = Integer.parseInt(parts[index]);
            if (numbers[index] > 255) return false;
        }
        return numbers[0] == 10
            || (numbers[0] == 172 && numbers[1] >= 16 && numbers[1] <= 31)
            || (numbers[0] == 192 && numbers[1] == 168);
    }

    static String validate(String input) {
        try {
            URI uri = new URI(input.trim());
            String host = uri.getHost();
            String scheme = uri.getScheme();
            if (host == null || !host.contains(".")
                || host.equalsIgnoreCase("localhost.localdomain")
                || host.toLowerCase(Locale.ROOT).endsWith(".localhost")
                || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || !(uri.getPath().isEmpty() || uri.getPath().equals("/"))
                || uri.getPort() == 0 || uri.getPort() > 65535
                || (!"https".equals(scheme) && !"http".equals(scheme))
                || ("http".equals(scheme) && !isPrivateIpv4(host))) {
                return null;
            }
            return scheme + "://" + host.toLowerCase(Locale.ROOT)
                + (uri.getPort() == -1 ? "" : ":" + uri.getPort());
        } catch (URISyntaxException error) {
            return null;
        }
    }

    static boolean sameOrigin(String server, String value) {
        try {
            URI candidate = new URI(value);
            URI trusted = new URI(server);
            int defaultPort = "https".equals(trusted.getScheme()) ? 443 : 80;
            int port = candidate.getPort() == -1 ? defaultPort : candidate.getPort();
            int trustedPort = trusted.getPort() == -1 ? defaultPort : trusted.getPort();
            return trusted.getScheme().equals(candidate.getScheme()) && candidate.getUserInfo() == null
                && trusted.getHost().equalsIgnoreCase(candidate.getHost()) && port == trustedPort;
        } catch (URISyntaxException error) {
            return false;
        }
    }
}
