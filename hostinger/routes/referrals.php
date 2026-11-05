<?php

use App\Http\Controllers\ReferralController;
use Illuminate\Support\Facades\Route;

Route::middleware(['auth', 'role:employee'])->group(function () {
    Route::match(['get', 'post'], '/employee/referrals', [ReferralController::class, 'employee']);
});
Route::middleware(['auth', 'role:admin'])->group(function () {
    Route::get('/admin/referrals', [ReferralController::class, 'index']);
    Route::get('/admin/referrals/{id}/resume', [ReferralController::class, 'download'])->whereNumber('id');
});
