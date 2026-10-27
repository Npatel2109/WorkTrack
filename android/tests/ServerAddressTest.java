package com.worktrack.mobile;

public class ServerAddressTest {
    public static void main(String[] args) {
        String server = AppConfig.SERVER;
        if (!"https://worktrack.blackpurple.in".equals(server)) throw new AssertionError("HTTPS production server");
        if (!server.matches("https?://[a-z0-9.-]+(:[0-9]+)?")) throw new AssertionError("Fixed server must be an origin: " + server);
        for (String value : new String[]{server, server + "/login", server + "/admin/settings?x=1"}) {
            if (!ServerAddress.sameOrigin(server, value)) throw new AssertionError(value);
        }
        if (!ServerAddress.sameOrigin("http://192.168.1.6", "http://192.168.1.6:80/login")) throw new AssertionError("HTTP default port");
        if (!ServerAddress.sameOrigin("https://example.com", "https://example.com:443/login")) throw new AssertionError("HTTPS default port");
        for (String value : new String[]{
            "https://worktrack.blackpurple.in", "http://worktrack.blackpurple.in.evil.com",
            "http://user@worktrack.blackpurple.in", "http://worktrack.blackpurple.in:8080",
            "javascript:alert(1)", "file:///etc/passwd"
        }) {
            if (ServerAddress.sameOrigin("http://worktrack.blackpurple.in", value)) throw new AssertionError(value);
        }
        if (!"-".equals(AppConfig.OFFLINE_TEXT)) throw new AssertionError("Offline text");
        System.out.println("Fixed server and origin tests passed.");
    }
}
