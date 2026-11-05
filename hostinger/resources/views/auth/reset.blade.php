@extends('layouts.app')
@section('content')
<div class="card mx-auto p-4" style="max-width:460px"><h1 class="h3">Set a new password</h1>
    <form method="post">@csrf
        <label class="form-label" for="email">Email</label><input id="email" name="email" type="email" class="form-control mb-3" required value="{{ old('email',$email) }}">
        <label class="form-label" for="password">New password (at least 10 characters)</label><input id="password" name="password" type="password" class="form-control mb-3" minlength="10" required autocomplete="new-password">
        <label class="form-label" for="confirmation">Confirm password</label><input id="confirmation" name="password_confirmation" type="password" class="form-control mb-3" required autocomplete="new-password">
        <button class="btn btn-primary">Update password</button>
    </form>
</div>
@endsection
