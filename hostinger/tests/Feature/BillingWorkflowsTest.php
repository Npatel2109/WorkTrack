<?php

namespace Tests\Feature;

use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Tests\TestCase;

class BillingWorkflowsTest extends TestCase
{
    use RefreshDatabase;

    private function user(string $role, string $suffix): User
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

    private function assignedSite(int $employee): int
    {
        $site = DB::table('assigned_sites')->insertGetId([
            'site_name' => 'South Plant', 'customer_name' => 'Example Customer',
            'project_name' => 'Upgrade', 'project_id' => null, 'report_company' => '',
            'commissioning_manager' => '', 'project_manager' => '', 'report_remarks' => '',
            'address' => '2 Example Road', 'latitude' => 12.9, 'longitude' => 77.6,
            'allowed_radius' => 100, 'assigned_date' => now('UTC'), 'is_active' => true,
        ]);
        DB::table('site_assignment')->insert([
            'employee_id' => $employee, 'site_id' => $site, 'is_active' => true, 'assigned_at' => now('UTC'),
        ]);

        return $site;
    }

    public function test_admin_can_create_purchase_orders_and_invoice_and_download_pdf(): void
    {
        $admin = $this->user('admin', '1');
        $employee = $this->user('employee', '1');
        $site = $this->assignedSite($employee->id);
        $this->actingAs($admin)->get('/admin/invoices')->assertOk()->assertSee('Create invoice');

        $this->post('/admin/invoices', [
            'form_action' => 'create_po', 'po_number' => 'PO-55', 'po_customer_name' => 'Example Customer',
            'po_employee_id' => $employee->id, 'po_site_id' => $site,
            'po_start_date' => '2025-03-01', 'po_end_date' => '2025-03-31', 'total_mandays' => 20,
        ])->assertRedirect('/admin/invoices');
        $this->assertDatabaseHas('purchase_order', ['po_number' => 'PO-55', 'total_mandays' => 20]);

        $this->post('/admin/invoices', [
            'employee_id' => $employee->id, 'site_id' => $site,
            'customer_name' => 'Example Customer', 'po_number' => 'PO-55',
            'po_date' => '2025-03-01', 'start_date' => '2025-03-01', 'end_date' => '2025-03-05',
            'billable_days' => 3, 'daily_rate' => '125.50', 'currency' => 'INR',
            'description' => 'Commissioning service',
        ])->assertRedirect('/admin/invoices');
        $invoice = DB::table('invoice')->first();
        $this->assertSame(376.5, (float) $invoice->total_amount);
        $this->assertDatabaseHas('purchase_order', ['po_number' => 'PO-55', 'invoice_generated' => true]);
        $pdf = $this->get("/admin/invoices/{$invoice->id}/pdf");
        $pdf->assertOk()->assertHeader('Content-Type', 'application/pdf');
        $this->assertStringStartsWith('%PDF-', $pdf->getContent());
    }

    public function test_billing_is_admin_only_and_invoice_validation_is_enforced(): void
    {
        $employee = $this->user('employee', '1');
        $site = $this->assignedSite($employee->id);
        $this->actingAs($employee)->get('/admin/invoices')->assertForbidden();

        $admin = $this->user('admin', '1');
        $this->actingAs($admin)->from('/admin/invoices')->post('/admin/invoices', [
            'employee_id' => $employee->id, 'site_id' => $site,
            'customer_name' => 'Customer', 'start_date' => '2025-03-05',
            'end_date' => '2025-03-01', 'billable_days' => 5, 'daily_rate' => '100',
            'currency' => 'INR', 'description' => 'Invalid dates',
        ])->assertSessionHasErrors('end_date');
        $this->assertDatabaseCount('invoice', 0);
    }

    public function test_purchase_order_status_updates_are_persisted(): void
    {
        $admin = $this->user('admin', '1');
        $employee = $this->user('employee', '1');
        $site = $this->assignedSite($employee->id);
        $id = DB::table('purchase_order')->insertGetId([
            'po_number' => 'PO-STATUS', 'customer_name' => 'Customer', 'employee_id' => $employee->id,
            'site_id' => $site, 'start_date' => '2025-03-01', 'end_date' => '2025-03-31',
            'total_mandays' => 10, 'used_mandays' => 0, 'invoice_generated' => false,
            'money_received' => false, 'completion_email_sent' => false, 'created_at' => now('UTC'),
        ]);
        $this->actingAs($admin)->post("/admin/purchase-orders/{$id}/status", [
            'invoice_generated' => 'yes', 'money_received' => 'yes',
        ])->assertRedirect('/admin/invoices');
        $this->assertDatabaseHas('purchase_order', [
            'id' => $id, 'invoice_generated' => true, 'money_received' => true,
        ]);
    }
}
