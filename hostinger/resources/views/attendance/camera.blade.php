@extends('layouts.app')
@section('content')
<div class="workflow-wrap"><a class="back-link" href="{{ url('/employee') }}">Cancel visit</a>
<section class="workflow-card p-4"><h1 class="h3">Capture check-in selfie</h1><p>{{ $site->site_name }}</p>
    <div class="camera-frame"><video id="camera-video" autoplay playsinline muted aria-label="Live camera preview"></video><canvas id="camera-canvas" class="d-none"></canvas><img id="captured-preview" class="d-none" alt="Captured selfie"><div id="camera-placeholder" class="camera-placeholder">Camera preview appears here</div></div>
    <div id="camera-error" class="alert alert-danger d-none mt-3" role="alert"></div>
    <div class="d-flex flex-wrap gap-2 mt-3"><button class="btn btn-outline-primary" id="start-camera" type="button">Enable camera</button><button class="btn btn-primary" id="capture-photo" type="button" disabled>Capture selfie</button><button class="btn btn-outline-secondary d-none" id="retake-photo" type="button">Retake</button></div>
    <form id="checkin-form" method="post" action="{{ url('/visit/'.$site->id.'/checkin') }}" class="mt-3">@csrf
        <input type="hidden" name="selfie" id="selfie-data"><button class="btn btn-success w-100" id="submit-checkin" disabled>Confirm &amp; Start Site</button>
    </form>
</section></div>
@endsection
@section('scripts')<script src="{{ asset('static/js/workflow.js') }}"></script>@endsection
