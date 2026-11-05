@extends('layouts.app')

@section('content')
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <h1 class="h3 mb-0">Completed site reports</h1>
        <a class="btn btn-outline-primary" href="{{ route('submitted-reports.index') }}">Submitted PDF reports</a>
    </div>
    @if (session('success'))<div class="alert alert-success">{{ session('success') }}</div>@endif
    @if ($errors->any())<div class="alert alert-danger">{{ $errors->first() }}</div>@endif

    <div class="card mb-4" id="activity-export">
        <div class="card-header">Activity workbook</div>
        <div class="card-body">
            <form method="get" action="{{ route('reports.activity') }}" class="row g-3">
                <div class="col-md-3"><label class="form-label" for="site_id">Site</label>
                    <select class="form-select" name="site_id" id="site_id" required><option value="">Select a site</option>
                        @foreach ($sites as $site)<option value="{{ $site->id }}">{{ $site->site_name }}</option>@endforeach
                    </select>
                </div>
                @if (auth()->user()->role === 'admin')
                    <div class="col-md-3"><label class="form-label" for="employee_id">Employee</label>
                        <select class="form-select" name="employee_id" id="employee_id" required><option value="">Select an employee</option>
                            @foreach ($employees as $employee)<option value="{{ $employee->id }}">{{ $employee->name }}</option>@endforeach
                        </select>
                    </div>
                @endif
                <div class="col-md-2"><label class="form-label" for="activity_start">Start date</label><input id="activity_start" class="form-control" type="date" name="start_date" required></div>
                <div class="col-md-2"><label class="form-label" for="activity_end">End date</label><input id="activity_end" class="form-control" type="date" name="end_date" required></div>
                <div class="col-md-2 d-flex align-items-end"><button class="btn btn-primary w-100">Download XLSX</button></div>
            </form>
        </div>
    </div>

    @if (auth()->user()->role === 'admin')
        <div class="card mb-4">
            <div class="card-header">Combined PDF</div><div class="card-body">
                <form method="get" action="{{ route('reports.combined') }}" class="row g-3">
                    <div class="col-md-4"><label class="form-label">Start date</label><input class="form-control" type="date" name="start_date" required></div>
                    <div class="col-md-4"><label class="form-label">End date</label><input class="form-control" type="date" name="end_date" required></div>
                    <div class="col-md-4 d-flex align-items-end"><button class="btn btn-outline-primary">Download completed reports</button></div>
                </form>
            </div>
        </div>
    @endif

    <div class="table-responsive">
        <table class="table table-striped align-middle">
            <thead><tr><th>Visit</th><th>Site</th><th>Employee</th><th>Customer</th><th>Check-out</th><th>Activity</th>@if (auth()->user()->role === 'admin')<th>PDF</th>@endif</tr></thead>
            <tbody>
            @forelse ($reports as $report)
                <tr>
                    <td>#{{ $report->id }}</td><td>{{ $report->site_name }}</td><td>{{ $report->employee_name }}</td>
                    <td>{{ $report->customer_name }}</td><td>{{ $report->checkout_time ? \Carbon\CarbonImmutable::parse($report->checkout_time, 'UTC')->setTimezone('Asia/Kolkata')->format('d M Y H:i') : '—' }} IST</td>
                    <td>{{ \Illuminate\Support\Str::limit($report->work_performed, 120) }}</td>
                    @if (auth()->user()->role === 'admin')<td><a class="btn btn-sm btn-outline-secondary" href="{{ route('reports.pdf', $report->id) }}">PDF</a></td>@endif
                </tr>
            @empty<tr><td colspan="{{ auth()->user()->role === 'admin' ? 7 : 6 }}" class="text-center text-muted py-4">No completed reports found.</td></tr>@endforelse
            </tbody>
        </table>
    </div>
</div>
@endsection
