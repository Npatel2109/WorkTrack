@extends('layouts.app')
@section('content')
<div class="card mx-auto p-4" style="max-width:460px"><h1 class="h3">Recover password</h1>
    <form method="post">@csrf<label for="email" class="form-label">Account email</label><input id="email" name="email" type="email" class="form-control mb-3" required value="{{ old('email') }}"><button class="btn btn-primary">Send reset link</button></form>
    <a href="{{ url('/login') }}" class="mt-3">Back to sign in</a>
</div>
@endsection
