<?php

namespace Tests\Feature;

use App\Models\User;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\File;
use Illuminate\Support\Facades\Hash;
use Tests\TestCase;

class AdminWorkflowsTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-05 10:00:00', 'UTC'));
        config(['worktrack.upload_path' => storage_path('framework'.DIRECTORY_SEPARATOR.'testing'.DIRECTORY_SEPARATOR.'admin-workflows-'.getmypid())]);
    }

    protected function tearDown(): void
    {
        $root = config('worktrack.upload_path');
        if ($root && is_dir($root)) {
            File::deleteDirectory($root);
        }
        CarbonImmutable::setTestNow();
        parent::tearDown();
    }

    private function user(string $role = 'admin'): User
    {
        return User::create(['name' => ucfirst($role), 'email' => uniqid().'@example.test', 'mobile' => '9999999999',
            'designation' => 'Engineer', 'password_hash' => Hash::make('A secure password 123'),
            'role' => $role, 'is_active' => true, 'auth_version' => 0, 'created_date' => '2026-11-01 00:00:00']);
    }

    private function site(): int
    {
        return DB::table('assigned_sites')->insertGetId(['site_name' => 'Factory', 'customer_name' => 'Customer',
            'address' => 'Mumbai', 'latitude' => 19.1, 'longitude' => 72.9, 'allowed_radius' => 100,
            'assigned_date' => WorkTrack::now(), 'is_active' => true]);
    }

    private function employeeData(): array
    {
        return ['name' => 'Test Engineer', 'email' => 'engineer@example.test', 'mobile' => '1234567890',
            'designation' => 'Engineer', 'password' => 'A secure password 123', 'employee_code' => ' eng-01 ',
            'address' => 'Mumbai', 'birthday' => '1990-02-01', 'blood_group' => 'o+'];
    }

    public function test_admin_routes_enforce_authentication_and_role(): void
    {
        $this->get('/admin/employees')->assertRedirect('/login');
        $this->actingAs($this->user('employee'))->get('/admin/employees')->assertForbidden();
        $this->get('/admin/referrals')->assertForbidden();
    }

    public function test_employee_creation_normalizes_code_and_validates_duplicates(): void
    {
        $this->actingAs($this->user())->post('/admin/employees', $this->employeeData())->assertSessionHasNoErrors()->assertRedirect('/admin/employees');
        $employee = DB::table('user')->where('email', 'engineer@example.test')->first();
        $this->assertSame('ENG-01', $employee->employee_code);
        $this->assertSame('O+', $employee->blood_group);
        $this->assertTrue(Hash::check('A secure password 123', $employee->password_hash));
        $data = $this->employeeData();
        $data['email'] = 'another@example.test';
        $this->post('/admin/employees', $data)->assertSessionHasErrors('employee_code');
        $this->assertDatabaseCount('user', 2);
    }

    public function test_employee_with_history_is_deactivated_and_password_reset_invalidates_sessions(): void
    {
        $admin = $this->user();
        $employee = $this->user('employee');
        DB::table('leave_request')->insert(['employee_id' => $employee->id, 'start_date' => '2026-11-06',
            'end_date' => '2026-11-06', 'period' => 'full', 'reason' => 'Personal', 'status' => 'pending', 'created_at' => WorkTrack::now()]);
        $this->actingAs($admin)->post('/admin/employees/'.$employee->id.'/reset-password', ['password' => 'Another safe password 123'])->assertSessionHasNoErrors();
        $this->assertDatabaseHas('user', ['id' => $employee->id, 'auth_version' => 1]);
        $this->post('/admin/employees/'.$employee->id.'/delete')->assertSessionHasNoErrors();
        $this->assertDatabaseHas('user', ['id' => $employee->id, 'is_active' => false, 'auth_version' => 2]);
        $this->assertDatabaseHas('leave_request', ['employee_id' => $employee->id]);
    }

    public function test_assignments_preserve_membership_rows_and_block_active_removal(): void
    {
        $employee = $this->user('employee');
        $site = $this->site();
        $this->actingAs($this->user())->post('/admin/assignments', ['employee_id' => $employee->id, 'site_ids' => [$site]])->assertSessionHasNoErrors();
        $membership = DB::table('site_assignment')->first();
        $this->post('/admin/assignments', ['employee_id' => $employee->id])->assertSessionHasNoErrors();
        $this->assertDatabaseHas('site_assignment', ['id' => $membership->id, 'is_active' => false]);
        $this->post('/admin/assignments', ['employee_id' => $employee->id, 'site_ids' => [$site]])->assertSessionHasNoErrors();
        DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site, 'checkin_time' => WorkTrack::now(),
            'checkin_lat' => 19.1, 'checkin_lon' => 72.9, 'checkin_selfie' => 'test.png', 'status' => 'onsite']);
        $this->post('/admin/assignments', ['employee_id' => $employee->id])->assertSessionHasErrors('admin');
        $this->assertDatabaseHas('site_assignment', ['id' => $membership->id, 'is_active' => true]);
        $this->post('/admin/sites/'.$site.'/delete')->assertSessionHasErrors('admin');
    }

    public function test_projects_rename_linked_sites_and_reject_deletion_with_links(): void
    {
        $this->actingAs($this->user())->post('/admin/projects', ['project_number' => ' proj-1 ', 'name' => 'Original'])->assertSessionHasNoErrors();
        $project = DB::table('project')->first();
        $site = $this->site();
        DB::table('assigned_sites')->where('id', $site)->update(['project_id' => $project->id, 'project_name' => 'Original']);
        $this->post('/admin/projects/'.$project->id.'/edit', ['project_number' => 'PROJ-1', 'name' => 'Renamed'])->assertSessionHasNoErrors();
        $this->assertDatabaseHas('assigned_sites', ['id' => $site, 'project_name' => 'Renamed']);
        $this->post('/admin/projects/'.$project->id.'/delete')->assertSessionHasErrors('admin');
    }

    public function test_attendance_filter_uses_ist_boundaries_and_displays_names(): void
    {
        $employee = $this->user('employee');
        $site = $this->site();
        foreach (['2026-11-04 18:29:00', '2026-11-04 18:30:00'] as $time) {
            DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site, 'checkin_time' => $time,
                'checkin_lat' => 19.1, 'checkin_lon' => 72.9, 'checkin_selfie' => 'test.png',
                'status' => 'completed', 'checkin_distance_m' => 12.5]);
        }
        $response = $this->actingAs($this->user())->get('/admin/attendance?from_date=2026-11-05&to_date=2026-11-05');
        $response->assertOk()->assertSee('Factory')->assertSee($employee->name)->assertSee('12.5 m')->assertSee('05 Nov 2026 00:00');
        $this->assertCount(1, $response->viewData('records'));
    }

    public function test_admin_pages_render_and_settings_keep_existing_smtp_password(): void
    {
        $employee = $this->user('employee');
        $this->actingAs($this->user());
        foreach (['/admin', '/admin/employees', '/admin/sites', '/admin/projects', '/admin/assignments', '/admin/settings',
            '/admin/leaves', '/admin/work-status', '/admin/referrals'] as $url) {
            $this->get($url)->assertOk();
        }
        $this->post('/admin/settings', ['section' => 'mail', 'mail_server' => 'smtp.example.test', 'mail_port' => 465,
            'mail_security' => 'ssl', 'mail_sender' => 'sender@example.test', 'mail_password' => 'secret'])->assertSessionHasNoErrors();
        $this->post('/admin/settings', ['section' => 'mail', 'mail_server' => 'smtp.example.test', 'mail_port' => 465,
            'mail_security' => 'ssl', 'mail_sender' => 'sender@example.test', 'mail_password' => ''])->assertSessionHasNoErrors();
        $this->assertDatabaseHas('app_settings', ['id' => 1, 'mail_password' => 'secret']);
        $this->actingAs($employee)->get('/employee')->assertOk()->assertSee('Welcome');
    }

    public function test_referrals_accept_only_pdf_and_admin_downloads_private_resume(): void
    {
        $employee = $this->user('employee');
        $data = ['candidate_name' => 'Candidate', 'candidate_email' => 'candidate@example.test'];
        $this->actingAs($employee)->post('/employee/referrals', $data + ['resume' => UploadedFile::fake()->createWithContent('resume.pdf', "%PDF-1.4\n".str_repeat('x', 2048))])
            ->assertSessionHasNoErrors()->assertRedirect('/employee/referrals');
        $referral = DB::table('candidate_referral')->first();
        $this->assertFileExists(WorkTrack::uploadPath().DIRECTORY_SEPARATOR.'referral-resumes'.DIRECTORY_SEPARATOR.$referral->resume_filename);
        $this->get('/admin/referrals/'.$referral->id.'/resume')->assertForbidden();
        $this->actingAs($this->user())->get('/admin/referrals/'.$referral->id.'/resume')->assertDownload('resume.pdf');
        $this->actingAs($employee)->post('/employee/referrals', $data + ['resume' => UploadedFile::fake()->create('resume.docx', 2)])
            ->assertSessionHasErrors('resume');
    }

    public function test_site_creation_and_edit_enforce_coordinates_and_active_visit_protection(): void
    {
        $this->actingAs($this->user());
        $data = ['site_name' => 'New site', 'customer_name' => 'Client', 'address' => 'Mumbai',
            'latitude' => 19.1, 'longitude' => 72.9, 'allowed_radius' => 100,
            'commissioning_manager' => 'Manager', 'report_remarks' => "Line 1\nLine 2"];
        $this->post('/admin/sites', $data)->assertSessionHasNoErrors();
        $site = DB::table('assigned_sites')->first();
        $this->assertSame('Blackpurple automation', $site->report_company);
        $this->post('/admin/sites/'.$site->id.'/edit', array_replace($data, ['latitude' => 91]))->assertSessionHasErrors('latitude');
        $employee = $this->user('employee');
        DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site->id,
            'checkin_time' => WorkTrack::now(), 'checkin_lat' => 19.1, 'checkin_lon' => 72.9,
            'checkin_selfie' => 'test.png', 'status' => 'onsite']);
        $this->post('/admin/sites/'.$site->id.'/edit', $data)->assertSessionHasErrors('admin');
    }

    public function test_logo_storage_is_private_and_removal_cleans_up_the_file(): void
    {
        $this->actingAs($this->user())->post('/admin/settings', [
            'section' => 'brand', 'action' => 'upload', 'brand_logo' => UploadedFile::fake()->image('logo.png', 100, 100),
        ])->assertSessionHasNoErrors();
        $filename = DB::table('app_settings')->where('id', 1)->value('brand_logo_filename');
        $path = WorkTrack::uploadPath($filename);
        $this->assertFileExists($path);
        $this->post('/admin/settings', ['section' => 'brand', 'action' => 'remove'])->assertSessionHasNoErrors();
        $this->assertFileDoesNotExist($path);
        $this->assertDatabaseHas('app_settings', ['id' => 1, 'brand_logo_filename' => null]);
    }

    public function test_notification_failure_does_not_claim_delivery_or_lose_the_referral(): void
    {
        DB::table('app_settings')->insert(['id' => 1, 'referral_notification_email' => 'hr@example.test']);
        $this->actingAs($this->user('employee'))->post('/employee/referrals', [
            'candidate_name' => 'Candidate', 'candidate_email' => 'candidate@example.test',
            'resume' => UploadedFile::fake()->createWithContent('resume.pdf', "%PDF-1.4\n".str_repeat('x', 2048)),
        ])->assertRedirect('/employee/referrals')->assertSessionHas('error');
        $this->assertDatabaseCount('candidate_referral', 1);
        $this->assertDatabaseHas('candidate_referral', ['candidate_name' => 'Candidate']);
    }

    public function test_imported_root_logo_is_removed_when_replaced(): void
    {
        $old = str_repeat('a', 32).'.png';
        file_put_contents(WorkTrack::uploadPath($old), 'imported logo');
        DB::table('app_settings')->insert(['id' => 1, 'brand_logo_filename' => $old]);
        $this->actingAs($this->user())->post('/admin/settings', [
            'section' => 'brand', 'action' => 'upload', 'brand_logo' => UploadedFile::fake()->image('replacement.png', 100, 100),
        ])->assertSessionHasNoErrors();
        $new = DB::table('app_settings')->where('id', 1)->value('brand_logo_filename');
        $this->assertFileDoesNotExist(WorkTrack::uploadPath($old));
        $this->assertFileExists(WorkTrack::uploadPath($new));
    }

    public function test_metric_routes_match_supported_flask_metrics_and_require_admin(): void
    {
        $this->get('/admin/metrics/employees')->assertRedirect('/login');
        $this->actingAs($this->user('employee'))->get('/admin/metrics/employees')->assertForbidden();
        $this->getJson('/api/admin/live-status')->assertForbidden();
        $employee = $this->user('employee');
        $site = $this->site();
        $this->actingAs($this->user());
        foreach (['employees', 'sites', 'onsite', 'pending-reports', 'completed', 'reports'] as $metric) {
            $this->get('/admin/metrics/'.$metric)->assertOk();
        }
        $this->get('/admin/metrics/unknown')->assertNotFound();
        $this->get('/admin/metrics/sites')->assertSee('Unassigned')->assertSee('Factory');
        DB::table('site_assignment')->insert(['employee_id' => $employee->id, 'site_id' => $site,
            'is_active' => true, 'assigned_at' => WorkTrack::now()]);
        $this->get('/admin/metrics/sites')->assertSee($employee->name);
        foreach (['2026-11-04 18:29:59', '2026-11-04 18:30:00'] as $time) {
            DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site,
                'checkin_time' => '2026-11-04 10:00:00', 'checkout_time' => $time,
                'checkin_lat' => 19.1, 'checkin_lon' => 72.9, 'checkin_selfie' => 'test.png', 'status' => 'completed']);
        }
        $response = $this->get('/admin/metrics/completed')->assertOk()->assertSee('05 Nov 2026 00:00 IST');
        $this->assertCount(1, $response->viewData('rows'));
    }

    public function test_live_status_matches_flask_payload_precedence_and_daily_details(): void
    {
        $admin = $this->user();
        $onsite = $this->user('employee');
        $pending = $this->user('employee');
        $assigned = $this->user('employee');
        $onLeave = $this->user('employee');
        $disabled = $this->user('employee');
        $completed = $this->user('employee');
        DB::table('user')->where('id', $disabled->id)->update(['is_active' => false]);
        $site = $this->site();
        foreach ([$onsite, $pending, $assigned] as $employee) {
            DB::table('site_assignment')->insert(['employee_id' => $employee->id, 'site_id' => $site,
                'is_active' => true, 'assigned_at' => WorkTrack::now()]);
        }
        foreach ([[$onsite, 'onsite'], [$pending, 'report_pending'], [$completed, 'completed']] as [$employee, $status]) {
            DB::table('attendance')->insert(['employee_id' => $employee->id, 'site_id' => $site,
                'checkin_time' => '2026-11-05 08:00:00',
                'checkout_time' => $status === 'onsite' ? null : '2026-11-05 09:00:00',
                'checkin_lat' => 19.1, 'checkin_lon' => 72.9, 'checkin_selfie' => 'test.png', 'status' => $status]);
        }
        DB::table('audit_log')->insert(['user_id' => $assigned->id, 'action' => 'login', 'created_at' => WorkTrack::now()]);
        foreach ([$onsite, $onLeave] as $employee) {
            DB::table('leave_request')->insert(['employee_id' => $employee->id, 'start_date' => '2026-11-05',
                'end_date' => '2026-11-05', 'period' => 'morning', 'reason' => 'Half day', 'status' => 'approved',
                'created_at' => WorkTrack::now(), 'reviewed_by_id' => $admin->id, 'reviewed_at' => WorkTrack::now()]);
        }
        DB::table('daily_work_status')->insert(['employee_id' => $assigned->id, 'work_date' => '2026-11-05',
            'work_type' => 'wfh', 'reason' => 'Remote', 'status' => 'pending', 'created_at' => WorkTrack::now()]);
        $payload = $this->actingAs($admin)->getJson('/api/admin/live-status')->assertOk()
            ->assertJsonStructure(['pending_leave_count', 'on_leave_count', 'onsite_count', 'pending_report_count',
                'employees' => [['name', 'designation', 'site', 'customer', 'checkin_time', 'onsite_hours', 'status', 'daily_statuses']]])
            ->assertJsonPath('onsite_count', 1)->assertJsonPath('pending_report_count', 1)
            ->assertJsonPath('on_leave_count', 2)->json();
        $this->assertCount(6, $payload['employees']);
        $this->assertEqualsCanonicalizing(['onsite', 'report-pending', 'assigned', 'on-leave', 'offline', 'completed'],
            array_column($payload['employees'], 'status'));
        $liveOnsite = collect($payload['employees'])->firstWhere('status', 'onsite');
        $this->assertSame('13:30 IST', $liveOnsite['checkin_time']);
        $this->assertSame('2h 00m', $liveOnsite['onsite_hours']);
        $this->assertSame('Factory', $liveOnsite['site']);
        $this->assertStringContainsString('Reviewed by Admin at 15:30 IST', $liveOnsite['daily_statuses'][1]['detail']);
    }

    public function test_dashboard_renders_only_active_map_sites_and_safe_polling_hookup(): void
    {
        $active = $this->site();
        $inactive = $this->site();
        DB::table('assigned_sites')->where('id', $active)->update(['site_name' => '</script><img src=x onerror=alert(1)>']);
        DB::table('assigned_sites')->where('id', $inactive)->update(['site_name' => 'Inactive secret site', 'is_active' => false]);
        $response = $this->actingAs($this->user())->get('/admin')->assertOk()
            ->assertSee('id="admin-site-map"', false)->assertSee('id="admin-map-sites"', false)
            ->assertSee('id="admin-live-rows"', false)->assertSee('/static/js/admin-live.js', false)
            ->assertSee('/api/admin/live-status', false)->assertSee('aria-live="polite"', false)
            ->assertDontSee('Inactive secret site')->assertDontSee('</script><img src=x onerror=alert(1)>', false);
        $this->assertCount(1, $response->viewData('mapSites'));
        preg_match('/<script type="application\/json" id="admin-map-sites">(.*?)<\/script>/s', $response->getContent(), $matches);
        $mapData = json_decode($matches[1], true, 512, JSON_THROW_ON_ERROR);
        $this->assertSame('</script><img src=x onerror=alert(1)>', $mapData[0]['site_name']);
        $this->assertEquals(19.1, $mapData[0]['latitude']);
        $script = file_get_contents(public_path('static'.DIRECTORY_SEPARATOR.'js'.DIRECTORY_SEPARATOR.'admin-live.js'));
        $this->assertStringContainsString('setTimeout(refresh, 30000)', $script);
        $this->assertStringContainsString('document.hidden', $script);
        $this->assertStringContainsString("document.createTextNode(' · ' + daily.status)", $script);
        $this->assertStringNotContainsString('innerHTML', $script);
        $this->assertStringContainsString('Displayed values have been retained', $script);
    }
}
