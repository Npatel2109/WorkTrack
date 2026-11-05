@extends('layouts.app')
@section('content')
<h1 class="h3">{{ $title }}</h1><a class="btn btn-outline-primary mb-3" href="{{ url('/admin') }}">Back to dashboard</a>
<div class="card table-responsive"><table class="table table-striped mb-0"><thead><tr><th>Employee</th><th>Details</th><th>Status</th><th>Report</th></tr></thead><tbody>
@forelse($rows as $row)<tr><td>{{ $row['employee'] }}</td><td>{{ $row['details'] }}</td><td>{{ $row['status'] }}</td><td>@if($row['pdf_url'])<a href="{{ $row['pdf_url'] }}">Download PDF</a>@else — @endif</td></tr>@empty<tr><td colspan="4">No matching records.</td></tr>@endforelse
</tbody></table></div>
@endsection
