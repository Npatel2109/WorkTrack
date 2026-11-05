<?php

namespace App\Services;

use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Str;

class WorkTrack
{
    public static function now(): CarbonImmutable
    {
        return CarbonImmutable::now('UTC');
    }

    public static function today(): CarbonImmutable
    {
        return self::now()->setTimezone('Asia/Kolkata')->startOfDay();
    }

    public static function audit(string $action, ?string $details = null, bool $system = false): void
    {
        DB::table('audit_log')->insert([
            'user_id' => ! $system && auth()->id() && DB::table('user')->where('id', auth()->id())->exists() ? auth()->id() : null,
            'action' => $action, 'details' => $details,
            'ip_address' => request()->ip(), 'created_at' => self::now(),
        ]);
    }

    public static function uploadPath(?string $filename = null): string
    {
        $root = config('worktrack.upload_path');
        if (! is_dir($root) && ! mkdir($root, 0700, true) && ! is_dir($root)) {
            throw new \RuntimeException('Cannot create private upload directory.');
        }
        if ($filename !== null && (! preg_match('/\A[a-zA-Z0-9_.-]+\z/D', $filename) || in_array($filename, ['.', '..'], true))) {
            abort(404);
        }

        return $root.($filename === null ? '' : DIRECTORY_SEPARATOR.$filename);
    }

    public static function saveSelfie(string $data): string
    {
        abort_unless(preg_match('/\Adata:image\/(?:png|jpeg);base64,([A-Za-z0-9+\/=]+)\z/D', $data, $match), 422, 'Capture a PNG or JPEG selfie.');
        $raw = base64_decode($match[1], true);
        abort_if($raw === false || strlen($raw) > 5 * 1024 * 1024, 422, 'Selfie exceeds 5 MB or is invalid.');
        $size = @getimagesizefromstring($raw);
        abort_if($size === false || $size[0] * $size[1] > 20_000_000, 422, 'Invalid image or image exceeds 20 megapixels.');
        $image = @imagecreatefromstring($raw);
        abort_if($image === false, 422, 'Cannot decode selfie.');
        $filename = Str::uuid()->toString().'.png';
        try {
            if (! imagepng($image, self::uploadPath($filename))) {
                throw new \RuntimeException('Could not save selfie.');
            }
        } finally {
            imagedestroy($image);
        }

        return $filename;
    }

    public static function configureMail(): void
    {
        $settings = DB::table('app_settings')->where('id', 1)->first();
        if ($settings && $settings->mail_server) {
            config([
                'mail.default' => 'smtp',
                'mail.mailers.smtp.host' => $settings->mail_server,
                'mail.mailers.smtp.port' => $settings->mail_port,
                'mail.mailers.smtp.scheme' => $settings->mail_security === 'ssl' ? 'smtps' : 'smtp',
                'mail.mailers.smtp.username' => $settings->mail_username,
                'mail.mailers.smtp.password' => $settings->mail_password,
                'mail.from.address' => $settings->mail_sender,
            ]);
            Mail::purge('smtp');
        }
    }
}
