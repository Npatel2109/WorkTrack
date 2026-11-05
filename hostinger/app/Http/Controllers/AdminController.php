<?php

namespace App\Http\Controllers;

use App\Services\LeavePolicy;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Str;
use Illuminate\Validation\Rule;
use Illuminate\Validation\ValidationException;

class AdminController extends Controller
{
    private function fail(string $message): never
    {
        throw ValidationException::withMessages(['admin' => $message]);
    }

    public function dashboard()
    {
        return view('admin.dashboard', ['live' => $this->liveRows(),
            'mapSites' => DB::table('assigned_sites')->where('is_active', true)->orderBy('site_name')->orderBy('id')
                ->get(['id', 'site_name', 'customer_name', 'address', 'latitude', 'longitude', 'allowed_radius']),
            'counts' => [
                'Employees' => DB::table('user')->where('role', 'employee')->count(),
                'Active sites' => DB::table('assigned_sites')->where('is_active', true)->count(),
                'Onsite' => DB::table('attendance')->where('status', 'onsite')->count(),
                'Pending leave' => DB::table('leave_request')->where('status', 'pending')->count(),
                'Pending work confirmations' => DB::table('daily_work_status')->where('status', 'pending')->count(),
            ]]);
    }

    private function ist(?string $value, string $format = 'd M Y H:i \I\S\T'): ?string
    {
        return $value ? CarbonImmutable::parse($value, 'UTC')->setTimezone('Asia/Kolkata')->format($format) : null;
    }

    private function leaveCounts(): array
    {
        $today = WorkTrack::today()->toDateString();

        return [
            'pending_leave_count' => DB::table('leave_request')->where('status', 'pending')->count(),
            'on_leave_count' => DB::table('leave_request as l')->join('user as u', 'u.id', '=', 'l.employee_id')
                ->where('l.status', 'approved')->where('u.is_active', true)
                ->where('l.start_date', '<=', $today)->where('l.end_date', '>=', $today)->distinct()->count('l.employee_id'),
        ];
    }

    private function liveRows()
    {
        $today = WorkTrack::today()->toDateString();
        $onLeave = DB::table('leave_request as l')->join('user as u', 'u.id', '=', 'l.employee_id')
            ->where('l.status', 'approved')->where('u.is_active', true)
            ->where('l.start_date', '<=', $today)->where('l.end_date', '>=', $today)->pluck('l.employee_id')->all();
        $work = DB::table('daily_work_status as w')->leftJoin('user as reviewer', 'reviewer.id', '=', 'w.reviewed_by_id')
            ->where('w.work_date', $today)->select('w.*', 'reviewer.name as reviewer_name')->get()->groupBy('employee_id');
        $leaves = DB::table('leave_request as l')->leftJoin('user as reviewer', 'reviewer.id', '=', 'l.reviewed_by_id')
            ->where('l.start_date', '<=', $today)->where('l.end_date', '>=', $today)
            ->select('l.*', 'reviewer.name as reviewer_name')->orderByDesc('l.created_at')->orderByDesc('l.id')->get()->groupBy('employee_id');

        return DB::table('user')->where('role', 'employee')->orderBy('name')->orderBy('id')->get()->map(function ($employee) use ($today, $onLeave, $work, $leaves) {
            $visits = DB::table('attendance as a')->join('assigned_sites as s', 's.id', '=', 'a.site_id')
                ->where('a.employee_id', $employee->id)->select('a.*', 's.site_name', 's.customer_name');
            $active = (clone $visits)->where('a.status', 'onsite')->orderByDesc('a.checkin_time')->first();
            $pending = (clone $visits)->where('a.status', 'report_pending')->orderByDesc('a.checkout_time')->first();
            $completed = (clone $visits)->where('a.status', 'completed')->orderByDesc('a.checkout_time')->first();
            $completedToday = $completed?->checkout_time && $this->ist($completed->checkout_time, 'Y-m-d') === $today;
            $assignments = DB::table('assigned_sites as s')->join('site_assignment as a', 'a.site_id', '=', 's.id')
                ->where('a.employee_id', $employee->id)->where('a.is_active', true)->where('s.is_active', true)
                ->select('s.*')->orderBy('s.site_name')->orderBy('s.id')->get();
            $lastLogin = DB::table('audit_log')->where('user_id', $employee->id)->whereIn('action', ['login', 'logout'])->orderByDesc('created_at')->orderByDesc('id')->first();
            $attendance = $active ?? $pending;
            $sites = $attendance ? collect([(object) ['site_name' => $attendance->site_name, 'customer_name' => $attendance->customer_name]]) : $assignments;
            $minutes = $attendance ? max(0, (int) round(CarbonImmutable::parse($attendance->checkin_time, 'UTC')->diffInMinutes(
                $attendance->checkout_time ? CarbonImmutable::parse($attendance->checkout_time, 'UTC') : WorkTrack::now()))) : null;
            $daily = [];
            if ($active) {
                $daily[] = ['label' => 'On Site', 'status' => 'onsite', 'detail' => $active->site_name];
            }
            if ($pending?->checkout_time && $this->ist($pending->checkout_time, 'Y-m-d') === $today) {
                $daily[] = ['label' => 'Visit stopped - report pending', 'status' => 'report-pending', 'detail' => $pending->site_name];
            }
            if ($completedToday) {
                $daily[] = ['label' => 'Site visit completed', 'status' => 'completed', 'detail' => $completed->site_name];
            }
            foreach ($work->get($employee->id, collect()) as $item) {
                $label = ['wfh' => 'WFH', 'travelling' => 'Travelling', 'training' => 'Training'][$item->work_type];
                $detail = $item->reason;
                if ($item->reviewer_name && $item->reviewed_at) {
                    $detail .= ' · Reviewed by '.$item->reviewer_name.' at '.$this->ist($item->reviewed_at, 'H:i \I\S\T');
                }
                $daily[] = ['label' => $label, 'status' => $item->status, 'detail' => $detail];
            }
            foreach ($leaves->get($employee->id, collect()) as $item) {
                $label = ['full' => 'Full day', 'morning' => 'Half day - morning', 'afternoon' => 'Half day - afternoon'][$item->period];
                $detail = $item->reason;
                if ($item->reviewer_name && $item->reviewed_at) {
                    $detail .= ' · Reviewed by '.$item->reviewer_name.' at '.$this->ist($item->reviewed_at, 'H:i \I\S\T');
                }
                $daily[] = ['label' => 'Leave - '.$label, 'status' => $item->status, 'detail' => $detail];
            }
            if (! $daily) {
                $daily[] = ['label' => 'No status submitted', 'status' => 'offline', 'detail' => ''];
            }
            $status = $active ? 'onsite' : (in_array($employee->id, $onLeave) ? 'on-leave'
                : ($pending ? 'report-pending' : ($completedToday ? 'completed'
                    : ($assignments->isNotEmpty() && $lastLogin?->action === 'login' ? 'assigned' : 'offline'))));

            return ['employee' => $employee, 'attendance' => $attendance, 'sites' => $sites,
                'onsite_hours' => $minutes === null ? '—' : intdiv($minutes, 60).'h '.sprintf('%02d', $minutes % 60).'m',
                'status' => $status, 'daily_statuses' => $daily];
        });
    }

    public function liveStatus()
    {
        $rows = $this->liveRows();

        return response()->json($this->leaveCounts() + [
            'onsite_count' => $rows->where('status', 'onsite')->count(),
            'pending_report_count' => DB::table('attendance')->where('status', 'report_pending')->count(),
            'pending_work_count' => DB::table('daily_work_status')->where('status', 'pending')->count(),
            'employees' => $rows->map(fn ($row) => [
                'id' => $row['employee']->id, 'employee_code' => $row['employee']->employee_code,
                'is_active' => (bool) $row['employee']->is_active,
                'name' => $row['employee']->name, 'designation' => $row['employee']->designation,
                'site' => $row['sites']->pluck('site_name')->implode(', ') ?: null,
                'customer' => $row['sites']->pluck('customer_name')->unique()->implode(', ') ?: null,
                'checkin_time' => $this->ist($row['attendance']?->checkin_time, 'H:i \I\S\T'),
                'onsite_hours' => $row['onsite_hours'], 'status' => $row['status'], 'daily_statuses' => $row['daily_statuses'],
            ])->values(),
        ])->header('Cache-Control', 'private, no-store');
    }

    public function metric(string $metric)
    {
        abort_unless(in_array($metric, ['employees', 'sites', 'onsite', 'pending-reports', 'completed', 'reports'], true), 404);
        $titles = ['employees' => 'Total employees', 'sites' => 'Assigned sites', 'onsite' => 'Currently onsite',
            'pending-reports' => 'Stopped visits - report pending', 'completed' => 'Completed today', 'reports' => 'Reports submitted'];
        if ($metric === 'employees') {
            $rows = DB::table('user')->where('role', 'employee')->orderBy('name')->get()->map(fn ($user) => [
                'employee' => $user->name, 'details' => $user->email.' - '.$user->designation,
                'status' => $user->is_active ? 'Active' : 'Disabled', 'pdf_url' => null]);
        } elseif ($metric === 'sites') {
            $members = DB::table('site_assignment as a')->join('user as u', 'u.id', '=', 'a.employee_id')
                ->where('a.is_active', true)->select('a.site_id', 'u.name')->orderBy('u.name')->get()->groupBy('site_id');
            $rows = DB::table('assigned_sites')->where('is_active', true)->orderBy('site_name')->get()->map(fn ($site) => [
                'employee' => $members->get($site->id, collect())->pluck('name')->implode(', ') ?: 'Unassigned',
                'details' => $site->site_name.' - '.$site->customer_name.' - '.$site->address, 'status' => 'Assigned', 'pdf_url' => null]);
        } else {
            $query = DB::table('attendance as a')->join('user as u', 'u.id', '=', 'a.employee_id')
                ->join('assigned_sites as s', 's.id', '=', 'a.site_id')->leftJoin('site_report as r', 'r.attendance_id', '=', 'a.id')
                ->select('a.*', 'u.name as employee_name', 's.site_name', 'r.id as report_id', 'r.report_date');
            if ($metric === 'reports') {
                $query->whereNotNull('r.id')->orderByDesc('r.report_date');
            } else {
                $query->where('a.status', ['onsite' => 'onsite', 'pending-reports' => 'report_pending', 'completed' => 'completed'][$metric]);
                if ($metric === 'completed') {
                    $query->where('a.checkout_time', '>=', WorkTrack::today()->utc()->toDateTimeString())
                        ->where('a.checkout_time', '<', WorkTrack::today()->addDay()->utc()->toDateTimeString());
                }
                $query->orderByDesc($metric === 'onsite' ? 'a.checkin_time' : 'a.checkout_time');
            }
            $rows = $query->get()->map(function ($visit) use ($metric) {
                $time = match ($metric) {
                    'onsite' => $visit->checkin_time, 'reports' => $visit->report_date, default => $visit->checkout_time,
                };

                return ['employee' => $visit->employee_name,
                    'details' => $visit->site_name.' - '.($metric === 'pending-reports' ? 'Stopped ' : ($metric === 'reports' ? 'Submitted ' : '')).$this->ist($time),
                    'status' => match ($metric) {
                        'onsite' => 'Onsite', 'completed' => 'Completed', 'reports' => 'Submitted',
                        default => $visit->auto_closed ? 'Auto-stopped; report pending' : 'Report pending',
                    },
                    'pdf_url' => in_array($metric, ['completed', 'reports'], true) && $visit->report_id ? url('/reports/'.$visit->id.'/pdf') : null];
            });
        }

        return view('admin.metric', ['title' => $titles[$metric], 'rows' => $rows]);
    }

    public function employeeDashboard()
    {
        $employee = auth()->user();
        $sites = DB::table('assigned_sites as s')->join('site_assignment as a', 'a.site_id', '=', 's.id')
            ->where('a.employee_id', $employee->id)->where('a.is_active', true)->where('s.is_active', true)->select('s.*')->orderBy('s.site_name')->get();
        $visits = DB::table('attendance as a')->join('assigned_sites as s', 's.id', '=', 'a.site_id')
            ->where('a.employee_id', $employee->id)->whereIn('a.status', ['onsite', 'report_pending'])->select('a.*', 's.site_name')->orderByDesc('a.id')->get();

        return view('admin.employee-dashboard', ['sites' => $sites, 'visits' => $visits,
            'fullDayLeave' => LeavePolicy::fullDay($employee->id),
            'summary' => LeavePolicy::summary($employee, WorkTrack::today()->startOfMonth(), WorkTrack::today()->endOfMonth()),
            'balance' => LeavePolicy::balance($employee),
            'confirmation' => DB::table('daily_work_status')->where('employee_id', $employee->id)->where('work_date', WorkTrack::today()->toDateString())->first()]);
    }

    public function employees(Request $request, ?int $id = null)
    {
        if ($request->isMethod('post')) {
            return $this->saveEmployee($request);
        }

        return view('admin.employees', ['employees' => DB::table('user')->where('role', 'employee')->orderBy('name')->get(),
            'editing' => $id ? DB::table('user')->where('role', 'employee')->where('id', $id)->firstOrFail() : null]);
    }

    public function saveEmployee(Request $request, ?int $id = null)
    {
        $existing = $id ? DB::table('user')->where('role', 'employee')->where('id', $id)->firstOrFail() : null;
        $request->merge(['email' => strtolower(trim((string) $request->input('email'))),
            'employee_code' => strtoupper(trim((string) $request->input('employee_code', $existing?->employee_code ?? ''))) ?: null,
            'blood_group' => strtoupper(trim((string) $request->input('blood_group', $existing?->blood_group ?? '')))]);
        $data = $request->validate([
            'name' => ['required', 'string', 'max:120'], 'email' => ['required', 'email', 'max:255', Rule::unique('user', 'email')->ignore($id)],
            'mobile' => ['required', 'string', 'max:40'], 'designation' => ['required', 'string', 'max:120'],
            'address' => ['nullable', 'string', 'max:1000'], 'emergency_contact_no' => ['nullable', 'string', 'max:40'],
            'blood_group' => ['nullable', Rule::in(['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'])],
            'laptop_serial_no' => ['nullable', 'string', 'max:120'],
            'employee_code' => ['nullable', 'string', 'max:80', Rule::unique('user', 'employee_code')->ignore($id)],
            'birthday' => ['nullable', 'date_format:Y-m-d', 'before_or_equal:'.WorkTrack::today()->toDateString()],
            'password' => [$id ? 'nullable' : 'required', 'string', 'min:12', 'max:255'],
        ]);
        if ($data['employee_code'] && DB::table('user')->whereRaw('UPPER(employee_code) = ?', [$data['employee_code']])
            ->when($id, fn ($q) => $q->where('id', '!=', $id))->exists()) {
            $this->fail('Employee ID is already in use.');
        }
        $password = $data['password'] ?? null;
        unset($data['password']);
        foreach (['address', 'emergency_contact_no', 'blood_group', 'laptop_serial_no'] as $field) {
            $data[$field] = $data[$field] ?? ($existing->$field ?? '');
        }
        DB::transaction(function () use ($id, $data, $password, $existing) {
            if ($id) {
                $changes = $data;
                if ($password) {
                    $changes += ['password_hash' => Hash::make($password), 'auth_version' => DB::raw('auth_version + 1')];
                }
                DB::table('user')->where('id', $id)->update($changes);
            } else {
                DB::table('user')->insert($data + ['password_hash' => Hash::make($password), 'role' => 'employee',
                    'created_date' => WorkTrack::now(), 'is_active' => true, 'auth_version' => 0]);
            }
            WorkTrack::audit($existing ? 'employee_updated' : 'employee_created', $data['email']);
        });

        return redirect('/admin/employees')->with('success', 'Employee saved.');
    }

    public function resetPassword(Request $request, int $id)
    {
        $request->validate(['password' => ['required', 'string', 'min:12', 'max:255']]);
        DB::transaction(function () use ($request, $id) {
            DB::table('user')->where('id', $id)->where('role', 'employee')->lockForUpdate()->firstOrFail();
            DB::table('user')->where('id', $id)->update(['password_hash' => Hash::make($request->password), 'auth_version' => DB::raw('auth_version + 1')]);
            WorkTrack::audit('employee_password_reset', "Employee $id");
        });

        return back()->with('success', 'Password reset; existing sessions invalidated.');
    }

    public function deleteEmployee(int $id)
    {
        $deactivated = DB::transaction(function () use ($id) {
            $employee = DB::table('user')->where('id', $id)->where('role', 'employee')->lockForUpdate()->firstOrFail();
            $history = false;
            foreach (['attendance', 'invoice', 'leave_request', 'daily_work_status', 'monthly_leave_entitlement',
                'leave_allowance_account', 'submitted_report', 'candidate_referral'] as $table) {
                $history = $history || DB::table($table)->where('employee_id', $id)->exists();
            }
            $history = $history || DB::table('audit_log')->where('user_id', $id)->exists()
                || DB::table('leave_request')->where('reviewed_by_id', $id)->exists()
                || DB::table('daily_work_status')->where('reviewed_by_id', $id)->exists();
            if ($history) {
                DB::table('user')->where('id', $id)->update(['is_active' => false, 'auth_version' => DB::raw('auth_version + 1')]);
                DB::table('site_assignment')->where('employee_id', $id)->update(['is_active' => false]);
            } else {
                DB::table('site_assignment')->where('employee_id', $id)->delete();
                DB::table('user')->where('id', $id)->delete();
            }
            WorkTrack::audit($history ? 'employee_deactivated' : 'employee_deleted', $employee->email);

            return $history;
        });

        return redirect('/admin/employees')->with('success', $deactivated ? 'Employee deactivated; history preserved.' : 'Employee deleted.');
    }

    private function signoff(Request $request): array
    {
        $data = $request->validate(['commissioning_manager' => ['nullable', 'string', 'max:120'],
            'project_manager' => ['nullable', 'string', 'max:120'], 'report_remarks' => ['nullable', 'string', 'max:2000']]);
        if (substr_count(str_replace("\r\n", "\n", $data['report_remarks'] ?? ''), "\n") > 4) {
            $this->fail('Report remarks must fit within five lines.');
        }

        return array_map(fn ($value) => $value ?? '', $data) + ['commissioning_manager' => '', 'project_manager' => '', 'report_remarks' => ''];
    }

    public function sites(Request $request, ?int $id = null)
    {
        $editing = $id ? DB::table('assigned_sites')->where('id', $id)->firstOrFail() : null;
        if ($request->isMethod('post')) {
            $data = $request->validate(['site_name' => ['required', 'string', 'max:160'], 'customer_name' => ['required', 'string', 'max:160'],
                'address' => ['required', 'string', 'max:10000'], 'latitude' => ['required', 'numeric', 'between:-90,90'],
                'longitude' => ['required', 'numeric', 'between:-180,180'], 'allowed_radius' => ['required', 'numeric', 'between:10,5000'],
                'project_id' => ['nullable', 'integer', 'exists:project,id'], 'is_active' => ['sometimes', 'boolean']]);
            $data += $this->signoff($request);
            DB::transaction(function () use ($id, $data) {
                if ($id) {
                    DB::table('assigned_sites')->where('id', $id)->lockForUpdate()->firstOrFail();
                    if (DB::table('attendance')->where('site_id', $id)->where('status', 'onsite')->exists()) {
                        $this->fail('Sites cannot be edited during an active visit.');
                    }
                }
                $projectName = isset($data['project_id']) ? DB::table('project')->where('id', $data['project_id'])->value('name') : '';
                $data['project_id'] = $data['project_id'] ?? null;
                $data['project_name'] = $projectName;
                $data['report_company'] = 'Blackpurple automation';
                if ($id) {
                    DB::table('assigned_sites')->where('id', $id)->update($data);
                } else {
                    DB::table('assigned_sites')->insert($data + ['assigned_date' => WorkTrack::now(), 'is_active' => true]);
                }
                WorkTrack::audit($id ? 'site_updated' : 'site_created', $data['site_name']);
            });

            return redirect('/admin/sites')->with('success', 'Site saved.');
        }

        return view('admin.sites', ['editing' => $editing, 'sites' => DB::table('assigned_sites')->orderBy('site_name')->get(),
            'projects' => DB::table('project')->orderBy('name')->get()]);
    }

    public function reportDetails(Request $request, int $id)
    {
        $data = $this->signoff($request);
        DB::transaction(function () use ($id, $data) {
            DB::table('assigned_sites')->where('id', $id)->lockForUpdate()->firstOrFail();
            if (DB::table('attendance')->where('site_id', $id)->where('status', 'onsite')->exists()) {
                $this->fail('Report details cannot change during an active visit.');
            }
            DB::table('assigned_sites')->where('id', $id)->update($data + ['report_company' => 'Blackpurple automation']);
            WorkTrack::audit('site_report_details_updated', "Site $id");
        });

        return back()->with('success', 'Report details saved.');
    }

    public function deleteSite(int $id)
    {
        DB::transaction(function () use ($id) {
            $site = DB::table('assigned_sites')->where('id', $id)->lockForUpdate()->firstOrFail();
            foreach (['attendance', 'invoice', 'submitted_report'] as $table) {
                if (DB::table($table)->where('site_id', $id)->exists()) {
                    $this->fail('This site has history and cannot be deleted. Deactivate it using Edit instead.');
                }
            }
            DB::table('site_assignment')->where('site_id', $id)->delete();
            DB::table('assigned_sites')->where('id', $id)->delete();
            WorkTrack::audit('site_deleted', $site->site_name);
        });

        return redirect('/admin/sites')->with('success', 'Site deleted.');
    }

    public function projects(Request $request, ?int $id = null)
    {
        if ($id) {
            DB::table('project')->where('id', $id)->firstOrFail();
        }
        if ($request->isMethod('post')) {
            $request->merge(['project_number' => strtoupper(trim((string) $request->input('project_number')))]);
            $data = $request->validate(['project_number' => ['required', 'string', 'max:80', Rule::unique('project')->ignore($id)],
                'name' => ['required', 'string', 'max:250', Rule::unique('project')->ignore($id)]]);
            if (DB::table('project')->where(fn ($q) => $q->whereRaw('UPPER(project_number) = ?', [$data['project_number']])
                ->orWhereRaw('LOWER(name) = ?', [strtolower($data['name'])]))->when($id, fn ($q) => $q->where('id', '!=', $id))->exists()) {
                $this->fail('A project with that number or name already exists.');
            }
            DB::transaction(function () use ($id, $data) {
                if ($id) {
                    DB::table('project')->where('id', $id)->lockForUpdate()->firstOrFail();
                    DB::table('project')->where('id', $id)->update($data);
                    DB::table('assigned_sites')->where('project_id', $id)->update(['project_name' => $data['name']]);
                } else {
                    DB::table('project')->insert($data);
                }
                WorkTrack::audit($id ? 'project_updated' : 'project_created', json_encode($data));
            });

            return redirect('/admin/projects')->with('success', 'Project saved.');
        }

        return view('admin.projects', ['projects' => DB::table('project')->orderBy('name')->get()]);
    }

    public function deleteProject(int $id)
    {
        DB::transaction(function () use ($id) {
            $project = DB::table('project')->where('id', $id)->lockForUpdate()->firstOrFail();
            if (DB::table('assigned_sites')->where('project_id', $id)->exists()) {
                $this->fail('Projects linked to sites cannot be deleted.');
            }
            DB::table('project')->where('id', $id)->delete();
            WorkTrack::audit('project_deleted', $project->name);
        });

        return back()->with('success', 'Project deleted.');
    }

    public function assignments(Request $request)
    {
        $request->validate(['employee_id' => [$request->isMethod('post') ? 'required' : 'nullable', 'integer'],
            'site_ids' => ['nullable', 'array'], 'site_ids.*' => ['integer', 'distinct', Rule::exists('assigned_sites', 'id')->where('is_active', true)]]);
        $employee = $request->filled('employee_id') ? DB::table('user')->where('id', $request->integer('employee_id'))
            ->where('role', 'employee')->where('is_active', true)->firstOrFail() : null;
        if ($request->isMethod('post')) {
            $selected = array_map('intval', $request->input('site_ids', []));
            DB::transaction(function () use ($employee, $selected) {
                DB::table('user')->where('id', $employee->id)->lockForUpdate()->firstOrFail();
                $existing = DB::table('site_assignment')->where('employee_id', $employee->id)->get();
                $removing = $existing->where('is_active', true)->pluck('site_id')->diff($selected);
                if (DB::table('attendance')->where('employee_id', $employee->id)->where('status', 'onsite')->whereIn('site_id', $removing)->exists()) {
                    $this->fail('Stop active visits before removing their assignments.');
                }
                DB::table('site_assignment')->where('employee_id', $employee->id)->whereNotIn('site_id', $selected)->update(['is_active' => false]);
                foreach ($selected as $site) {
                    $catalog = DB::table('assigned_sites')->where('id', $site)->lockForUpdate()->firstOrFail();
                    if (! $catalog->is_active) {
                        $this->fail('Only active sites may be assigned.');
                    }
                    if ($existing->contains('site_id', $site)) {
                        DB::table('site_assignment')->where('employee_id', $employee->id)->where('site_id', $site)->update(['is_active' => true]);
                    } else {
                        DB::table('site_assignment')->insert(['employee_id' => $employee->id, 'site_id' => $site,
                            'is_active' => true, 'assigned_at' => WorkTrack::now()]);
                    }
                }
                WorkTrack::audit('site_assignments_updated', "Employee {$employee->id}: ".implode(',', $selected));
            });

            return redirect('/admin/assignments?employee_id='.$employee->id)->with('success', 'Assignments saved; membership history retained.');
        }

        return view('admin.assignments', ['employee' => $employee,
            'employees' => DB::table('user')->where('role', 'employee')->where('is_active', true)->orderBy('name')->get(),
            'sites' => DB::table('assigned_sites')->where('is_active', true)->orderBy('site_name')->get(),
            'selected' => $employee ? DB::table('site_assignment')->where('employee_id', $employee->id)->where('is_active', true)->pluck('site_id')->all() : []]);
    }

    public function settings(Request $request)
    {
        $settings = DB::table('app_settings')->where('id', 1)->first();
        if ($request->isMethod('post')) {
            $request->validate(['section' => ['required', Rule::in(['mail', 'brand', 'android'])]]);
            $data = [];
            $newFile = null;
            $oldFile = null;
            if ($request->section === 'mail') {
                $data = $request->validate(['mail_server' => ['nullable', 'string', 'max:255', 'regex:/^[a-zA-Z0-9._-]+$/'],
                    'mail_port' => ['required', 'integer', 'between:1,65535'], 'mail_security' => ['required', Rule::in(['ssl', 'starttls', 'none'])],
                    'mail_username' => ['nullable', 'string', 'max:255'], 'mail_password' => ['nullable', 'string', 'max:500'],
                    'mail_sender' => ['nullable', 'required_with:mail_server', 'email', 'max:255'],
                    'referral_notification_email' => ['nullable', 'email', 'max:255'], 'po_notification_email' => ['nullable', 'email', 'max:255'],
                    'public_base_url' => ['nullable', 'url:http,https', 'max:500']]);
                if (! $request->filled('mail_password') && ! $request->boolean('clear_mail_password')) {
                    unset($data['mail_password']);
                }
                foreach ($data as $key => $value) {
                    $data[$key] = $value ?? '';
                }
                if ($request->boolean('clear_mail_password')) {
                    $data['mail_password'] = '';
                }
            } elseif ($request->section === 'brand') {
                $request->validate(['action' => ['required', Rule::in(['upload', 'remove'])],
                    'brand_logo' => [$request->action === 'upload' ? 'required' : 'nullable', 'image', 'mimes:png,jpg,jpeg,webp', 'max:2048', 'dimensions:max_width=4096,max_height=4096']]);
                $oldFile = $settings?->brand_logo_filename;
                $data['brand_logo_filename'] = null;
                if ($request->action === 'upload') {
                    $extension = $request->file('brand_logo')->extension();
                    $extension = $extension === 'jpeg' ? 'jpg' : $extension;
                    $newFile = str_replace('-', '', (string) Str::uuid()).'.'.$extension;
                    $request->file('brand_logo')->move(WorkTrack::uploadPath(), $newFile);
                    $data['brand_logo_filename'] = $newFile;
                }
            } else {
                $data = ['android_download_enabled' => $request->boolean('android_download_enabled')];
            }
            $saved = false;
            try {
                DB::transaction(function () use ($data, $request) {
                    DB::table('app_settings')->updateOrInsert(['id' => 1], $data);
                    WorkTrack::audit($request->section.'_settings_updated');
                });
                $saved = true;
            } finally {
                if (! $saved && $newFile) {
                    $this->removeLogo($newFile);
                }
            }
            if ($oldFile && preg_match('/^[a-f0-9]{32}\.(png|jpg|webp)$/', $oldFile)) {
                $this->removeLogo($oldFile);
            }
            if ($request->section === 'mail' && $request->input('action') === 'test') {
                try {
                    $this->sendSettingsTest(DB::table('app_settings')->where('id', 1)->first());
                } catch (\Throwable $error) {
                    report($error);

                    return back()->with('error', 'Settings saved, but SMTP test failed. Check mail settings and the application log.');
                }

                return back()->with('success', 'Settings saved; test email sent.');
            }

            return back()->with('success', 'Settings saved.');
        }

        return view('admin.settings', compact('settings'));
    }

    private function sendSettingsTest(object $settings): void
    {
        if (! $settings->mail_server || ! $settings->mail_sender) {
            $this->fail('Configure an SMTP server and sender before testing.');
        }

        $mailer = Mail::build(['transport' => 'smtp', 'host' => $settings->mail_server, 'port' => $settings->mail_port,
            'scheme' => $settings->mail_security === 'ssl' ? 'smtps' : 'smtp',
            'auto_tls' => $settings->mail_security !== 'none', 'require_tls' => $settings->mail_security === 'starttls',
            'username' => $settings->mail_username, 'password' => $settings->mail_password, 'timeout' => 20]);
        $mailer->raw('Your WorkTrack SMTP settings are working.', fn ($message) => $message->from($settings->mail_sender)->to(auth()->user()->email)->subject('WorkTrack test email'));
    }

    private function removeLogo(string $filename): void
    {
        $path = WorkTrack::uploadPath($filename);
        try {
            if (is_file($path) && ! unlink($path)) {
                throw new \RuntimeException('Could not delete the private logo '.$filename);
            }
        } catch (\Throwable $error) {
            report($error);
        }
    }

    public function attendance(Request $request)
    {
        $request->validate(['from_date' => ['nullable', 'date_format:Y-m-d'], 'to_date' => ['nullable', 'date_format:Y-m-d', 'after_or_equal:from_date'],
            'employee_id' => ['nullable', 'integer', 'exists:user,id'], 'site_id' => ['nullable', 'integer', 'exists:assigned_sites,id'], 'employee_search' => ['nullable', 'string', 'max:255'],
            'status' => ['nullable', Rule::in(['all', 'onsite', 'report_pending', 'completed'])]]);
        $query = DB::table('attendance as a')->join('user as u', 'u.id', '=', 'a.employee_id')->join('assigned_sites as s', 's.id', '=', 'a.site_id')
            ->select('a.*', 'u.name as employee_name', 'u.employee_code', 's.site_name', 's.customer_name');
        $from = $request->input('from_date', WorkTrack::today()->toDateString());
        $to = $request->input('to_date', WorkTrack::today()->toDateString());
        if ($from) {
            $query->where('a.checkin_time', '>=', LeavePolicy::date($from)->utc()->toDateTimeString());
        }
        if ($to) {
            $query->where('a.checkin_time', '<', LeavePolicy::date($to)->addDay()->utc()->toDateTimeString());
        }
        foreach (['employee_id', 'site_id'] as $field) {
            if ($request->filled($field)) {
                $query->where("a.$field", $request->integer($field));
            }
        }
        if ($request->filled('status') && $request->status !== 'all') {
            $query->where('a.status', $request->status);
        }
        if ($request->filled('employee_search')) {
            $query->where(fn ($q) => $q->where('u.name', 'like', '%'.$request->employee_search.'%')
                ->orWhere('u.employee_code', 'like', '%'.$request->employee_search.'%')->orWhere('u.email', 'like', '%'.$request->employee_search.'%'));
        }

        return view('admin.attendance', ['records' => $query->orderByDesc('a.checkin_time')->paginate(50)->withQueryString(),
            'employees' => DB::table('user')->where('role', 'employee')->orderBy('name')->get(),
            'sites' => DB::table('assigned_sites')->orderBy('site_name')->get(),
            'audits' => DB::table('audit_log as a')->leftJoin('user as u', 'u.id', '=', 'a.user_id')
                ->select('a.*', 'u.name as user_name')->orderByDesc('a.created_at')->limit(100)->get()]);
    }
}
