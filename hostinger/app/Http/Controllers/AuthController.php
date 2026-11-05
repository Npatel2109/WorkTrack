<?php

namespace App\Http\Controllers;

use App\Models\User;
use App\Services\WorkTrack;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Facades\Password;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Str;
use Illuminate\Validation\ValidationException;

class AuthController extends Controller
{
    public function form()
    {
        return auth()->check() ? redirect(auth()->user()->role === 'admin' ? '/admin' : '/employee') : view('auth.login');
    }

    public function login(Request $request)
    {
        $data = $request->validate(['email' => 'required|email|max:255', 'password' => 'required|string|max:1024']);
        $email = Str::lower(trim($data['email']));
        $key = 'login:'.hash('sha256', $email.'|'.$request->ip());
        if (RateLimiter::tooManyAttempts($key, 5)) {
            throw ValidationException::withMessages(['email' => 'Too many attempts. Try again in '.RateLimiter::availableIn($key).' seconds.']);
        }
        RateLimiter::hit($key, 60);
        $user = User::where('email', $email)->where('is_active', true)->first();
        $valid = false;
        if ($user) {
            $stored = $user->password_hash;
            if (str_starts_with($stored, 'pbkdf2:sha256:')) {
                [$method, $salt, $expected] = explode('$', $stored, 3);
                $iterations = (int) explode(':', $method)[2];
                $valid = $iterations > 0 && $iterations <= 2_000_000 &&
                    hash_equals($expected, hash_pbkdf2('sha256', $data['password'], $salt, $iterations));
            } elseif (password_get_info($stored)['algo'] !== null) {
                $valid = password_verify($data['password'], $stored);
            } elseif (str_starts_with($stored, 'scrypt:')) {
                throw ValidationException::withMessages(['email' => 'This migrated account needs a password reset. Use Forgot password or contact your administrator.']);
            }
        }
        if (! $valid) {
            throw ValidationException::withMessages(['email' => 'Email or password is incorrect.']);
        }
        if (Hash::needsRehash($user->password_hash)) {
            $user->password_hash = Hash::make($data['password']);
            $user->save();
        }
        Auth::login($user);
        $request->session()->regenerate();
        $request->session()->put(['auth_version' => $user->auth_version, 'last_activity' => time()]);
        if ($user->role === 'employee') {
            $request->session()->flash('employee_welcome_pending', true);
        }
        RateLimiter::clear($key);
        WorkTrack::audit('login');

        return redirect($user->role === 'admin' ? '/admin' : '/employee');
    }

    public function logout(Request $request)
    {
        WorkTrack::audit('logout');
        Auth::logout();
        $request->session()->invalidate();
        $request->session()->regenerateToken();

        return redirect('/login');
    }

    public function forgot(Request $request)
    {
        if ($request->isMethod('get')) {
            return view('auth.forgot');
        }
        $data = $request->validate(['email' => 'required|email|max:255']);
        WorkTrack::configureMail();
        if (! app()->environment('testing') && config('mail.default') !== 'smtp') {
            throw ValidationException::withMessages(['email' => 'Password recovery email is not configured. Contact your administrator.']);
        }
        $user = User::where('email', Str::lower(trim($data['email'])))->where('is_active', true)->first();
        if ($user) {
            $broker = Password::broker();
            if ($broker->getRepository()->recentlyCreatedToken($user)) {
                throw ValidationException::withMessages(['email' => 'Please wait before requesting another recovery email.']);
            }
            $token = $broker->createToken($user);
            $link = url('/reset-password/'.$token).'?email='.rawurlencode($user->email);
            Mail::raw("Reset your WorkTrack password using this link (valid for 60 minutes):\n".$link, fn ($message) => $message->to($user->email)->subject('WorkTrack password reset'));
        }

        return back()->with('success', 'If an active account matches, a recovery email has been sent.');
    }

    public function reset(Request $request, string $token)
    {
        if ($request->isMethod('get')) {
            return view('auth.reset', ['token' => $token, 'email' => $request->query('email', '')]);
        }
        $data = $request->validate(['email' => 'required|email', 'password' => 'required|string|min:10|max:255|confirmed']);
        $result = Password::reset([...$data, 'token' => $token], function (User $user, string $password) {
            abort_unless($user->is_active, 403);
            DB::transaction(function () use ($user, $password) {
                $locked = User::whereKey($user->id)->lockForUpdate()->firstOrFail();
                $locked->password_hash = Hash::make($password);
                $locked->auth_version++;
                $locked->save();
            });
        });
        if ($result !== Password::PASSWORD_RESET) {
            throw ValidationException::withMessages(['email' => __($result)]);
        }

        return redirect('/login')->with('success', 'Password updated. Sign in again.');
    }

    public function logo()
    {
        $filename = DB::table('app_settings')->where('id', 1)->value('brand_logo_filename');
        abort_unless($filename && is_file(WorkTrack::uploadPath($filename)), 404);

        return response()->file(WorkTrack::uploadPath($filename), ['X-Content-Type-Options' => 'nosniff']);
    }

    public function android()
    {
        abort_unless(DB::table('app_settings')->where('id', 1)->value('android_download_enabled'), 404);
        abort_unless(is_file(config('worktrack.apk_path')), 404);

        return response()->download(config('worktrack.apk_path'), 'worktrack.apk', ['Content-Type' => 'application/vnd.android.package-archive']);
    }
}
