@extends('layouts.app')

@section('content')
<div class="container py-4">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <h1 class="h3 mb-0">Submitted PDF reports</h1>
        @if (auth()->user()->role === 'employee')<a class="btn btn-primary" href="{{ route('submitted-reports.create') }}">Submit report</a>@endif
    </div>
    @foreach (['success' => 'success', 'info' => 'info'] as $key => $type)
        @if (session($key))<div class="alert alert-{{ $type }}">{{ session($key) }}</div>@endif
    @endforeach
    <div class="table-responsive"><table class="table table-striped align-middle">
        <thead><tr>@if (auth()->user()->role === 'admin')<th>Employee</th>@endif<th>Site</th><th>Period</th><th>Man-days</th><th>Revision</th><th>Submitted</th><th></th></tr></thead>
        <tbody>
        @forelse ($reports as $report)
            <tr>
                @if (auth()->user()->role === 'admin')<td>{{ $report->employee_name }}</td>@endif
                <td>{{ $report->site_name ?? '—' }}</td><td>{{ $report->start_date }} – {{ $report->end_date }}</td>
                <td>{{ $report->total_mandays }}</td><td>{{ $report->revision_number }}</td>
                <td>{{ \Carbon\CarbonImmutable::parse($report->created_at, 'UTC')->setTimezone('Asia/Kolkata')->format('d M Y') }}</td>
                <td><a class="btn btn-sm btn-outline-primary" href="{{ route('submitted-reports.show', $report->id) }}">History</a></td>
            </tr>
        @empty<tr><td colspan="{{ auth()->user()->role === 'admin' ? 7 : 6 }}" class="text-center text-muted py-4">No submitted reports found.</td></tr>@endforelse
        </tbody>
    </table></div>
    {{ $reports->links() }}
</div>
@endsection
