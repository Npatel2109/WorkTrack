<?php

use App\Http\Controllers\LeaveController;
use Illuminate\Support\Facades\Route;

Route::middleware(['auth', 'role:employee'])->group(function () {
    Route::match(['get', 'post'], '/employee/leaves', [LeaveController::class, 'employeeLeaves']);
    Route::post('/employee/work-status', [LeaveController::class, 'submitWorkStatus']);
});
Route::middleware(['auth', 'role:admin'])->prefix('admin')->group(function () {
    Route::get('/leaves', [LeaveController::class, 'index']);
    Route::match(['get', 'post'], '/leaves/new', [LeaveController::class, 'form']);
    Route::match(['get', 'post'], '/leaves/{id}/edit', [LeaveController::class, 'form'])->whereNumber('id');
    Route::post('/leaves/{id}/review', [LeaveController::class, 'review'])->whereNumber('id');
    Route::post('/leaves/entitlement', [LeaveController::class, 'entitlement']);
    Route::post('/leaves/account', [LeaveController::class, 'account']);
    Route::get('/work-status', [LeaveController::class, 'workStatuses']);
    Route::post('/work-status/{id}/review', [LeaveController::class, 'reviewWork'])->whereNumber('id');
    Route::match(['get', 'post'], '/work-status/{id}/edit', [LeaveController::class, 'editWork'])->whereNumber('id');
});
