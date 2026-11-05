@extends('layouts.app')
@section('content')
<div class="workflow-wrap"><a class="back-link" href="{{ url('/employee') }}">Back to dashboard</a>
<section class="workflow-card text-center p-4"><p class="eyebrow">{{ $flow === 'checkin' ? 'START SITE' : 'STOP SITE' }}</p>
    <h1 class="h2">{{ $site->site_name }}</h1><p>{{ $site->customer_name }}<br>{{ $site->address }}</p>
    <p>Reference radius: {{ $site->allowed_radius }} meters</p>
    <button class="btn btn-primary btn-lg" id="verify-location" data-flow="{{ $flow }}" data-endpoint="{{ url('/api/visit/'.$id.'/verify-'.$flow) }}">{{ $flow === 'checkin' ? 'Use my current location' : 'Capture location & Stop Site' }}</button>
    <div id="gps-status" class="gps-feedback mt-4" role="status">Your browser will ask permission to access your location.</div>
    <p class="small mt-4">You can continue outside the reference radius. The administrator can see your recorded coordinates and distance.
    {{ $flow === 'checkout' ? 'If GPS is unavailable, stopping records the last captured location. Reports can be completed later.' : 'A fresh location and live selfie are required to start.' }}</p>
</section></div>
@endsection
@section('scripts')<script src="{{ asset('static/js/workflow.js') }}"></script>@endsection
