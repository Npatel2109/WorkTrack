<?php

namespace App\Http\Controllers;

use App\Services\LeavePolicy;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Validation\Rule;
use Illuminate\Validation\ValidationException;

class LeaveController extends Controller
{
    private function employee(int $id): object
    {
        return DB::table('user')->where('id', $id)->where('role', 'employee')->lockForUpdate()->firstOrFail();
    }

    private function fail(string $message): never
    {
        throw ValidationException::withMessages(['leave' => $message]);
    }

    private function details(Request $request, bool $future = false): array
    {
        $data = $request->validate([
            'start_date' => ['required', 'date_format:Y-m-d', ...($future ? ['after_or_equal:'.WorkTrack::today()->toDateString()] : [])],
            'end_date' => ['required', 'date_format:Y-m-d', 'after_or_equal:start_date'],
            'period' => ['required', Rule::in(['full', 'morning', 'afternoon'])],
            'reason' => ['required', 'string', 'max:2000'],
        ]);
        if ($data['period'] !== 'full' && $data['start_date'] !== $data['end_date']) {
            $this->fail('Half-day leave must begin and end on the same date.');
        }

        return $data;
    }

    private function requestLeave(int $employee, array $data): void
    {
        DB::transaction(function () use ($employee, $data) {
            $this->employee($employee);
            if (LeavePolicy::overlap($employee, $data['start_date'], $data['end_date'], $data['period'])) {
                $this->fail('These dates overlap pending or approved leave.');
            }
            $today = WorkTrack::today()->toDateString();
            if ($data['start_date'] <= $today && $data['end_date'] >= $today
                && DB::table('attendance')->where('employee_id', $employee)->where('status', 'onsite')->exists()) {
                $this->fail('Stop your active site visit before requesting leave today.');
            }
            DB::table('leave_request')->insert($data + ['employee_id' => $employee, 'status' => 'pending', 'created_at' => WorkTrack::now()]);
            WorkTrack::audit('leave_requested', json_encode($data));
        });
    }

    public function employeeLeaves(Request $request)
    {
        if ($request->isMethod('post')) {
            $this->requestLeave(auth()->id(), $this->details($request, true));

            return redirect('/employee/leaves')->with('success', 'Leave submitted for approval.');
        }

        return $this->listing($request, false);
    }

    public function index(Request $request)
    {
        return $this->listing($request, true);
    }

    private function listing(Request $request, bool $admin)
    {
        $request->validate(['from_date' => ['nullable', 'date_format:Y-m-d'],
            'to_date' => ['nullable', 'date_format:Y-m-d', 'after_or_equal:from_date'],
            'leave_month' => ['nullable', 'date_format:Y-m'], 'employee_id' => ['nullable', 'integer'],
            'employee_search' => ['nullable', 'string', 'max:255'],
            'status' => ['nullable', Rule::in(['all', 'pending', 'approved', 'rejected', 'today'])]]);
        $start = $request->input('from_date', WorkTrack::today()->toDateString());
        $end = $request->input('to_date', WorkTrack::today()->toDateString());
        $employees = DB::table('user')->where('role', 'employee')
            ->when(! $admin, fn ($q) => $q->where('id', auth()->id()))
            ->when($admin && $request->filled('employee_id'), fn ($q) => $q->where('id', $request->integer('employee_id')))
            ->when($admin && $request->filled('employee_search'), function ($q) use ($request) {
                $term = '%'.$request->string('employee_search').'%';
                $q->where(fn ($q) => $q->where('name', 'like', $term)->orWhere('email', 'like', $term)->orWhere('employee_code', 'like', $term));
            })->orderBy('name')->get();
        $query = DB::table('leave_request as l')->join('user as u', 'u.id', '=', 'l.employee_id')
            ->select('l.*', 'u.name as employee_name')->whereIn('l.employee_id', $employees->pluck('id'))
            ->when($start, fn ($q) => $q->where('l.end_date', '>=', $start))
            ->when($end, fn ($q) => $q->where('l.start_date', '<=', $end));
        $status = $request->input('status', 'all');
        if ($status === 'today') {
            $query->where('l.status', 'approved')->where('u.is_active', true)
                ->where('l.start_date', '<=', WorkTrack::today()->toDateString())->where('l.end_date', '>=', WorkTrack::today()->toDateString());
        } elseif ($status !== 'all') {
            $query->where('l.status', $status);
        }
        $month = LeavePolicy::date($request->input('leave_month', WorkTrack::today()->format('Y-m')).'-01');
        $balances = $employees->map(function ($employee) use ($month, $start, $end) {
            $firstLeave = DB::table('leave_request')->where('employee_id', $employee->id)->min('start_date');
            $lastLeave = DB::table('leave_request')->where('employee_id', $employee->id)->max('end_date');
            $created = CarbonImmutable::parse($employee->created_date, 'UTC')->setTimezone('Asia/Kolkata')->toDateString();

            return ['employee' => $employee, 'balance' => LeavePolicy::balance($employee, $month),
                'summary' => LeavePolicy::summary($employee,
                    LeavePolicy::date($start ?: min($created, $firstLeave ?? $created)),
                    LeavePolicy::date($end ?: max(WorkTrack::today()->toDateString(), $lastLeave ?? WorkTrack::today()->toDateString())))];
        });

        return view('leave.index', ['admin' => $admin, 'leaves' => $query->orderByDesc('l.created_at')->paginate(50)->withQueryString(),
            'balances' => $balances, 'employees' => DB::table('user')->where('role', 'employee')->orderBy('name')->get(), 'month' => $month]);
    }

    public function form(Request $request, ?int $id = null)
    {
        $leave = $id ? DB::table('leave_request')->where('id', $id)->firstOrFail() : null;
        if ($request->isMethod('post')) {
            $data = $this->details($request);
            $request->validate(['employee_id' => ['required', 'integer']]);
            DB::transaction(function () use ($request, $data, $id) {
                $employee = $this->employee($request->integer('employee_id'));
                $existing = $id ? DB::table('leave_request')->where('id', $id)->lockForUpdate()->firstOrFail() : null;
                if ($existing && $existing->employee_id !== $employee->id) {
                    $this->fail('The employee owning a leave cannot be changed.');
                }
                if (LeavePolicy::overlap($employee->id, $data['start_date'], $data['end_date'], $data['period'], $id)) {
                    $this->fail('These dates overlap another pending or approved request.');
                }
                if ($existing && collect($data)->every(fn ($value, $key) => $value == $existing->$key)) {
                    return;
                }
                $changes = $data + ['status' => 'pending', 'reviewed_by_id' => null, 'reviewed_at' => null];
                if ($id) {
                    DB::table('leave_request')->where('id', $id)->update($changes);
                } else {
                    DB::table('leave_request')->insert($changes + ['employee_id' => $employee->id, 'created_at' => WorkTrack::now()]);
                }
                WorkTrack::audit('leave_details_saved', json_encode(['id' => $id, 'before' => $existing, 'after' => $changes]));
            });

            return redirect('/admin/leaves?employee_id='.$request->integer('employee_id'))->with('success', 'Leave saved. Review approval separately.');
        }

        return view('leave.form', ['leave' => $leave, 'employees' => DB::table('user')->where('role', 'employee')->orderBy('name')->get()]);
    }

    public function review(Request $request, int $id)
    {
        $request->validate(['decision' => ['required', Rule::in(['approved', 'rejected'])]]);
        DB::transaction(function () use ($request, $id) {
            $initial = DB::table('leave_request')->where('id', $id)->firstOrFail();
            $employee = $this->employee($initial->employee_id);
            $leave = DB::table('leave_request')->where('id', $id)->lockForUpdate()->firstOrFail();
            if ($leave->status !== 'pending') {
                $this->fail('This leave has already been reviewed.');
            }
            if ($request->decision === 'approved') {
                if (! $employee->is_active) {
                    $this->fail('Inactive employee leave cannot be approved.');
                }
                if (LeavePolicy::overlap($employee->id, $leave->start_date, $leave->end_date, $leave->period, $id, ['approved'])) {
                    $this->fail('This request overlaps approved leave.');
                }
                $today = WorkTrack::today()->toDateString();
                $otherHalf = DB::table('leave_request')->where('employee_id', $employee->id)->where('status', 'approved')
                    ->where('start_date', '<=', $today)->where('end_date', '>=', $today)->exists();
                if ($leave->start_date <= $today && $leave->end_date >= $today && ($leave->period === 'full' || $otherHalf)
                    && DB::table('attendance')->where('employee_id', $employee->id)->where('status', 'onsite')->exists()) {
                    $this->fail('Stop the active visit before approving full-day leave today.');
                }
            }
            DB::table('leave_request')->where('id', $id)->update(['status' => $request->decision,
                'reviewed_by_id' => auth()->id(), 'reviewed_at' => WorkTrack::now()]);
            WorkTrack::audit('leave_'.$request->decision, "Leave $id");
        });

        return back()->with('success', 'Leave reviewed.');
    }

    public function entitlement(Request $request)
    {
        $data = $request->validate(['employee_id' => ['required', 'integer'], 'month' => ['required', 'date_format:Y-m'],
            'days' => ['required', 'numeric', 'min:0', 'max:31', 'multiple_of:0.5']]);
        DB::transaction(function () use ($data) {
            $employee = $this->employee($data['employee_id']);
            $balance = LeavePolicy::balance($employee);
            $month = $data['month'].'-01';
            if ($month < LeavePolicy::date($balance['joining_date'])->startOfMonth()->toDateString()) {
                $this->fail('Entitlement cannot begin before the joining month.');
            }
            DB::table('monthly_leave_entitlement')->updateOrInsert(['employee_id' => $employee->id, 'month' => $month],
                ['days' => $data['days'], 'updated_by_id' => auth()->id(), 'updated_at' => WorkTrack::now()]);
            WorkTrack::audit('leave_entitlement_updated', json_encode($data));
        });

        return back()->with('success', 'Rate saved from this month onward; previous credits preserved.');
    }

    public function account(Request $request)
    {
        $data = $request->validate(['employee_id' => ['required', 'integer'],
            'joining_date' => ['required', 'date_format:Y-m-d', 'before_or_equal:'.WorkTrack::today()->toDateString()],
            'opening_days' => ['required', 'numeric', 'min:0', 'max:999999', 'multiple_of:0.5']]);
        DB::transaction(function () use ($data) {
            $this->employee($data['employee_id']);
            if (DB::table('monthly_leave_entitlement')->where('employee_id', $data['employee_id'])
                ->where('month', '<', LeavePolicy::date($data['joining_date'])->startOfMonth()->toDateString())->exists()) {
                $this->fail('Joining date cannot exclude an existing rate change.');
            }
            DB::table('leave_allowance_account')->updateOrInsert(['employee_id' => $data['employee_id']],
                ['joining_date' => $data['joining_date'], 'opening_days' => $data['opening_days'],
                    'updated_by_id' => auth()->id(), 'updated_at' => WorkTrack::now()]);
            WorkTrack::audit('leave_account_updated', json_encode($data));
        });

        return back()->with('success', 'Joining date and opening balance saved.');
    }

    public function submitWorkStatus(Request $request)
    {
        if (WorkTrack::now() >= LeavePolicy::cutoff()) {
            $this->fail('The 23:59 IST confirmation deadline has passed.');
        }
        $request->validate(['work_type' => ['required', Rule::in(['onsite', 'leave', 'wfh', 'travelling', 'training'])]]);
        if ($request->work_type === 'onsite') {
            // The attendance controller owns assignment checks and check-in verification.
            return app(AttendanceController::class)->start($request, (int) $request->input('site_id'));
        }
        if ($request->work_type === 'leave') {
            $request->merge(['start_date' => WorkTrack::today()->toDateString(), 'end_date' => WorkTrack::today()->toDateString()]);
            $this->requestLeave(auth()->id(), $this->details($request, true));

            return redirect('/employee')->with('success', 'Leave submitted for approval.');
        }
        $data = $request->validate(['work_type' => ['required', Rule::in(['wfh', 'travelling', 'training'])],
            'reason' => ['required', 'string', 'max:2000']]);
        DB::transaction(function () use ($data) {
            $this->employee(auth()->id());
            if (WorkTrack::now() >= LeavePolicy::cutoff() || LeavePolicy::fullDay(auth()->id())) {
                $this->fail('Confirmation deadline passed or full-day leave is already approved.');
            }
            if (DB::table('attendance')->where('employee_id', auth()->id())
                ->where('checkin_time', '>=', WorkTrack::today()->utc()->toDateTimeString())->exists()) {
                $this->fail('A site visit is already recorded today.');
            }
            if (DB::table('daily_work_status')->where('employee_id', auth()->id())->where('work_date', WorkTrack::today()->toDateString())->exists()) {
                $this->fail('Today’s confirmation has already been submitted.');
            }
            DB::table('daily_work_status')->insert($data + ['employee_id' => auth()->id(), 'work_date' => WorkTrack::today()->toDateString(),
                'status' => 'pending', 'created_at' => WorkTrack::now()]);
            WorkTrack::audit('work_status_submitted', json_encode($data));
        });

        return redirect('/employee')->with('success', 'Work status submitted for approval.');
    }

    public function workStatuses(Request $request)
    {
        $request->validate(['status' => ['nullable', Rule::in(['all', 'pending', 'approved', 'rejected'])]]);
        $items = DB::table('daily_work_status as w')->join('user as u', 'u.id', '=', 'w.employee_id')
            ->select('w.*', 'u.name as employee_name')->when($request->filled('status') && $request->status !== 'all',
                fn ($q) => $q->where('w.status', $request->status))->orderByDesc('w.work_date')->orderByDesc('w.id')->paginate(30)->withQueryString();
        $summaries = DB::table('user')->where('role', 'employee')->orderBy('name')->get()->map(fn ($employee) => [
            'employee' => $employee, 'summary' => LeavePolicy::summary($employee, WorkTrack::today()->startOfMonth(), WorkTrack::today()->endOfMonth())]);

        return view('leave.work-status', compact('items', 'summaries'));
    }

    public function reviewWork(Request $request, int $id)
    {
        $request->validate(['decision' => ['required', Rule::in(['approved', 'rejected'])]]);
        DB::transaction(function () use ($request, $id) {
            $initial = DB::table('daily_work_status')->where('id', $id)->firstOrFail();
            $this->employee($initial->employee_id);
            $item = DB::table('daily_work_status')->where('id', $id)->lockForUpdate()->firstOrFail();
            if ($item->status !== 'pending') {
                $this->fail('This confirmation has already been reviewed.');
            }
            DB::table('daily_work_status')->where('id', $id)->update(['status' => $request->decision,
                'reviewed_by_id' => auth()->id(), 'reviewed_at' => WorkTrack::now()]);
            WorkTrack::audit('work_status_'.$request->decision, "Confirmation $id");
        });

        return back()->with('success', 'Work status reviewed.');
    }

    public function editWork(Request $request, int $id)
    {
        $item = DB::table('daily_work_status')->where('id', $id)->firstOrFail();
        if ($request->isMethod('post')) {
            $data = $request->validate(['work_type' => ['required', Rule::in(['wfh', 'travelling', 'training'])],
                'reason' => ['required', 'string', 'max:2000']]);
            DB::transaction(function () use ($item, $id, $data) {
                $this->employee($item->employee_id);
                $current = DB::table('daily_work_status')->where('id', $id)->lockForUpdate()->firstOrFail();
                if ($current->work_type === $data['work_type'] && $current->reason === $data['reason']) {
                    return;
                }
                DB::table('daily_work_status')->where('id', $id)->update($data + ['status' => 'pending', 'reviewed_by_id' => null, 'reviewed_at' => null]);
                WorkTrack::audit('work_status_corrected', json_encode(['id' => $id, 'before' => $current, 'after' => $data]));
            });

            return redirect('/admin/work-status')->with('success', 'Correction saved as pending.');
        }

        return view('leave.work-form', compact('item'));
    }
}
