package com.worktrack.mobile;

public class MonitorRulesTest {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        check(MonitorRules.freshResponse(1000, 31_000), "HTTP freshness boundary");
        check(!MonitorRules.freshResponse(1000, 31_001), "delayed HTTP rejected");
        check(!MonitorRules.freshResponse(1000, 999), "negative HTTP age rejected");
        check(MonitorRules.currentFix(100, 110, 120), "current-cycle location accepted");
        check(!MonitorRules.currentFix(100, 99, 120), "cached pre-request fix rejected");
        check(!MonitorRules.currentFix(100, 130, 120), "future nanosecond fix rejected");
        check(!MonitorRules.currentFix(100, 110, 60_000_000_111L), "stale nanosecond fix rejected");
        check(MonitorRules.validSite(19.1, 72.9, 100), "valid site snapshot");
        check(!MonitorRules.validSite(Double.NaN, 0, 100), "NaN latitude rejected");
        check(!MonitorRules.validSite(0, 181, 100), "longitude range");
        check(!MonitorRules.validSite(0, 0, 0), "positive radius required");
        check(!MonitorRules.validSite(0, 0, Double.POSITIVE_INFINITY), "infinite radius rejected");
        check(MonitorRules.classify(20, 5, 100, 1000) == MonitorRules.Position.INSIDE, "inside");
        check(MonitorRules.classify(200, 5, 100, 1000) == MonitorRules.Position.OUTSIDE, "outside");
        check(MonitorRules.classify(100, 0, 100, 0) == MonitorRules.Position.INSIDE, "boundary inside");
        check(MonitorRules.classify(105, 5, 100, 0) == MonitorRules.Position.UNCERTAIN, "outside uncertainty overlaps boundary");
        check(MonitorRules.classify(95, 10, 100, 0) == MonitorRules.Position.UNCERTAIN, "inside uncertainty overlaps boundary");
        check(MonitorRules.classify(500, 1000, 100, 0) == MonitorRules.Position.UNCERTAIN, "poor accuracy never alerts");
        check(MonitorRules.classify(200, 5, 100, 60_001) == MonitorRules.Position.UNCERTAIN, "stale fix never alerts");
        check(MonitorRules.classify(200, 5, 100, -1) == MonitorRules.Position.UNCERTAIN, "future fix never alerts");
        check(MonitorRules.classify(Double.NaN, 5, 100, 0) == MonitorRules.Position.UNCERTAIN, "invalid distance");
        check(MonitorRules.classify(200, -1, 100, 0) == MonitorRules.Position.UNCERTAIN, "invalid accuracy");
        check(MonitorRules.nextDelay(1000, 6000, 120_000) == 55_000, "one minute from cycle start");
        check(MonitorRules.nextDelay(1000, 6000, 10_000) == 10_000, "cutoff bounds delay");
        check(MonitorRules.nextDelay(1000, 70_000, 120_000) == 0, "slow network no negative delay");
        check(MonitorRules.nextDelay(1000, 6000, -1000) == 0, "expired visit");
        System.out.println("Minute schedule and geofence tests passed.");
    }
}
