<?php

use App\Http\Controllers\BillingController;
use Illuminate\Support\Facades\Route;

Route::middleware(['auth', 'role:admin'])->group(function () {
    Route::get('/admin/invoices', [BillingController::class, 'index'])->name('billing.index');
    Route::post('/admin/invoices', [BillingController::class, 'create'])->name('billing.create');
    Route::post('/admin/purchase-orders/{id}/status', [BillingController::class, 'updatePurchaseOrderStatus'])
        ->whereNumber('id')->name('billing.purchase-orders.status');
    Route::get('/admin/invoices/{id}/pdf', [BillingController::class, 'invoicePdf'])
        ->whereNumber('id')->name('billing.invoice.pdf');
});
