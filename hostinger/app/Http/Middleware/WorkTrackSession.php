<?php

namespace App\Http\Middleware;

use App\Models\User;
use App\Services\AttendanceService;
use App\Services\WorkTrack;
use Closure;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Symfony\Component\HttpFoundation\Response;

class WorkTrackSession
{
    public function handle(Request $request, Closure $next): Response
    {
        $user = $request->user();
        if ($user) {
            $current = User::find($user->id);
            $version = $request->session()->get('auth_version');
            $expired = $request->session()->has('last_activity') &&
                time() - $request->session()->get('last_activity') > config('worktrack.session_timeout') * 60;
            if (! $current || ! $current->is_active || ($version !== null && $version !== $current->auth_version) || $expired) {
                WorkTrack::audit('logout', 'Inactive account, changed credentials or session timeout');
                Auth::logout();
                $request->session()->invalidate();
                $request->session()->regenerateToken();

                return $request->expectsJson()
                    ? response()->json(['error' => 'Your session expired. Sign in again.'], 401)
                    : redirect('/login')->withErrors(['email' => 'Your session expired. Sign in again.']);
            }
            $user = $current;
            Auth::setUser($user);
            $request->session()->put('auth_version', $user->auth_version);
            $request->session()->put('last_activity', time());
            app(AttendanceService::class)->closeOverdue();
        }

        return $next($request);
    }
}
