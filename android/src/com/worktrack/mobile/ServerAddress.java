package com.worktrack.mobile;

import java.net.URI;
import java.net.URISyntaxException;

final class ServerAddress {
    private ServerAddress() {}

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
