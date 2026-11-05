@extends('layouts.app')
@section('content')
<div class="card p-4"><h1 class="h3">Complete site report</h1>
    <form method="post" id="site-report-form">@csrf
        <label class="form-label" for="activity-detail">Activity detail</label><textarea class="form-control mb-2" id="activity-detail" name="activity_detail" rows="8" maxlength="50000" required>{{ old('activity_detail') }}</textarea>
        <button class="btn btn-outline-primary mb-2" id="report-dictate" type="button" aria-pressed="false">Start microphone</button><p id="dictation-status" role="status">Type normally or use English voice typing.</p>
        <label class="form-label" for="billing">Billing</label><select class="form-select mb-3" id="billing" name="billing" required><option value="">Select</option><option value="billable" @selected(old('billing') === 'billable')>Billable</option><option value="non_billable" @selected(old('billing') === 'non_billable')>Non-billable</option></select>
        <label class="form-label" for="engineer-name">Engineer name</label><input class="form-control mb-3" id="engineer-name" name="engineer_name" maxlength="120" value="{{ old('engineer_name',auth()->user()->name) }}" required>
        <p>Customer name/signature and sign-off dates remain blank for offline completion.</p><button class="btn btn-success">Submit report &amp; complete visit</button>
    </form>
</div>
@endsection
@section('scripts')<script src="{{ asset('static/js/report-dictation.js') }}"></script>@endsection
