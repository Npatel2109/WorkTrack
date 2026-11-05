<?php

namespace App\Services;

use Carbon\CarbonImmutable;
use Illuminate\Support\Facades\DB;

class LeavePolicy
{
    public static function date(string $date): CarbonImmutable
    {
        return CarbonImmutable::parse($date, 'Asia/Kolkata')->startOfDay();
    }

    public static function cutoff(?CarbonImmutable $day = null): CarbonImmutable
    {
        return ($day ?? WorkTrack::today())->setTimezone('Asia/Kolkata')->setTime(23, 59)->utc();
    }

    public static function overlap(int $employee, string $start, string $end, string $period, ?int $except = null, array $statuses = ['pending', 'approved']): bool
    {
        return DB::table('leave_request')->where('employee_id', $employee)->whereIn('status', $statuses)
            ->where('start_date', '<=', $end)->where('end_date', '>=', $start)
            ->when($period !== 'full', fn ($q) => $q->whereIn('period', ['full', $period]))
            ->when($except, fn ($q) => $q->where('id', '!=', $except))->exists();
    }

    public static function fullDay(int $employee, ?string $date = null): bool
    {
        $periods = DB::table('leave_request')->where('employee_id', $employee)->where('status', 'approved')
            ->where('start_date', '<=', $date ?? WorkTrack::today()->toDateString())
            ->where('end_date', '>=', $date ?? WorkTrack::today()->toDateString())->pluck('period');

        return $periods->contains('full') || ($periods->contains('morning') && $periods->contains('afternoon'));
    }

    public static function summary(object $employee, CarbonImmutable $start, CarbonImmutable $end): array
    {
        $start = self::date($start->toDateString());
        $end = self::date($end->toDateString());
        $leaves = DB::table('leave_request')->where('employee_id', $employee->id)
            ->where('start_date', '<=', $end->toDateString())->where('end_date', '>=', $start->toDateString())
            ->orderBy('start_date')->orderBy('id')->get();
        $totals = ['approved' => 0, 'pending' => 0, 'rejected' => 0, 'automatic' => 0];
        $approved = [];
        foreach ($leaves as $leave) {
            $first = $start->max(self::date($leave->start_date));
            $last = $end->min(self::date($leave->end_date));
            $amount = $leave->period === 'full' ? 1 : .5;
            $totals[$leave->status] += ((int) $first->diffInDays($last) + 1) * $amount;
            if ($leave->status === 'approved') {
                for ($day = $first; $day <= $last; $day = $day->addDay()) {
                    $key = $day->toDateString();
                    $approved[$key] = min(1, ($approved[$key] ?? 0) + $amount);
                }
            }
        }
        $confirmations = DB::table('daily_work_status')->where('employee_id', $employee->id)
            ->whereBetween('work_date', [$start->toDateString(), $end->toDateString()])->orderByDesc('work_date')->get();
        $approvedWork = $confirmations->where('status', 'approved')->pluck('work_date')->all();
        $onsite = DB::table('attendance')->where('employee_id', $employee->id)
            ->where('checkin_time', '>=', $start->utc()->toDateTimeString())
            ->where('checkin_time', '<', $end->addDay()->utc()->toDateTimeString())->pluck('checkin_time')
            ->map(fn ($value) => CarbonImmutable::parse($value, 'UTC')->setTimezone('Asia/Kolkata')->toDateString())->all();
        $policy = DB::table('attendance_policy')->where('id', 1)->first();
        $automatic = [];
        if ($policy) {
            $created = CarbonImmutable::parse($employee->created_date, 'UTC')->setTimezone('Asia/Kolkata')->startOfDay();
            $first = $start->max(self::date($policy->effective_from))->max($created);
            $last = $end->min(WorkTrack::today());
            for ($day = $first; $day <= $last; $day = $day->addDay()) {
                $key = $day->toDateString();
                if (! $day->isSunday() && WorkTrack::now() >= self::cutoff($day)
                    && ! in_array($key, $onsite, true) && ! in_array($key, $approvedWork, true)) {
                    $amount = 1 - ($approved[$key] ?? 0);
                    if ($amount > 0) {
                        $automatic[] = ['date' => $key, 'days' => $amount];
                        $totals['automatic'] += $amount;
                    }
                }
            }
        }

        return compact('leaves', 'totals', 'automatic', 'confirmations') + [
            'total_leave' => $totals['approved'] + $totals['automatic'], 'policy_start' => $policy?->effective_from,
        ];
    }

    public static function balance(object $employee, ?CarbonImmutable $month = null): array
    {
        $month = ($month ?? WorkTrack::today())->startOfMonth();
        $through = WorkTrack::today()->min($month->endOfMonth()->startOfDay());
        $account = DB::table('leave_allowance_account')->where('employee_id', $employee->id)->first();
        $joining = $account ? self::date($account->joining_date)
            : CarbonImmutable::parse($employee->created_date, 'UTC')->setTimezone('Asia/Kolkata')->startOfDay();
        $opening = (float) ($account?->opening_days ?? 0);
        $rates = DB::table('monthly_leave_entitlement')->where('employee_id', $employee->id)->orderBy('month')->get();
        $summary = $joining <= $through ? self::summary($employee, $joining, $through) : null;
        $credited = $joining <= $through ? $opening : 0;
        $used = 0;
        $selectedRate = 2;
        foreach ($rates as $item) {
            if ($item->month <= $month->toDateString()) {
                $selectedRate = (float) $item->days;
            }
        }
        $history = [];
        $rate = 2;
        for ($cursor = $joining->startOfMonth(); $joining <= $through && $cursor <= $through; $cursor = $cursor->addMonth()) {
            foreach ($rates as $item) {
                if ($item->month <= $cursor->toDateString()) {
                    $rate = (float) $item->days;
                }
            }
            $start = $joining->max($cursor)->toDateString();
            $end = $through->min($cursor->endOfMonth()->startOfDay())->toDateString();
            $approved = 0;
            foreach ($summary['leaves'] as $leave) {
                $lower = max($start, $leave->start_date);
                $upper = min($end, $leave->end_date);
                if ($leave->status === 'approved' && $lower <= $upper) {
                    $approved += $leave->period === 'full' ? (int) self::date($lower)->diffInDays(self::date($upper)) + 1 : .5;
                }
            }
            $automatic = collect($summary['automatic'])->filter(fn ($row) => $row['date'] >= $start && $row['date'] <= $end)->sum('days');
            $credited += $rate;
            $used += $approved + $automatic;
            $history[] = ['month' => $cursor->format('Y-m'), 'credit' => $rate, 'approved' => $approved,
                'automatic' => $automatic, 'credited' => $credited, 'used' => $used, 'remaining' => $credited - $used];
        }
        $reserved = 0;
        $future = DB::table('leave_request')->where('employee_id', $employee->id)->where('status', 'approved')
            ->where('end_date', '>', $through->toDateString())->where('end_date', '>=', $joining->toDateString())->get();
        foreach ($future as $leave) {
            $start = max($leave->start_date, $joining->toDateString(), $through->addDay()->toDateString());
            if ($start <= $leave->end_date) {
                $reserved += $leave->period === 'full' ? (int) self::date($start)->diffInDays(self::date($leave->end_date)) + 1 : .5;
            }
        }

        return ['entitlement' => $credited, 'monthly_rate' => $selectedRate, 'used' => $used,
            'remaining' => $credited - $used, 'reserved' => $reserved, 'available' => $credited - $used - $reserved,
            'joining_date' => $joining->toDateString(), 'opening_days' => $opening, 'through' => $through->toDateString(),
            'history' => $history, 'rate_changes' => $rates];
    }
}
