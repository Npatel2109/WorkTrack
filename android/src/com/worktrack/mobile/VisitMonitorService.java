package com.worktrack.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class VisitMonitorService extends Service implements LocationListener {
    private static final String TRACKING_CHANNEL = "visit_tracking";
    private static final String REMINDER_CHANNEL = "outside_site";
    private static final int TRACKING_ID = 100;
    private static final int REMINDER_ID = 101;
    private static final int FAILURE_ID = 102;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private final Runnable cycle = this::checkVisit;
    private final Runnable cutoffTask = this::checkCutoff;
    private final Runnable fixTimeout = () -> {
        stopLocations();
        clearReminder();
        updateStatus("Location unavailable. Check GPS and precise location permission.");
    };
    private VisitSnapshot visit;
    private LocationManager locations;
    private NotificationManager notifications;
    private long deadlineElapsed;
    private long fixRequestedNanos;
    private boolean waitingForFix;
    private boolean destroyed;
    private boolean explicitlyStopped;
    private int generation;
    private PowerManager.WakeLock wakeLock;
    private String currentStatus = "Checking active visit…";
    private static volatile String displayStatus = "No active visit monitoring.";
    private final BroadcastReceiver providerChanges = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (visit == null) return;
            clearReminder();
            stopLocations();
            updateStatus("Location settings changed. Waiting for the next fresh location check.");
        }
    };

    static String status() { return displayStatus; }

    static void stopForInactiveVisit(Context context) {
        displayStatus = "No active visit monitoring.";
        context.stopService(new Intent(context, VisitMonitorService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        locations = (LocationManager) getSystemService(LOCATION_SERVICE);
        notifications = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel tracking = new NotificationChannel(TRACKING_CHANNEL,
            "Active visit location tracking", NotificationManager.IMPORTANCE_LOW);
        tracking.setDescription("Visible while an active site visit is monitored.");
        notifications.createNotificationChannel(tracking);
        NotificationChannel reminders = new NotificationChannel(REMINDER_CHANNEL,
            "Outside-site reminders", NotificationManager.IMPORTANCE_DEFAULT);
        reminders.setDescription("A reminder each minute while confirmed outside the active site.");
        notifications.createNotificationChannel(reminders);
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WorkTrack:activeVisit");
        wakeLock.setReferenceCounted(false);
        IntentFilter filter = new IntentFilter(LocationManager.PROVIDERS_CHANGED_ACTION);
        filter.addAction(LocationManager.MODE_CHANGED_ACTION);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(providerChanges, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(providerChanges, filter);
        }
    }

    static Intent intent(android.content.Context context, VisitSnapshot visit) {
        return new Intent(context, VisitMonitorService.class)
            .putExtra("attendance_id", visit.attendanceId)
            .putExtra("latitude", visit.latitude).putExtra("longitude", visit.longitude)
            .putExtra("radius_m", visit.radius).putExtra("cutoff", visit.cutoff)
            .putExtra("monitoring_token", visit.token);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Never resurrect a visit without a fresh snapshot from a visible Activity.
        if (intent == null) {
            finishMonitoring(null);
            return START_NOT_STICKY;
        }
        try {
            VisitSnapshot incoming = new VisitSnapshot(intent.getLongExtra("attendance_id", 0),
                intent.getDoubleExtra("latitude", Double.NaN),
                intent.getDoubleExtra("longitude", Double.NaN),
                intent.getDoubleExtra("radius_m", Double.NaN),
                intent.getLongExtra("cutoff", 0), intent.getStringExtra("monitoring_token"));
            if (!permissionsReady()) {
                finishMonitoring("Reminders stopped: allow precise location and notifications, then open WorkTrack.");
                return START_NOT_STICKY;
            }
            if (incoming.cutoff <= System.currentTimeMillis()) {
                finishMonitoring(null);
                return START_NOT_STICKY;
            }
            boolean newVisit = !incoming.sameVisit(visit);
            if (!newVisit) {
                // Refresh the capability without resetting the minute cadence or local deadline.
                visit = incoming;
            } else {
                generation++;
                handler.removeCallbacks(cycle);
                handler.removeCallbacks(cutoffTask);
                stopLocations();
                clearReminder();
                visit = incoming;
                deadlineElapsed = SystemClock.elapsedRealtime() + incoming.cutoff - System.currentTimeMillis();
                currentStatus = "Checking active visit…";
            }
            Notification notification = trackingNotification(currentStatus);
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(TRACKING_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
            } else {
                startForeground(TRACKING_ID, notification);
            }
            notifications.cancel(FAILURE_ID);
            if (newVisit) {
                if (wakeLock.isHeld()) wakeLock.release();
                // Bounded by this visit's server cutoff; never an indefinite device wake lock.
                wakeLock.acquire(Math.max(1, remaining()));
            }
            displayStatus = currentStatus;
            if (newVisit) handler.post(cycle);
            handler.removeCallbacks(cutoffTask);
            checkCutoff();
        } catch (IllegalArgumentException | SecurityException | IllegalStateException error) {
            finishMonitoring("Reminders unavailable. Open WorkTrack and check Android permissions.");
        }
        return START_NOT_STICKY;
    }

    private boolean permissionsReady() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
            && notifications.areNotificationsEnabled()
            && (Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED)
            && notifications.getNotificationChannel(TRACKING_CHANNEL).getImportance() != NotificationManager.IMPORTANCE_NONE
            && notifications.getNotificationChannel(REMINDER_CHANNEL).getImportance() != NotificationManager.IMPORTANCE_NONE;
    }

    private long remaining() {
        if (visit == null) return 0;
        return Math.min(visit.cutoff - System.currentTimeMillis(),
            deadlineElapsed - SystemClock.elapsedRealtime());
    }

    private void checkCutoff() {
        if (remaining() <= 0) {
            finishMonitoring(null);
        } else {
            // Recheck the wall clock too, including after clock changes/device sleep.
            handler.postDelayed(cutoffTask, Math.min(remaining(), 1000));
        }
    }

    private void checkVisit() {
        if (destroyed || visit == null) return;
        if (remaining() <= 0) {
            finishMonitoring(null);
            return;
        }
        if (!permissionsReady()) {
            finishMonitoring("Reminders stopped: restore precise location and notification permissions, then open WorkTrack.");
            return;
        }
        stopLocations();
        clearReminder();
        final VisitSnapshot requestedVisit = visit;
        final int requestedGeneration = generation;
        final long started = SystemClock.elapsedRealtime();
        updateStatus("Checking active visit and location…");
        network.execute(() -> {
            MonitorApi.Result result = null;
            try {
                result = MonitorApi.monitor(requestedVisit.token);
            } catch (Exception ignored) {
                // Never log a capability or a response body.
            }
            final MonitorApi.Result response = result;
            handler.post(() -> {
                if (destroyed || requestedGeneration != generation || visit == null) return;
                if (remaining() <= 0) {
                    finishMonitoring(null);
                    return;
                }
                if (!MonitorRules.freshResponse(started, SystemClock.elapsedRealtime())) {
                    updateStatus("Visit status response delayed: reminders paused until a fresh check.");
                } else if (response != null && response.status == 200) {
                    if (response.visit == null) {
                        finishMonitoring(null);
                        return;
                    }
                    if (!visit.sameVisit(response.visit)) {
                        finishMonitoring("Visit details changed. Open WorkTrack to restart reminders.");
                        return;
                    }
                    requestFreshLocation();
                } else if (response != null && (response.status == 401 || response.status == 403)) {
                    finishMonitoring("Visit monitoring authorization expired. Open WorkTrack to check your visit.");
                    return;
                } else {
                    updateStatus("Cannot verify active visit: reminders paused. Check internet connection.");
                }
                handler.postDelayed(cycle, MonitorRules.nextDelay(started,
                    SystemClock.elapsedRealtime(), remaining()));
            });
        });
    }

    private void requestFreshLocation() {
        if (!permissionsReady()) {
            finishMonitoring("Reminders stopped: allow precise location and notifications, then open WorkTrack.");
            return;
        }
        waitingForFix = true;
        fixRequestedNanos = SystemClock.elapsedRealtimeNanos();
        boolean enabled = false;
        try {
            for (String provider : new String[]{LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
                if (locations.isProviderEnabled(provider)) {
                    locations.requestLocationUpdates(provider, 0, 0, this, Looper.getMainLooper());
                    enabled = true;
                }
            }
        } catch (SecurityException | IllegalArgumentException error) {
            stopLocations();
            updateStatus("Location unavailable. Enable GPS and precise location permission.");
            return;
        }
        if (!enabled) {
            stopLocations();
            updateStatus("Location is disabled. Enable GPS to receive outside-site reminders.");
            return;
        }
        updateStatus("Waiting for a fresh precise location…");
        handler.postDelayed(fixTimeout, MonitorRules.FIX_TIMEOUT_MS);
    }

    @Override
    public void onLocationChanged(Location location) {
        if (!waitingForFix || visit == null || destroyed) return;
        if (remaining() <= 0) {
            finishMonitoring(null);
            return;
        }
        if (location.isFromMockProvider() || !location.hasAccuracy()
                || !MonitorRules.validSite(location.getLatitude(), location.getLongitude(), 1)) return;
        long nowNanos = SystemClock.elapsedRealtimeNanos();
        if (!MonitorRules.currentFix(fixRequestedNanos, location.getElapsedRealtimeNanos(), nowNanos)) return;
        long age = (nowNanos - location.getElapsedRealtimeNanos()) / 1_000_000;
        float[] distance = new float[1];
        Location.distanceBetween(visit.latitude, visit.longitude,
            location.getLatitude(), location.getLongitude(), distance);
        MonitorRules.Position position = MonitorRules.classify(distance[0],
            location.getAccuracy(), visit.radius, age);
        if (position == MonitorRules.Position.UNCERTAIN) {
            clearReminder();
            updateStatus("Location uncertain. Waiting for an accurate GPS fix; no outside reminder.");
            return;
        }
        stopLocations();
        if (position == MonitorRules.Position.OUTSIDE) {
            updateStatus("Outside your active site's allowed radius. Checking every minute.");
            notifications.notify(REMINDER_ID, notification(REMINDER_CHANNEL,
                "Outside active site", "You are outside your site's allowed radius. Return to the site or stop your visit.", false)
                .setOnlyAlertOnce(false).setTimeoutAfter(MonitorRules.MINUTE_MS).build());
        } else {
            clearReminder();
            updateStatus("Inside your active site's allowed radius. Checking every minute.");
        }
    }

    @Override
    public void onProviderDisabled(String provider) {
        if (visit == null) return;
        clearReminder();
        updateStatus("Location provider disabled. Enable GPS for reliable reminders.");
    }

    @Override public void onProviderEnabled(String provider) {}
    @Override public void onStatusChanged(String provider, int status, Bundle extras) {}

    private void stopLocations() {
        waitingForFix = false;
        handler.removeCallbacks(fixTimeout);
        if (locations != null) {
            try {
                locations.removeUpdates(this);
            } catch (SecurityException ignored) {}
        }
    }

    private Notification.Builder notification(String channel, String title, String text, boolean ongoing) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
            new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, channel).setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle(title).setContentText(text).setStyle(new Notification.BigTextStyle().bigText(text))
            .setContentIntent(open).setOngoing(ongoing).setAutoCancel(!ongoing)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setCategory(ongoing ? Notification.CATEGORY_SERVICE : Notification.CATEGORY_REMINDER);
    }

    private Notification trackingNotification(String text) {
        return notification(TRACKING_CHANNEL, "WorkTrack active visit • location monitoring", text, true)
            .setOnlyAlertOnce(true).build();
    }

    private void updateStatus(String text) {
        currentStatus = text;
        displayStatus = text;
        if (!destroyed && visit != null) notifications.notify(TRACKING_ID, trackingNotification(text));
    }

    private void clearReminder() {
        if (notifications != null) notifications.cancel(REMINDER_ID);
    }

    private void finishMonitoring(String reason) {
        explicitlyStopped = true;
        displayStatus = reason != null ? reason : "No active visit monitoring.";
        generation++;
        visit = null;
        handler.removeCallbacksAndMessages(null);
        stopLocations();
        clearReminder();
        stopForeground(STOP_FOREGROUND_REMOVE);
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        if (reason != null && notifications.areNotificationsEnabled()) {
            notifications.notify(FAILURE_ID, notification(TRACKING_CHANNEL,
                "WorkTrack reminders stopped", reason, false).build());
        }
        stopSelf();
    }

    @Override
    public void onDestroy() {
        if (!explicitlyStopped && visit != null && !"No active visit monitoring.".equals(displayStatus)) {
            displayStatus = "Reminders interrupted. Open WorkTrack to resume an active visit.";
        }
        destroyed = true;
        generation++;
        visit = null;
        handler.removeCallbacksAndMessages(null);
        stopLocations();
        clearReminder();
        stopForeground(STOP_FOREGROUND_REMOVE);
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        unregisterReceiver(providerChanges);
        network.shutdownNow();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
