package com.worktrack.mobile;

public class ServerAddressTest {
    public static void main(String[] args) {
        String[] valid = {
            "http://192.168.1.6:5000", "http://10.0.0.5:5000",
            "http://172.16.0.1", "http://172.31.255.254",
            "https://worktrack.example.com", "https://192.168.1.6:5000"
        };
        for (String value : valid) {
            if (!value.equals(ServerAddress.validate(value))) throw new AssertionError(value);
        }
        String[] invalid = {
            "http://example.com", "http://127.0.0.1", "http://8.8.8.8",
            "http://172.32.0.1", "http://172.15.0.1", "http://192.169.0.1",
            "http://192.168.1.999", "http://192.168.01.6", "http://169.254.1.1",
            "http://localhost", "file:///etc/passwd", "javascript:alert(1)",
            "http://192.168.1.6:0", "https://example.com:99999",
            "http://user:password@192.168.1.6", "http://192.168.1.6/path",
            "http://192.168.1.6?q=token", "https://example.com#fragment"
        };
        for (String value : invalid) {
            if (ServerAddress.validate(value) != null) throw new AssertionError(value);
        }
        if (!ServerAddress.sameOrigin("http://192.168.1.6", "http://192.168.1.6:80/login")) throw new AssertionError("HTTP default port");
        if (!ServerAddress.sameOrigin("https://example.com", "https://example.com:443/login")) throw new AssertionError("HTTPS default port");
        if (!ServerAddress.sameOrigin("http://192.168.1.6:5000", "http://192.168.1.6:5000/reports")) throw new AssertionError("Local path");
        for (String value : new String[]{
            "https://192.168.1.6:5000", "http://192.168.1.6:5001",
            "http://192.168.1.7:5000", "http://user@192.168.1.6:5000"
        }) {
            if (ServerAddress.sameOrigin("http://192.168.1.6:5000", value)) throw new AssertionError(value);
        }
        System.out.println("Server URL validation and origin tests passed.");
    }
}
