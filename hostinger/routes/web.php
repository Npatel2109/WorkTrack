<?php

use App\Http\Controllers\AdminController;
use App\Http\Controllers\AttendanceController;
use App\Http\Controllers\AuthController;
use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    return redirect(auth()->check() ? (auth()->user()->role === 'admin' ? '/admin' : '/employee') : '/login');
});
Route::get('/login', [AuthController::class, 'form'])->name('login');
Route::post('/login', [AuthController::class, 'login'])->middleware('throttle:15,1');
Route::match(['get', 'post'], '/forgot-password', [AuthController::class, 'forgot'])->middleware('throttle:5,1');
Route::match(['get', 'post'], '/reset-password/{token}', [AuthController::class, 'reset'])->middleware('throttle:10,1')->name('password.reset');
Route::post('/logout', [AuthController::class, 'logout'])->middleware('auth');
Route::get('/brand-logo', [AuthController::class, 'logo']);
Route::get('/download/android', [AuthController::class, 'android']);
Route::get('/api/visit-monitor', [AttendanceController::class, 'monitor'])->middleware('throttle:120,1');
Route::middleware(['auth', 'role:admin'])->get('/admin', [AdminController::class, 'dashboard']);
Route::middleware(['auth', 'role:employee'])->group(function () {
    Route::get('/employee', [AdminController::class, 'employeeDashboard']);
    Route::post('/visit/start/{id}', [AttendanceController::class, 'start']);
    Route::get('/visit/{id}/checkin-location', [AttendanceController::class, 'checkinLocation']);
    Route::post('/api/visit/{id}/verify-checkin', [AttendanceController::class, 'verifyCheckin']);
    Route::get('/visit/{id}/camera', [AttendanceController::class, 'camera']);
    Route::post('/visit/{id}/checkin', [AttendanceController::class, 'checkin']);
    Route::get('/visit/{id}/checkout-location', [AttendanceController::class, 'checkoutLocation']);
    Route::post('/api/visit/{id}/verify-checkout', [AttendanceController::class, 'checkout']);
    Route::post('/api/visit/{id}/location', [AttendanceController::class, 'location']);
    Route::get('/api/employee/active-visit', [AttendanceController::class, 'active']);
    Route::match(['get', 'post'], '/visit/{id}/report', [AttendanceController::class, 'report']);
});
Route::get('/media/{filename}', [AttendanceController::class, 'media'])->middleware('auth')->where('filename', '[A-Za-z0-9_.-]+');
foreach (['admin', 'leave', 'referrals', 'reports', 'billing'] as $module) {
    require __DIR__.'/'.$module.'.php';
}
