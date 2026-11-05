<?php

namespace Tests\Feature;

use App\Models\User;
use App\Services\WorkTrack;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Password;
use Illuminate\Support\Facades\Schema;
use PHPUnit\Framework\Attributes\Group;
use Tests\TestCase;

class MigrationSafetyTest extends TestCase
{
    use RefreshDatabase;

    public function test_complete_import_is_adopted_without_changing_rows(): void
    {
        $user = User::factory()->create();
        $hash = $user->password_hash;
        $migration = require database_path('migrations/2026_11_05_000000_worktrack_schema.php');
        $migration->up();
        $this->assertSame($hash, User::findOrFail($user->id)->password_hash);
        $this->assertSame(1, DB::table('user')->count());
    }

    #[Group('schema-ddl')]
    public function test_partial_import_is_rejected_not_silently_repaired(): void
    {
        Schema::drop('audit_log');
        $migration = require database_path('migrations/2026_11_05_000000_worktrack_schema.php');
        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessage('missing audit_log');
        $migration->up();
    }

    public function test_rollback_cannot_destroy_imported_data(): void
    {
        $migration = require database_path('migrations/2026_11_05_000000_worktrack_schema.php');
        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessage('Automatic rollback is disabled');
        $migration->down();
    }

    public function test_password_recovery_and_reset_revoke_existing_sessions(): void
    {
        $user = User::factory()->create(['email' => 'recovery@example.com']);
        $token = Password::createToken($user);
        $this->post('/reset-password/'.$token, ['email' => $user->email, 'password' => 'new-secure-password', 'password_confirmation' => 'new-secure-password'])
            ->assertRedirect('/login');
        $this->assertSame(1, $user->fresh()->auth_version);
        $this->assertTrue(Hash::check('new-secure-password', $user->fresh()->password_hash));
        $this->post('/reset-password/'.$token, ['email' => $user->email, 'password' => 'another-password', 'password_confirmation' => 'another-password'])
            ->assertSessionHasErrors('email');
    }

    public function test_guest_access_and_login_view(): void
    {
        $this->get('/')->assertRedirect('/login');
        $this->get('/login')->assertOk()->assertSee('Welcome to WorkTrack');
        $this->get('/admin')->assertRedirect('/login');
        $this->post('/logout')->assertRedirect('/login');
    }

    public function test_first_administrator_activates_policy_without_seeded_business_data(): void
    {
        $this->artisan('worktrack:create-admin')
            ->expectsQuestion('Name', 'Installation Admin')
            ->expectsQuestion('Email', 'install@example.test')
            ->expectsQuestion('Mobile', '9999999999')
            ->expectsQuestion('Password', 'installation-password')
            ->assertSuccessful();
        $this->assertDatabaseHas('user', ['email' => 'install@example.test', 'role' => 'admin']);
        $this->assertDatabaseHas('attendance_policy', ['id' => 1, 'effective_from' => WorkTrack::today()->toDateString()]);
        $this->assertDatabaseCount('assigned_sites', 0);
    }
}
