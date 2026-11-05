<?php

namespace App\Services;

use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

class AttendanceService
{
    public static function cutoff(string|CarbonImmutable $value): CarbonImmutable
    {
        return CarbonImmutable::parse($value, 'UTC')->setTimezone('Asia/Kolkata')->setTime(23, 59)->utc();
    }

    public static function distance(float $lat1, float $lon1, float $lat2, float $lon2): float
    {
        $a = sin(deg2rad($lat2 - $lat1) / 2) ** 2 +
            cos(deg2rad($lat1)) * cos(deg2rad($lat2)) * sin(deg2rad($lon2 - $lon1) / 2) ** 2;

        return 2 * 6371000 * asin(sqrt(min(1, $a)));
    }

    public function closeOverdue(): int
    {
        $now = WorkTrack::now();
        $threshold = self::cutoff($now);
        if ($now->lt($threshold)) {
            $threshold = $threshold->subDay();
        }
        $count = 0;
        foreach (DB::table('attendance')->where('status', 'onsite')->where('checkin_time', '<=', $threshold)->pluck('id') as $id) {
            $count += $this->stop($id, null, 'daily_cutoff', true) ? 1 : 0;
        }

        return $count;
    }

    public function stop(int $id, ?array $coordinates, ?string $reason, bool $automatic = false): bool
    {
        return DB::transaction(function () use ($id, $coordinates, $reason, $automatic) {
            $visit = DB::table('attendance')->where('id', $id)->where('status', 'onsite')->lockForUpdate()->first();
            if (! $visit) {
                return false;
            }
            $now = WorkTrack::now();
            $cutoff = self::cutoff($visit->checkin_time);
            if ($now->gte($cutoff)) {
                $now = $cutoff;
                $coordinates = null;
                $reason = 'daily_cutoff';
                $automatic = true;
            }
            $lat = $coordinates['latitude'] ?? $visit->last_location_lat ?? $visit->checkin_lat;
            $lon = $coordinates['longitude'] ?? $visit->last_location_lon ?? $visit->checkin_lon;
            $captured = $coordinates ? $now : ($visit->last_location_time ?? $visit->checkin_location_time ?? $visit->checkin_time);
            $site = DB::table('assigned_sites')->find($visit->site_id);
            $distance = self::distance($visit->site_latitude ?? $site->latitude, $visit->site_longitude ?? $site->longitude, $lat, $lon);
            $values = [
                'checkout_time' => $now, 'checkout_lat' => $lat, 'checkout_lon' => $lon,
                'checkout_distance_m' => $distance, 'checkout_location_time' => $captured,
                'checkout_location_source' => $coordinates ? 'current' : 'last_known',
                'checkout_location_reason' => $reason, 'auto_closed' => $automatic, 'status' => 'report_pending',
            ];
            if ($coordinates) {
                $values += ['last_location_lat' => $lat, 'last_location_lon' => $lon, 'last_location_time' => $now];
            }
            DB::table('attendance')->where('id', $id)->update($values);
            WorkTrack::audit($automatic ? 'site_auto_checkout' : 'site_checkout', "Attendance {$id}; distance_m={$distance}; reason={$reason}", $automatic);

            return true;
        });
    }
}
