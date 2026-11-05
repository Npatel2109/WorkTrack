<?php

namespace Tests\Feature;

use App\Models\User;
use App\Services\LeavePolicy;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Tests\TestCase;

class LeaveWorkflowsTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-05 10:00:00', 'UTC'));
    }

    protected function tearDown(): void
    {
        CarbonImmutable::setTestNow();
        parent::tearDown();
    }

    private function user(string $role = 'employee', string $created = '2026-11-01 00:00:00'): User
    {
        return User::create(['name' => ucfirst($role), 'email' => uniqid().'@example.test', 'mobile' => '123456789',
            'designation' => 'Engineer', 'password_hash' => Hash::make('A secure password 123'), 'role' => $role,
            'is_active' => true, 'auth_version' => 0, 'created_date' => $created]);
    }

    private function leave(int $employee, string $date, string $period = 'full', string $status = 'approved'): int
    {
        return DB::table('leave_request')->insertGetId(['employee_id' => $employee, 'start_date' => $date, 'end_date' => $date,
            'period' => $period, 'reason' => 'Personal', 'status' => $status, 'created_at' => WorkTrack::now()]);
    }

    public function test_employee_can_apply_complementary_halves_but_not_overlapping_leave(): void
    {
        $employee = $this->user();
        $data = ['start_date' => '2026-11-06', 'end_date' => '2026-11-06', 'reason' => 'Personal'];
        $this->actingAs($employee)->post('/employee/leaves', $data + ['period' => 'morning'])->assertSessionHasNoErrors();
        $this->post('/employee/leaves', $data + ['period' => 'afternoon'])->assertSessionHasNoErrors();
        $this->post('/employee/leaves', $data + ['period' => 'full'])->assertSessionHasErrors('leave');
        $this->assertDatabaseCount('leave_request', 2);
        $this->post('/employee/leaves', ['start_date' => '2026-11-04', 'end_date' => '2026-11-04', 'period' => 'full', 'reason' => 'Past'])
            ->assertSessionHasErrors('start_date');
        $this->post('/employee/leaves', ['start_date' => '2026-11-07', 'end_date' => '2026-11-08', 'period' => 'morning', 'reason' => 'Invalid'])
            ->assertSessionHasErrors('leave');
    }

    public function test_employee_cannot_read_other_employee_leave_or_review_requests(): void
    {
        $employee = $this->user();
        $other = $this->user();
        $id = $this->leave($other->id, '2026-11-05');
        DB::table('leave_request')->where('id', $id)->update(['reason' => 'Other private reason']);
        $this->actingAs($employee)->get('/employee/leaves?employee_id='.$other->id)->assertOk()->assertDontSee('Other private reason');
        $this->post('/admin/leaves/'.$id.'/review', ['decision' => 'approved'])->assertForbidden();
    }

    public function test_admin_edits_reset_approval_and_employee_owner_is_immutable(): void
    {
        $employee = $this->user();
        $other = $this->user();
        $id = $this->leave($employee->id, '2026-11-05');
        $data = ['employee_id' => $employee->id, 'start_date' => '2026-11-06', 'end_date' => '2026-11-06', 'period' => 'morning', 'reason' => 'Corrected'];
        $this->actingAs($this->user('admin'))->post('/admin/leaves/'.$id.'/edit', $data)->assertSessionHasNoErrors();
        $this->assertDatabaseHas('leave_request', ['id' => $id, 'status' => 'pending', 'reviewed_at' => null]);
        $data['employee_id'] = $other->id;
        $this->post('/admin/leaves/'.$id.'/edit', $data)->assertSessionHasErrors('leave');
        $this->post('/admin/leaves/'.$id.'/review', ['decision' => 'approved'])->assertSessionHasNoErrors();
        $this->post('/admin/leaves/'.$id.'/review', ['decision' => 'rejected'])->assertSessionHasErrors('leave');
        $this->assertDatabaseHas('leave_request', ['id' => $id, 'status' => 'approved']);
    }

    public function test_work_status_deadline_duplicate_review_and_correction(): void
    {
        $employee = $this->user();
        $this->actingAs($employee)->post('/employee/work-status', ['work_type' => 'wfh', 'reason' => 'Remote work'])->assertSessionHasNoErrors();
        $this->post('/employee/work-status', ['work_type' => 'training', 'reason' => 'Duplicate'])->assertSessionHasErrors('leave');
        $item = DB::table('daily_work_status')->first();
        $this->actingAs($this->user('admin'))->post('/admin/work-status/'.$item->id.'/review', ['decision' => 'approved'])->assertSessionHasNoErrors();
        $this->post('/admin/work-status/'.$item->id.'/edit', ['work_type' => 'travelling', 'reason' => 'Corrected'])->assertSessionHasNoErrors();
        $this->assertDatabaseHas('daily_work_status', ['id' => $item->id, 'status' => 'pending', 'reviewed_at' => null]);
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-06 18:29:00', 'UTC'));
        $this->actingAs($employee)->post('/employee/work-status', ['work_type' => 'wfh', 'reason' => 'Late'])->assertSessionHasErrors('leave');
    }

    public function test_automatic_policy_respects_cutoff_sundays_half_days_and_approved_work(): void
    {
        $employee = $this->user();
        DB::table('attendance_policy')->insert(['id' => 1, 'effective_from' => '2026-11-01']);
        $this->leave($employee->id, '2026-11-02', 'morning');
        DB::table('daily_work_status')->insert(['employee_id' => $employee->id, 'work_date' => '2026-11-03', 'work_type' => 'wfh',
            'reason' => 'Remote', 'status' => 'approved', 'created_at' => WorkTrack::now()]);
        $summary = LeavePolicy::summary($employee, LeavePolicy::date('2026-11-01'), LeavePolicy::date('2026-11-05'));
        $this->assertEquals(1.5, $summary['totals']['automatic']);
        $this->assertEquals(.5, $summary['totals']['approved']);
        $this->assertSame(['2026-11-02', '2026-11-04'], array_column($summary['automatic'], 'date'));
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-05 18:29:00', 'UTC'));
        $summary = LeavePolicy::summary($employee, LeavePolicy::date('2026-11-05'), LeavePolicy::date('2026-11-05'));
        $this->assertEquals(1, $summary['totals']['automatic']);
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-05 18:28:59', 'UTC'));
        $this->assertEquals(0, LeavePolicy::summary($employee, LeavePolicy::date('2026-11-05'), LeavePolicy::date('2026-11-05'))['totals']['automatic']);
    }

    public function test_balances_credit_partial_joining_month_carry_forward_rates_and_future_reservations(): void
    {
        $employee = $this->user('employee', '2026-09-15 00:00:00');
        $admin = $this->user('admin');
        $this->actingAs($admin)->post('/admin/leaves/account', ['employee_id' => $employee->id, 'joining_date' => '2026-09-15', 'opening_days' => 1.5])->assertSessionHasNoErrors();
        $this->post('/admin/leaves/entitlement', ['employee_id' => $employee->id, 'month' => '2026-10', 'days' => 3])->assertSessionHasNoErrors();
        $this->leave($employee->id, '2026-10-01', 'morning');
        $this->leave($employee->id, '2026-11-10');
        $balance = LeavePolicy::balance($employee);
        $this->assertEquals(9.5, $balance['entitlement']);
        $this->assertEquals(.5, $balance['used']);
        $this->assertEquals(1, $balance['reserved']);
        $this->assertEquals(8, $balance['available']);
        $this->assertCount(3, $balance['history']);
        $this->post('/admin/leaves/entitlement', ['employee_id' => $employee->id, 'month' => '2026-08', 'days' => 3])->assertSessionHasErrors('leave');
        $this->post('/admin/leaves/entitlement', ['employee_id' => $employee->id, 'month' => '2026-11', 'days' => .3])->assertSessionHasErrors('days');
        $this->post('/admin/leaves/account', ['employee_id' => $employee->id, 'joining_date' => '2026-11-01', 'opening_days' => 0])->assertSessionHasErrors('leave');
    }

    public function test_active_visits_block_full_day_approval_but_allow_a_single_half_day(): void
    {
        $employee = $this->user();
        $site = DB::table('assigned_sites')->insertGetId(['site_name' => 'Factory', 'customer_name' => 'Client',
            'address' => 'Mumbai', 'latitude' => 19, 'longitude' => 72, 'allowed_radius' => 100,
            'assigned_date' => WorkTrack::now(), 'is_active' => true]);
        DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site,
            'checkin_time' => WorkTrack::now(), 'checkin_lat' => 19, 'checkin_lon' => 72,
            'checkin_selfie' => 'test.png', 'status' => 'onsite']);
        $morning = $this->leave($employee->id, '2026-11-05', 'morning', 'pending');
        $afternoon = $this->leave($employee->id, '2026-11-05', 'afternoon', 'pending');
        $this->actingAs($this->user('admin'))->post('/admin/leaves/'.$morning.'/review', ['decision' => 'approved'])->assertSessionHasNoErrors();
        $this->post('/admin/leaves/'.$afternoon.'/review', ['decision' => 'approved'])->assertSessionHasErrors('leave');
        $this->assertFalse(LeavePolicy::fullDay($employee->id));
        DB::table('attendance')->where('employee_id', $employee->id)->update(['status' => 'report_pending', 'checkout_time' => WorkTrack::now()]);
        $this->post('/admin/leaves/'.$afternoon.'/review', ['decision' => 'approved'])->assertSessionHasNoErrors();
        $this->assertTrue(LeavePolicy::fullDay($employee->id));
        $this->actingAs($employee)->post('/employee/work-status', ['work_type' => 'wfh', 'reason' => 'Unneeded'])->assertSessionHasErrors('leave');
    }

    public function test_recorded_visit_suppresses_automatic_leave_on_its_ist_business_date(): void
    {
        $employee = $this->user();
        DB::table('attendance_policy')->insert(['id' => 1, 'effective_from' => '2026-11-01']);
        $site = DB::table('assigned_sites')->insertGetId(['site_name' => 'Factory', 'customer_name' => 'Client',
            'address' => 'Mumbai', 'latitude' => 19, 'longitude' => 72, 'allowed_radius' => 100,
            'assigned_date' => WorkTrack::now(), 'is_active' => true]);
        DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site,
            'checkin_time' => '2026-11-03 18:30:00', 'checkin_lat' => 19, 'checkin_lon' => 72,
            'checkin_selfie' => 'test.png', 'status' => 'completed']);
        $summary = LeavePolicy::summary($employee, LeavePolicy::date('2026-11-04'), LeavePolicy::date('2026-11-04'));
        $this->assertEquals(0, $summary['totals']['automatic']);
        $this->assertEquals(1, LeavePolicy::summary($employee, LeavePolicy::date('2026-11-03'), LeavePolicy::date('2026-11-03'))['totals']['automatic']);
    }
}
