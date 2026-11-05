<?php

namespace Tests\Feature;

use App\Models\User;
use App\Services\AttendanceService;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Tests\TestCase;

class AuthAttendanceTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-05 04:00:00', 'UTC'));
        config(['worktrack.upload_path' => storage_path('framework/testing/uploads')]);
    }

    protected function tearDown(): void
    {
        CarbonImmutable::setTestNow();
        foreach (glob(storage_path('framework/testing/uploads/*.png')) ?: [] as $path) {
            unlink($path);
        }
        parent::tearDown();
    }

    private function employee(): User
    {
        return User::factory()->create(['email' => 'engineer@example.com', 'password_hash' => Hash::make('correct-password')]);
    }

    private function site(User $user): int
    {
        $site = DB::table('assigned_sites')->insertGetId(['site_name' => 'Test Site', 'customer_name' => 'Customer', 'address' => 'Address', 'latitude' => 23.0, 'longitude' => 72.0, 'allowed_radius' => 100, 'is_active' => true, 'assigned_date' => WorkTrack::now()]);
        DB::table('site_assignment')->insert(['employee_id' => $user->id, 'site_id' => $site, 'is_active' => true, 'assigned_at' => WorkTrack::now()]);

        return $site;
    }

    private function start(User $user, int $site): int
    {
        $this->actingAs($user)->postJson("/api/visit/{$site}/verify-checkin", ['latitude' => 24, 'longitude' => 72])
            ->assertOk()->assertJson(['verified' => true, 'within_radius' => false]);
        $image = imagecreatetruecolor(2, 2);
        ob_start();
        imagepng($image);
        $raw = ob_get_clean();
        imagedestroy($image);
        $this->post("/visit/{$site}/checkin", ['selfie' => 'data:image/png;base64,'.base64_encode($raw)])->assertRedirect('/employee');

        return DB::table('attendance')->value('id');
    }

    public function test_login_and_revocation(): void
    {
        $user = $this->employee();
        $this->post('/login', ['email' => $user->email, 'password' => 'correct-password'])->assertRedirect('/employee');
        $this->assertAuthenticatedAs($user);
        $user->increment('auth_version');
        $this->getJson('/api/employee/active-visit')->assertUnauthorized();
        $this->assertGuest();
    }

    public function test_legacy_scrypt_requires_explicit_password_reset(): void
    {
        $user = $this->employee();
        $user->update(['password_hash' => 'scrypt:32768:8:1$salt$hash']);
        $this->post('/login', ['email' => $user->email, 'password' => 'anything'])->assertSessionHasErrors('email');
        $this->assertGuest();
    }

    public function test_pbkdf2_is_verified_and_upgraded(): void
    {
        $user = $this->employee();
        $user->update(['password_hash' => 'pbkdf2:sha256:1000$salt$'.hash_pbkdf2('sha256', 'correct-password', 'salt', 1000)]);
        $this->post('/login', ['email' => $user->email, 'password' => 'correct-password'])->assertRedirect('/employee');
        $this->assertTrue(Hash::check('correct-password', $user->fresh()->password_hash));
    }

    public function test_full_visit_outside_radius_last_known_checkout_report_and_monitor(): void
    {
        $user = $this->employee();
        $site = $this->site($user);
        $id = $this->start($user, $site);
        $active = $this->getJson('/api/employee/active-visit')->assertOk()->assertJson(['active' => true, 'attendance_id' => $id])->json();
        $this->withHeader('Authorization', 'Bearer '.$active['monitoring_token'])->getJson('/api/visit-monitor')
            ->assertOk()->assertHeader('Cache-Control', 'no-store, private')->assertJson(['active' => true]);
        $this->postJson("/api/visit/{$id}/verify-checkout", ['location_unavailable' => true, 'reason' => 'timeout'])
            ->assertOk()->assertJson(['location_source' => 'last_known', 'verified' => true]);
        $this->assertDatabaseHas('attendance', ['id' => $id, 'status' => 'report_pending', 'checkout_lat' => 24]);
        $this->post("/visit/{$id}/report", ['activity_detail' => 'Commissioned equipment', 'billing' => 'billable', 'engineer_name' => $user->name])->assertRedirect('/employee');
        $this->assertDatabaseHas('attendance', ['id' => $id, 'status' => 'completed']);
        $this->assertDatabaseHas('site_report', ['attendance_id' => $id, 'work_performed' => 'Commissioned equipment', 'is_billable' => true]);
        $this->post("/visit/{$id}/report", ['activity_detail' => 'duplicate', 'billing' => 'billable', 'engineer_name' => $user->name])->assertForbidden();
    }

    public function test_authorization_expired_verification_and_invalid_gps(): void
    {
        $user = $this->employee();
        $site = $this->site($user);
        $this->actingAs($user)->postJson("/api/visit/{$site}/verify-checkin", ['latitude' => 999, 'longitude' => 72])->assertUnprocessable();
        $this->postJson("/api/visit/{$site}/verify-checkin", ['latitude' => 23, 'longitude' => 72])->assertOk();
        CarbonImmutable::setTestNow(WorkTrack::now()->addMinutes(11));
        $this->get("/visit/{$site}/camera")->assertForbidden();
        $other = User::factory()->create();
        $this->actingAs($other)->postJson("/api/visit/{$site}/verify-checkin", ['latitude' => 23, 'longitude' => 72])->assertNotFound();
        $this->get('/admin/employees')->assertForbidden();
        $this->withHeader('Authorization', 'Bearer invalid')->getJson('/api/visit-monitor')->assertUnauthorized();
    }

    public function test_daily_cutoff_is_exact_2359_ist_and_idempotent(): void
    {
        $user = $this->employee();
        $site = $this->site($user);
        $id = $this->start($user, $site);
        CarbonImmutable::setTestNow(CarbonImmutable::parse('2026-11-06 04:00:00', 'UTC'));
        $service = new AttendanceService;
        $this->assertSame(1, $service->closeOverdue());
        $this->assertSame(0, $service->closeOverdue());
        $this->assertDatabaseHas('attendance', ['id' => $id, 'checkout_time' => '2026-11-05 18:29:00', 'status' => 'report_pending', 'auto_closed' => true, 'checkout_location_source' => 'last_known']);
    }

    public function test_approved_opposite_half_days_block_site_start(): void
    {
        $user = $this->employee();
        $site = $this->site($user);
        foreach (['morning', 'afternoon'] as $period) {
            DB::table('leave_request')->insert(['employee_id' => $user->id, 'start_date' => '2026-11-05', 'end_date' => '2026-11-05', 'period' => $period, 'reason' => 'Leave', 'status' => 'approved', 'created_at' => WorkTrack::now()]);
        }
        $this->actingAs($user)->postJson("/api/visit/{$site}/verify-checkin", ['latitude' => 23, 'longitude' => 72])->assertConflict();
    }

    public function test_import_database_validation_command(): void
    {
        $this->artisan('worktrack:database-check')->assertSuccessful();
    }

    public function test_invalid_selfie_is_rejected_without_starting_visit(): void
    {
        $user = $this->employee();
        $site = $this->site($user);
        $this->actingAs($user)->postJson("/api/visit/{$site}/verify-checkin", ['latitude' => 23, 'longitude' => 72])->assertOk();
        $this->postJson("/visit/{$site}/checkin", ['selfie' => 'data:image/png;base64,'.base64_encode('not an image')])->assertUnprocessable();
        $this->assertDatabaseCount('attendance', 0);
    }
}
