<?php

namespace App\Http\Controllers;

use App\Services\AttendanceService;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Illuminate\Contracts\Encryption\DecryptException;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Crypt;
use Illuminate\Support\Facades\DB;

class AttendanceController extends Controller
{
    private function site(int $id): object
    {
        $site = DB::table('assigned_sites')->where('id', $id)->where('is_active', true)
            ->whereExists(fn ($q) => $q->selectRaw('1')->from('site_assignment')
                ->whereColumn('site_id', 'assigned_sites.id')->where('employee_id', auth()->id())->where('is_active', true))->first();
        abort_unless($site, 404);

        return $site;
    }

    private function visit(int $id): object
    {
        $visit = DB::table('attendance')->where('id', $id)->where('employee_id', auth()->id())->first();
        abort_unless($visit, 404);

        return $visit;
    }

    private function activeVisit(): ?object
    {
        return DB::table('attendance')->where('employee_id', auth()->id())->where('status', 'onsite')->first();
    }

    private function allowedStart(): void
    {
        $periods = DB::table('leave_request')->where('employee_id', auth()->id())->where('status', 'approved')
            ->where('start_date', '<=', WorkTrack::today()->toDateString())->where('end_date', '>=', WorkTrack::today()->toDateString())->pluck('period');
        abort_if($periods->contains('full') || ($periods->contains('morning') && $periods->contains('afternoon')), 409, 'You are on approved full-day leave today.');
        abort_if($this->activeVisit(), 409, 'Finish your current visit before starting another.');
        abort_if(WorkTrack::now()->gte(AttendanceService::cutoff(WorkTrack::now())), 409, 'Daily cutoff reached. Start again after midnight IST.');
    }

    private function coordinates(Request $request): array
    {
        $values = $request->validate(['latitude' => 'required|numeric|between:-90,90', 'longitude' => 'required|numeric|between:-180,180']);
        abort_if(is_bool($values['latitude']) || is_bool($values['longitude']), 422, 'Invalid GPS coordinates.');

        return ['latitude' => (float) $values['latitude'], 'longitude' => (float) $values['longitude']];
    }

    private function verification(Request $request, int $id): array
    {
        $value = $request->session()->get('visit_verification', []);
        abort_unless(($value['site_id'] ?? null) === $id && isset($value['verified_at']), 403, 'Capture your location first.');
        $age = WorkTrack::now()->getTimestamp() - $value['verified_at'];
        abort_if($age < 0 || $age > 600, 403, 'Location capture expired. Capture it again.');

        return $value;
    }

    public function start(Request $request, int $id)
    {
        $this->site($id);
        $this->allowedStart();
        $request->session()->forget('visit_verification');

        return redirect('/visit/'.$id.'/checkin-location');
    }

    public function checkinLocation(int $id)
    {
        $site = $this->site($id);
        $this->allowedStart();

        return view('attendance.location', ['site' => $site, 'flow' => 'checkin', 'id' => $id]);
    }

    public function verifyCheckin(Request $request, int $id)
    {
        $site = $this->site($id);
        $this->allowedStart();
        $coordinates = $this->coordinates($request);
        $request->session()->put('visit_verification', $coordinates + ['site_id' => $id, 'verified_at' => WorkTrack::now()->getTimestamp()]);
        $distance = AttendanceService::distance($site->latitude, $site->longitude, ...array_values($coordinates));

        return response()->json(['verified' => true, 'distance' => round($distance, 1),
            'within_radius' => $distance <= $site->allowed_radius, 'allowed' => $site->allowed_radius,
            'next' => url('/visit/'.$id.'/camera')]);
    }

    public function camera(Request $request, int $id)
    {
        $site = $this->site($id);
        $this->allowedStart();
        $this->verification($request, $id);

        return view('attendance.camera', compact('site'));
    }

    public function checkin(Request $request, int $id)
    {
        $request->validate(['selfie' => 'required|string|max:7500000']);
        DB::transaction(function () use ($request, $id) {
            DB::table('user')->where('id', auth()->id())->lockForUpdate()->first();
            $site = $this->site($id);
            $this->allowedStart();
            $value = $this->verification($request, $id);
            $filename = WorkTrack::saveSelfie($request->input('selfie'));
            $now = WorkTrack::now();
            $captured = CarbonImmutable::createFromTimestampUTC($value['verified_at']);
            $visitId = DB::table('attendance')->insertGetId([
                'employee_id' => auth()->id(), 'site_id' => $id, 'checkin_time' => $now,
                'checkin_lat' => $value['latitude'], 'checkin_lon' => $value['longitude'],
                'checkin_selfie' => $filename, 'status' => 'onsite', 'site_latitude' => $site->latitude,
                'site_longitude' => $site->longitude, 'site_radius_m' => $site->allowed_radius,
                'checkin_distance_m' => AttendanceService::distance($site->latitude, $site->longitude, $value['latitude'], $value['longitude']),
                'checkin_location_time' => $captured, 'last_location_lat' => $value['latitude'],
                'last_location_lon' => $value['longitude'], 'last_location_time' => $captured, 'auto_closed' => false,
            ]);
            WorkTrack::audit('site_checkin', 'Attendance '.$visitId);
        });
        $request->session()->forget('visit_verification');

        return redirect('/employee')->with('success', 'Site started. Location and distance recorded.');
    }

    public function checkoutLocation(int $id)
    {
        $visit = $this->visit($id);
        if ($visit->status === 'report_pending') {
            return redirect('/visit/'.$id.'/report');
        }
        abort_unless($visit->status === 'onsite', 404);
        $site = DB::table('assigned_sites')->find($visit->site_id);

        return view('attendance.location', ['site' => $site, 'flow' => 'checkout', 'id' => $id]);
    }

    public function checkout(Request $request, int $id, AttendanceService $service)
    {
        $visit = $this->visit($id);
        if ($visit->status === 'onsite') {
            $reason = null;
            $coordinates = null;
            if ($request->input('location_unavailable') === true) {
                $reason = $request->validate(['reason' => 'required|in:permission_denied,position_unavailable,timeout,unsupported,insecure_context'])['reason'];
            } else {
                $coordinates = $this->coordinates($request);
            }
            abort_unless($service->stop($id, $coordinates, $reason), 409, 'Visit already stopped.');
            $visit = $this->visit($id);
        }
        abort_unless($visit->status === 'report_pending', 404);

        return response()->json([
            'verified' => true, 'distance' => $visit->checkout_distance_m === null ? null : round($visit->checkout_distance_m, 1),
            'within_radius' => $visit->checkout_distance_m === null || $visit->site_radius_m === null || $visit->checkout_distance_m <= $visit->site_radius_m,
            'location_source' => $visit->checkout_location_source, 'next' => url('/visit/'.$id.'/report'),
        ]);
    }

    public function location(Request $request, int $id)
    {
        $visit = $this->visit($id);
        $coordinates = $this->coordinates($request);
        $changed = DB::table('attendance')->where('id', $id)->where('status', 'onsite')->update([
            'last_location_lat' => $coordinates['latitude'], 'last_location_lon' => $coordinates['longitude'],
            'last_location_time' => WorkTrack::now(),
        ]);
        if (! $changed) {
            return response()->json(['error' => 'This visit has stopped.', 'active' => false], 409);
        }
        $site = DB::table('assigned_sites')->find($visit->site_id);
        $distance = AttendanceService::distance($visit->site_latitude ?? $site->latitude, $visit->site_longitude ?? $site->longitude, ...array_values($coordinates));

        return response()->json(['recorded' => true, 'distance' => round($distance, 1),
            'within_radius' => $distance <= ($visit->site_radius_m ?? $site->allowed_radius), 'active' => true]);
    }

    private function monitorResponse(?object $visit, bool $token = false)
    {
        $values = ['active' => false];
        if ($visit && $visit->status === 'onsite' && WorkTrack::now()->lt(AttendanceService::cutoff($visit->checkin_time))) {
            $site = DB::table('assigned_sites')->find($visit->site_id);
            $cutoff = AttendanceService::cutoff($visit->checkin_time)->toIso8601String();
            $values = ['active' => true, 'attendance_id' => $visit->id,
                'latitude' => $visit->site_latitude ?? $site->latitude, 'longitude' => $visit->site_longitude ?? $site->longitude,
                'radius_m' => $visit->site_radius_m ?? $site->allowed_radius, 'cutoff_time' => $cutoff];
            if ($token) {
                $values['monitoring_token'] = Crypt::encryptString(json_encode(['visit' => $visit->id, 'cutoff' => $cutoff], JSON_THROW_ON_ERROR));
            }
        }

        return response()->json($values)->header('Cache-Control', 'private, no-store');
    }

    public function active()
    {
        return $this->monitorResponse($this->activeVisit(), true);
    }

    public function monitor(Request $request, AttendanceService $service)
    {
        try {
            $token = $request->bearerToken();
            $values = $token ? json_decode(Crypt::decryptString($token), true, 512, JSON_THROW_ON_ERROR) : null;
        } catch (DecryptException|\JsonException $error) {
            return response()->json(['error' => 'Invalid or expired visit-monitoring token.'], 401)->header('Cache-Control', 'private, no-store');
        }
        $visit = isset($values['visit']) ? DB::table('attendance')->find($values['visit']) : null;
        $activeUser = $visit && DB::table('user')->where('id', $visit->employee_id)->where('is_active', true)->exists();
        if (! $visit || ! $activeUser || ($values['cutoff'] ?? null) !== AttendanceService::cutoff($visit->checkin_time)->toIso8601String() || WorkTrack::now()->gte(AttendanceService::cutoff($visit->checkin_time))) {
            return response()->json(['error' => 'Invalid or expired visit-monitoring token.'], 401)->header('Cache-Control', 'private, no-store');
        }
        $service->closeOverdue();

        return $this->monitorResponse(DB::table('attendance')->find($visit->id));
    }

    public function report(Request $request, int $id)
    {
        $visit = $this->visit($id);
        abort_unless($visit->status === 'report_pending', 403);
        if ($request->isMethod('post')) {
            $data = $request->validate(['activity_detail' => 'required|string|max:50000', 'billing' => 'required|in:billable,non_billable', 'engineer_name' => 'required|string|max:120']);
            DB::transaction(function () use ($data, $id) {
                $changed = DB::table('attendance')->where('id', $id)->where('status', 'report_pending')->update(['status' => 'completed']);
                abort_unless($changed, 409, 'Report already submitted.');
                DB::table('site_report')->insert([
                    'attendance_id' => $id, 'work_performed' => $data['activity_detail'], 'is_billable' => $data['billing'] === 'billable',
                    'engineer_name' => $data['engineer_name'], 'activities_completed' => '', 'issues_found' => '',
                    'recommendations' => '', 'materials_used' => '', 'remarks' => '', 'customer_representative_name' => '',
                    'employee_signature' => '', 'customer_signature' => '', 'report_date' => WorkTrack::now(),
                ]);
                WorkTrack::audit('site_report_submitted', 'Attendance '.$id);
            });

            return redirect('/employee')->with('success', 'Site report submitted.')->with('employee_thank_you_pending', true);
        }

        return view('attendance.report', compact('visit'));
    }

    public function media(string $filename)
    {
        $allowed = auth()->user()->role === 'admin';
        if (! $allowed) {
            $allowed = DB::table('attendance')->where('employee_id', auth()->id())->where('checkin_selfie', $filename)->exists() ||
                DB::table('site_report')->join('attendance', 'attendance.id', '=', 'site_report.attendance_id')
                    ->where('attendance.employee_id', auth()->id())
                    ->where(fn ($q) => $q->where('employee_signature', $filename)->orWhere('customer_signature', $filename))->exists();
        }
        abort_unless($allowed && is_file(WorkTrack::uploadPath($filename)), 404);
        $mime = mime_content_type(WorkTrack::uploadPath($filename));
        abort_unless(in_array($mime, ['image/png', 'image/jpeg', 'image/webp'], true), 404);

        return response()->file(WorkTrack::uploadPath($filename), ['Content-Type' => $mime, 'X-Content-Type-Options' => 'nosniff', 'Cache-Control' => 'private, no-store']);
    }
}
