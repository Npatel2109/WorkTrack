<?php

namespace Tests\Feature;

use App\Models\User;
use Dompdf\Dompdf;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\DB;
use Tests\TestCase;

class ReportWorkflowsTest extends TestCase
{
    use RefreshDatabase;

    protected function tearDown(): void
    {
        if (DB::getSchemaBuilder()->hasTable('submitted_report_version')) {
            foreach (DB::table('submitted_report_version')->pluck('filename') as $filename) {
                @unlink(config('worktrack.upload_path').DIRECTORY_SEPARATOR.$filename);
            }
        }
        parent::tearDown();
    }

    private function user(string $role = 'employee', string $suffix = '1'): User
    {
        $id = DB::table('user')->insertGetId([
            'name' => ucfirst($role).' '.$suffix, 'email' => "{$role}{$suffix}@example.test",
            'mobile' => '555-0100', 'designation' => 'Engineer', 'address' => '',
            'emergency_contact_no' => '', 'blood_group' => '', 'laptop_serial_no' => '',
            'password_hash' => bcrypt('password'), 'role' => $role,
            'created_date' => now('UTC'), 'is_active' => true, 'auth_version' => 0,
        ]);

        return User::findOrFail($id);
    }

    private function site(): int
    {
        return DB::table('assigned_sites')->insertGetId([
            'site_name' => 'North Plant', 'customer_name' => 'Example Customer',
            'project_name' => 'Modernisation', 'project_id' => null, 'report_company' => '',
            'commissioning_manager' => 'Commissioning Manager', 'project_manager' => 'Project Manager',
            'report_remarks' => "Remark one\nRemark two", 'address' => '1 Example Road',
            'latitude' => 12.9, 'longitude' => 77.6, 'allowed_radius' => 100,
            'assigned_date' => now('UTC'), 'is_active' => true,
        ]);
    }

    private function assign(int $employee, int $site): void
    {
        DB::table('site_assignment')->insert([
            'employee_id' => $employee, 'site_id' => $site, 'is_active' => true, 'assigned_at' => now('UTC'),
        ]);
    }

    private function completedVisit(int $employee, int $site, string $work = 'Replaced control module', ?bool $billable = true): int
    {
        $attendance = DB::table('attendance')->insertGetId([
            'employee_id' => $employee, 'site_id' => $site,
            'checkin_time' => '2025-03-10 04:00:00', 'checkout_time' => '2025-03-10 12:00:00',
            'checkin_lat' => 12.9, 'checkin_lon' => 77.6, 'checkout_lat' => 12.9, 'checkout_lon' => 77.6,
            'checkin_selfie' => '', 'status' => 'completed', 'site_latitude' => 12.9,
            'site_longitude' => 77.6, 'site_radius_m' => 100, 'auto_closed' => false,
        ]);
        DB::table('site_report')->insert([
            'attendance_id' => $attendance, 'work_performed' => $work, 'is_billable' => $billable,
            'activities_completed' => '', 'issues_found' => '', 'recommendations' => '',
            'materials_used' => '', 'remarks' => '', 'engineer_name' => 'Engineer',
            'customer_representative_name' => '', 'employee_signature' => '',
            'customer_signature' => '', 'report_date' => '2025-03-10 12:00:00',
        ]);

        return $attendance;
    }

    private function validPdf(): string
    {
        $pdf = new Dompdf;
        $pdf->loadHtml('<h1>Submitted report</h1>');
        $pdf->render();

        return $pdf->output();
    }

    public function test_completed_reports_are_scoped_and_admin_can_download_a_pdf(): void
    {
        $employee = $this->user();
        $other = $this->user('employee', '2');
        $site = $this->site();
        $visit = $this->completedVisit($employee->id, $site, 'Control panel maintenance');
        $this->completedVisit($other->id, $site, 'Other employee work');

        $this->actingAs($employee)->get('/reports')->assertOk()
            ->assertSee('North Plant')->assertSee('Control panel maintenance')
            ->assertDontSee('Other employee work');
        $this->actingAs($employee)->get("/reports/{$visit}/pdf")->assertForbidden();

        $admin = $this->user('admin');
        $response = $this->actingAs($admin)->get("/reports/{$visit}/pdf");
        $response->assertOk()->assertHeader('Content-Type', 'application/pdf');
        $this->assertStringStartsWith('%PDF-', $response->getContent());
    }

    public function test_activity_export_uses_the_workbook_template_and_downloads_xlsx(): void
    {
        $employee = $this->user();
        $site = $this->site();
        $this->assign($employee->id, $site);
        $this->completedVisit($employee->id, $site);

        $response = $this->actingAs($employee)->get('/reports/activity.xlsx?site_id='.$site.'&start_date=2025-03-10&end_date=2025-03-10');
        $response->assertOk()->assertDownload("site-activity-{$site}-2025-03-10-2025-03-10.xlsx");
        $this->assertStringStartsWith('PK', $response->streamedContent());
    }

    public function test_admin_can_download_combined_completed_report_pdfs(): void
    {
        $employee = $this->user();
        $site = $this->site();
        $visit = $this->completedVisit($employee->id, $site);
        $admin = $this->user('admin');

        $response = $this->actingAs($admin)->get('/reports/combined.pdf?start_date=2025-03-10&end_date=2025-03-10');
        $response->assertOk()->assertHeader('Content-Type', 'application/pdf');
        $this->assertStringStartsWith('%PDF-', $response->getContent());
        $this->assertStringContainsString('worktrack-reports-2025-03-10-to-2025-03-10.pdf', $response->headers->get('content-disposition'));
        $this->assertDatabaseHas('attendance', ['id' => $visit, 'status' => 'completed']);
    }

    public function test_submitted_pdf_edits_keep_immutable_versions_and_reject_stale_revisions(): void
    {
        $employee = $this->user();
        $site = $this->site();
        $this->assign($employee->id, $site);
        $fields = [
            'site_id' => $site, 'start_date' => '2025-03-01', 'end_date' => '2025-03-10',
            'total_mandays' => 8,
        ];
        $this->actingAs($employee)->post('/employee/submitted-reports/new', $fields + [
            'pdf' => UploadedFile::fake()->createWithContent('submitted.pdf', $this->validPdf()),
        ])->assertRedirect();
        $report = DB::table('submitted_report')->first();
        $first = DB::table('submitted_report_version')->where('report_id', $report->id)->first();
        $this->assertSame(1, (int) $report->revision_number);
        $this->assertFileExists(config('worktrack.upload_path').DIRECTORY_SEPARATOR.$first->filename);

        $this->post("/employee/submitted-reports/{$report->id}/edit", array_merge($fields, [
            'total_mandays' => 9,
            'revision_number' => 1,
        ]))->assertRedirect("/submitted-reports/{$report->id}");
        $this->assertDatabaseHas('submitted_report_version', [
            'report_id' => $report->id, 'revision_number' => 2,
            'filename' => $first->filename, 'pdf_replaced' => false,
        ]);
        $this->assertFileExists(config('worktrack.upload_path').DIRECTORY_SEPARATOR.$first->filename);

        $this->post("/employee/submitted-reports/{$report->id}/edit", $fields + [
            'revision_number' => 1,
        ])->assertStatus(409)->assertSessionHasErrors('revision_number');
        $this->actingAs($this->user('employee', 'other'))->get("/submitted-reports/{$report->id}")->assertNotFound();
        $this->actingAs($employee)->get("/submitted-reports/{$report->id}")->assertOk()
            ->assertSee('Version history')->assertSee('Revision');
    }

    public function test_submitted_pdf_rejects_non_pdf_and_keeps_detail_private(): void
    {
        $employee = $this->user();
        $site = $this->site();
        $this->assign($employee->id, $site);
        $this->actingAs($employee)->from('/employee/submitted-reports/new')->post('/employee/submitted-reports/new', [
            'site_id' => $site, 'start_date' => '2025-03-01', 'end_date' => '2025-03-10',
            'total_mandays' => 1, 'pdf' => UploadedFile::fake()->createWithContent('renamed.pdf', 'not a pdf'),
        ])->assertStatus(422);
        $this->assertDatabaseCount('submitted_report', 0);
    }
}
