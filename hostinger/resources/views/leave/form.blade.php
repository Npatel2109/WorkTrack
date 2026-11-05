@extends('layouts.app')
@section('content')
<h1 class="h3">{{ $leave ? 'Correct leave' : 'New leave record' }}</h1><p>Changed records return to pending. Review approval separately.</p>
<form class="card card-body" method="post" action="{{ url($leave ? '/admin/leaves/'.$leave->id.'/edit' : '/admin/leaves/new') }}">@csrf
<label class="form-label" for="employee_id">Employee</label><select class="form-select mb-3" id="employee_id" name="employee_id" required>@foreach($employees as $employee)@if(!$leave || $leave->employee_id===$employee->id)<option value="{{ $employee->id }}" @selected(old('employee_id',$leave->employee_id ?? '')==$employee->id)>{{ $employee->name }}</option>@endif@endforeach</select>
<div class="row g-3">@foreach(['start_date'=>'Start date','end_date'=>'End date'] as $field=>$label)<div class="col-md-6"><label class="form-label" for="{{ $field }}">{{ $label }}</label><input class="form-control" id="{{ $field }}" type="date" name="{{ $field }}" value="{{ old($field,$leave->$field ?? \App\Services\WorkTrack::today()->toDateString()) }}" required></div>@endforeach</div>
<label class="form-label mt-3" for="period">Duration</label><select class="form-select" id="period" name="period">@foreach(['full'=>'Full day','morning'=>'Morning half day','afternoon'=>'Afternoon half day'] as $value=>$label)<option value="{{ $value }}" @selected(old('period',$leave->period ?? 'full')===$value)>{{ $label }}</option>@endforeach</select>
<label class="form-label mt-3" for="reason">Reason</label><textarea class="form-control" id="reason" name="reason" maxlength="2000" required>{{ old('reason',$leave->reason ?? '') }}</textarea><button class="btn btn-primary mt-3">Save as pending</button><a class="btn btn-link mt-2" href="{{ url('/admin/leaves') }}">Cancel</a></form>
@endsection
