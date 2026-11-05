<?php

use App\Http\Controllers\AdminController;
use Illuminate\Support\Facades\Route;

Route::middleware(['auth', 'role:admin'])->prefix('admin')->group(function () {
    Route::match(['get', 'post'], '/employees', [AdminController::class, 'employees']);
    Route::get('/employees/{id}/edit', [AdminController::class, 'employees'])->whereNumber('id');
    Route::post('/employees/{id}/edit', [AdminController::class, 'saveEmployee'])->whereNumber('id');
    Route::post('/employees/{id}/reset-password', [AdminController::class, 'resetPassword'])->whereNumber('id');
    Route::post('/employees/{id}/delete', [AdminController::class, 'deleteEmployee'])->whereNumber('id');
    Route::match(['get', 'post'], '/sites', [AdminController::class, 'sites']);
    Route::match(['get', 'post'], '/sites/{id}/edit', [AdminController::class, 'sites'])->whereNumber('id');
    Route::post('/sites/{id}/report-details', [AdminController::class, 'reportDetails'])->whereNumber('id');
    Route::post('/sites/{id}/delete', [AdminController::class, 'deleteSite'])->whereNumber('id');
    Route::match(['get', 'post'], '/projects', [AdminController::class, 'projects']);
    Route::post('/projects/{id}/edit', [AdminController::class, 'projects'])->whereNumber('id');
    Route::post('/projects/{id}/delete', [AdminController::class, 'deleteProject'])->whereNumber('id');
    Route::match(['get', 'post'], '/assignments', [AdminController::class, 'assignments']);
    Route::match(['get', 'post'], '/settings', [AdminController::class, 'settings']);
    Route::get('/attendance', [AdminController::class, 'attendance']);
    Route::get('/metrics/{metric}', [AdminController::class, 'metric']);
});

Route::middleware(['auth', 'role:admin'])->get('/api/admin/live-status', [AdminController::class, 'liveStatus']);
