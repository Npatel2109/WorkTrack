@extends('layouts.app')
@section('title', 'Sign in')
@section('content')
<div class="card mx-auto p-4" style="max-width:460px">
    <h1 class="h3">Welcome to WorkTrack</h1><p class="text-secondary">Sign in to your workspace.</p>
    <form method="post" action="{{ url('/login') }}">@csrf
        <label class="form-label" for="email">Email</label><input id="email" name="email" type="email" class="form-control mb-3" value="{{ old('email') }}" required autocomplete="username">
        <label class="form-label" for="password">Password</label><div class="input-group mb-3"><input id="password" name="password" type="password" class="form-control" required autocomplete="current-password"><button id="toggle-password" class="btn btn-outline-secondary" type="button" aria-label="Show password">Show</button></div>
        <button class="btn btn-primary w-100">Sign in</button>
    </form>
    <a class="mt-3" href="{{ url('/forgot-password') }}">Forgot password?</a>
    @if(\Illuminate\Support\Facades\DB::table('app_settings')->where('id',1)->value('android_download_enabled') && is_file(config('worktrack.apk_path')))
    <a class="mt-3" href="{{ url('/download/android') }}">Download Android app</a>@endif
</div>
@endsection
