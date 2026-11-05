@extends('layouts.app')

@section('content')
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <h1 class="h3 mb-0">Submitted report #{{ $report->id }}</h1>
        <div>@if (auth()->user()->role === 'employee' && auth()->id() === $report->employee_id)<a class="btn btn-primary" href="{{ route('submitted-reports.edit', $report->id) }}">Edit</a>@endif
            <a class="btn btn-outline-secondary" href="{{ route('submitted-reports.index') }}">Back</a></div>
    </div>
    @if (session('success'))<div class="alert alert-success">{{ session('success') }}</div>@endif
    <div class="card mb-4"><div class="card-body">
        <dl class="row mb-0">
            @if (auth()->user()->role === 'admin')<dt class="col-sm-3">Employee</dt><dd class="col-sm-9">{{ $employee->name ?? '—' }}</dd>@endif
            <dt class="col-sm-3">Site</dt><dd class="col-sm-9">{{ $site->site_name ?? '—' }}</dd>
            <dt class="col-sm-3">Created</dt><dd class="col-sm-9">{{ \Carbon\CarbonImmutable::parse($report->created_at, 'UTC')->setTimezone('Asia/Kolkata')->format('d M Y H:i') }} IST</dd>
            <dt class="col-sm-3">Current revision</dt><dd class="col-sm-9">{{ $report->revision_number }}</dd>
        </dl>
    </div></div>
    <h2 class="h5">Version history</h2>
    <div class="table-responsive"><table class="table table-striped align-middle">
        <thead><tr><th>Revision</th><th>Period</th><th>Man-days</th><th>PDF</th><th>Updated</th><th></th></tr></thead>
        <tbody>@foreach ($versions as $version)
            <tr><td>{{ $version->revision_number }} @if ($version->revision_number === $report->revision_number)<span class="badge text-bg-primary">Current</span>@endif</td>
                <td>{{ $version->start_date }} – {{ $version->end_date }}</td><td>{{ $version->total_mandays }}</td>
                <td>{{ $version->original_filename }} @if ($version->pdf_replaced)<span class="badge text-bg-info">PDF replaced</span>@endif</td>
                <td>{{ \Carbon\CarbonImmutable::parse($version->created_at, 'UTC')->setTimezone('Asia/Kolkata')->format('d M Y H:i') }} IST</td>
                <td><a class="btn btn-sm btn-outline-primary" href="{{ route('submitted-reports.download', [$report->id, $version->id]) }}">Download</a></td>
            </tr>
        @endforeach</tbody>
    </table></div>
</div>
@endsection
