<?php

return [
    'upload_path' => env('WORKTRACK_UPLOAD_PATH', storage_path('app/private/uploads')),
    'apk_path' => env('WORKTRACK_APK_PATH', storage_path('app/private/downloads/worktrack.apk')),
    'session_timeout' => (int) env('SESSION_LIFETIME', 60),
];
