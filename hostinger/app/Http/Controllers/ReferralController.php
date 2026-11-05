<?php

namespace App\Http\Controllers;

use App\Services\WorkTrack;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Str;

class ReferralController extends Controller
{
    public function employee(Request $request)
    {
        if ($request->isMethod('post')) {
            $data = $request->validate(['candidate_name' => ['required', 'string', 'max:120'],
                'candidate_email' => ['required', 'email', 'max:255'], 'candidate_mobile' => ['nullable', 'string', 'max:40'],
                'notes' => ['nullable', 'string', 'max:10000'], 'resume' => ['required', 'file', 'mimes:pdf', 'extensions:pdf', 'min:1', 'max:5120']]);
            $resume = $request->file('resume');
            $original = Str::limit(preg_replace('/[^\pL\pN._ -]/u', '_', basename(str_replace('\\', '/', $resume->getClientOriginalName()))), 250, '');
            $filename = str_replace('-', '', (string) Str::uuid()).'.pdf';
            $folder = WorkTrack::uploadPath().DIRECTORY_SEPARATOR.'referral-resumes';
            if (! is_dir($folder) && ! mkdir($folder, 0700, true) && ! is_dir($folder)) {
                throw new \RuntimeException('Unable to create private referral storage.');
            }
            $resume->move($folder, $filename);
            $path = $folder.DIRECTORY_SEPARATOR.$filename;
            unset($data['resume']);
            $data['candidate_mobile'] = $data['candidate_mobile'] ?? '';
            $data['notes'] = $data['notes'] ?? '';
            $data['candidate_email'] = strtolower($data['candidate_email']);
            try {
                DB::transaction(function () use ($data, $filename, $original) {
                    DB::table('candidate_referral')->insert($data + ['employee_id' => auth()->id(),
                        'resume_filename' => $filename, 'resume_original_filename' => $original, 'status' => 'submitted', 'created_at' => WorkTrack::now()]);
                    WorkTrack::audit('candidate_referred', $data['candidate_name']);
                });
            } catch (\Throwable $error) {
                if (is_file($path)) {
                    unlink($path);
                }
                throw $error;
            }
            $settings = DB::table('app_settings')->where('id', 1)->first();
            if ($settings?->referral_notification_email) {
                try {
                    $this->notify($settings, $data, $path, $original);
                } catch (\Throwable $error) {
                    report($error);

                    return redirect('/employee/referrals')->with('error', 'Referral saved, but notification delivery failed. Please inform your administrator; do not resubmit.');
                }
            }

            return redirect('/employee/referrals')->with('success', 'Referral saved.'.($settings?->referral_notification_email ? ' Notification sent.' : ''));
        }

        return view('referrals.employee', ['referrals' => DB::table('candidate_referral')->where('employee_id', auth()->id())->orderByDesc('created_at')->paginate(30)]);
    }

    private function notify(object $settings, array $data, string $path, string $original): void
    {
        if (! $settings->mail_server && in_array(config('mail.mailers.'.config('mail.default').'.transport'), ['log', 'array', 'null'], true)) {
            throw new \RuntimeException('Referral notification requires a configured delivery transport.');
        }
        $mailer = $settings->mail_server ? Mail::build(['transport' => 'smtp', 'host' => $settings->mail_server,
            'port' => $settings->mail_port, 'scheme' => $settings->mail_security === 'ssl' ? 'smtps' : 'smtp',
            'auto_tls' => $settings->mail_security !== 'none', 'require_tls' => $settings->mail_security === 'starttls',
            'username' => $settings->mail_username, 'password' => $settings->mail_password, 'timeout' => 20]) : Mail::mailer();
        $text = auth()->user()->name." referred {$data['candidate_name']}.\nEmail: {$data['candidate_email']}\nMobile: {$data['candidate_mobile']}\nNotes: {$data['notes']}";
        $mailer->raw($text, function ($message) use ($settings, $path, $original) {
            if ($settings->mail_sender) {
                $message->from($settings->mail_sender);
            }
            $message->to($settings->referral_notification_email)->subject('New WorkTrack candidate referral')
                ->attach($path, ['as' => $original, 'mime' => 'application/pdf']);
        });
    }

    public function index()
    {
        return view('referrals.admin', ['referrals' => DB::table('candidate_referral as r')
            ->join('user as u', 'u.id', '=', 'r.employee_id')->select('r.*', 'u.name as employee_name')
            ->orderByDesc('r.created_at')->paginate(50)]);
    }

    public function download(int $id)
    {
        $referral = DB::table('candidate_referral')->where('id', $id)->firstOrFail();
        abort_unless(preg_match('/^[a-f0-9]{32}\.(pdf|doc|docx)$/', $referral->resume_filename), 404);
        $path = WorkTrack::uploadPath().DIRECTORY_SEPARATOR.'referral-resumes'.DIRECTORY_SEPARATOR.$referral->resume_filename;
        abort_unless(is_file($path), 404);
        $original = preg_replace('/[\r\n\/\\\\]/', '_', $referral->resume_original_filename);

        return response()->download($path, $original ?: $referral->resume_filename, ['X-Content-Type-Options' => 'nosniff', 'Cache-Control' => 'private, no-store']);
    }
}
