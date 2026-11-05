@extends('layouts.app')

@section('content')
<div class="container py-4">
    <h1 class="h3 mb-3">{{ $report ? 'Edit submitted report' : 'Submit PDF report' }}</h1>
    @if ($errors->any())<div class="alert alert-danger"><ul class="mb-0">@foreach ($errors->all() as $error)<li>{{ $error }}</li>@endforeach</ul></div>@endif
    <form method="post" enctype="multipart/form-data" action="{{ $report ? route('submitted-reports.update', $report->id) : route('submitted-reports.store') }}" class="card card-body">
        @csrf
        @if ($report)<input type="hidden" name="revision_number" value="{{ old('revision_number', $report->revision_number) }}">@endif
        <div class="row g-3">
            <div class="col-md-6"><label class="form-label" for="site_id">Site</label>
                <select class="form-select" name="site_id" id="site_id" required><option value="">Select an assigned site</option>
                    @foreach ($sites as $site)<option value="{{ $site->id }}" @selected(old('site_id', $values['site_id']) == $site->id)>{{ $site->site_name }}{{ ! $site->is_active ? ' (inactive history)' : '' }}</option>@endforeach
                </select>
            </div>
            <div class="col-md-3"><label class="form-label" for="start_date">Start date</label><input class="form-control" type="date" id="start_date" name="start_date" value="{{ old('start_date', $values['start_date']) }}" required></div>
            <div class="col-md-3"><label class="form-label" for="end_date">End date</label><input class="form-control" type="date" id="end_date" name="end_date" value="{{ old('end_date', $values['end_date']) }}" required></div>
            <div class="col-md-4"><label class="form-label" for="total_mandays">Total man-days</label><input class="form-control" type="number" min="1" max="2147483647" id="total_mandays" name="total_mandays" value="{{ old('total_mandays', $values['total_mandays']) }}" required></div>
            <div class="col-md-8"><label class="form-label" for="pdf">PDF report {{ $current ? '(optional; leave empty to keep the current file)' : '' }}</label><input class="form-control" type="file" id="pdf" name="pdf" accept="application/pdf,.pdf" {{ $current ? '' : 'required' }}><div class="form-text">Maximum file size: 5 MB. Prior versions are retained.</div></div>
        </div>
        <div class="mt-4"><button class="btn btn-primary">Save report</button> <a class="btn btn-outline-secondary" href="{{ $report ? route('submitted-reports.show', $report->id) : route('submitted-reports.index') }}">Cancel</a></div>
    </form>
</div>
@endsection
