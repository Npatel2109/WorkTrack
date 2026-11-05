<?php

use App\Http\Controllers\ReportController;
use Illuminate\Support\Facades\Route;

Route::middleware('auth')->group(function () {
    Route::get('/reports', [ReportController::class, 'index'])->name('reports.index');
    Route::get('/reports/activity.xlsx', [ReportController::class, 'activity'])->name('reports.activity');
    Route::get('/admin/reports/export.xlsx', [ReportController::class, 'activityAdmin'])->middleware('role:admin')->name('reports.activity.admin');
    Route::get('/reports/combined.pdf', [ReportController::class, 'combined'])->middleware('role:admin')->name('reports.combined');
    Route::get('/reports/{id}/pdf', [ReportController::class, 'pdf'])->whereNumber('id')->middleware('role:admin')->name('reports.pdf');

    Route::get('/submitted-reports', [ReportController::class, 'submittedIndex'])->name('submitted-reports.index');
    Route::get('/employee/submitted-reports/new', [ReportController::class, 'submittedCreate'])
        ->middleware('role:employee')->name('submitted-reports.create');
    Route::post('/employee/submitted-reports/new', [ReportController::class, 'submittedStore'])
        ->middleware('role:employee')->name('submitted-reports.store');
    Route::get('/employee/submitted-reports/{id}/edit', [ReportController::class, 'submittedEdit'])
        ->whereNumber('id')->middleware('role:employee')->name('submitted-reports.edit');
    Route::post('/employee/submitted-reports/{id}/edit', [ReportController::class, 'submittedStore'])
        ->whereNumber('id')->middleware('role:employee')->name('submitted-reports.update');
    Route::get('/submitted-reports/{id}/versions/{versionId}/download', [ReportController::class, 'submittedDownload'])
        ->whereNumber('id')->whereNumber('versionId')->name('submitted-reports.download');
    Route::get('/submitted-reports/{id}', [ReportController::class, 'submittedShow'])
        ->whereNumber('id')->name('submitted-reports.show');
});
