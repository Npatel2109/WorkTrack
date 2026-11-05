<?php

namespace App\Http\Controllers;

use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Dompdf\Dompdf;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Str;

class BillingController extends Controller
{
    private const CURRENCIES = ['INR', 'USD', 'EUR', 'GBP', 'AED'];

    public function index()
    {
        $assignments = DB::table('site_assignment as sa')
            ->join('assigned_sites as s', 's.id', '=', 'sa.site_id')
            ->join('user as u', 'u.id', '=', 'sa.employee_id')
            ->where('sa.is_active', true)->where('s.is_active', true)->where('u.is_active', true)
            ->orderBy('u.name')->orderBy('s.site_name')
            ->get(['sa.employee_id', 'sa.site_id', 'u.name as employee_name', 's.site_name']);
        $invoices = DB::table('invoice as i')
            ->join('user as u', 'u.id', '=', 'i.employee_id')
            ->join('assigned_sites as s', 's.id', '=', 'i.site_id')
            ->orderByDesc('i.created_at')->limit(500)
            ->get(['i.*', 'u.name as employee_name', 's.site_name']);
        $notifications = [];
        $orders = DB::table('purchase_order as po')
            ->join('user as u', 'u.id', '=', 'po.employee_id')
            ->join('assigned_sites as s', 's.id', '=', 'po.site_id')
            ->orderByDesc('po.created_at')
            ->get(['po.*', 'u.name as employee_name', 's.site_name']);
        foreach ($orders as $order) {
            $used = $this->usedMandays($order);
            $generated = DB::table('invoice')->where('po_number', $order->po_number)->exists();
            $updates = [];
            if ((int) $order->used_mandays !== $used) {
                $updates['used_mandays'] = $used;
            }
            if ((bool) $order->invoice_generated !== $generated) {
                $updates['invoice_generated'] = $generated;
            }
            if ($updates) {
                DB::table('purchase_order')->where('id', $order->id)->update($updates);
                $order->used_mandays = $used;
                $order->invoice_generated = $generated;
            }

            if ((int) $order->total_mandays > 0 && $used >= (int) $order->total_mandays && ! $order->completion_email_sent) {
                $settings = DB::table('app_settings')->where('id', 1)->first();
                if (! $settings || ! $settings->po_notification_email) {
                    $notifications[$order->id] = 'Delivery failed: PO notification email is not configured.';
                    WorkTrack::audit(
                        'purchase_order_completion_notification_failed',
                        "PO {$order->po_number}; {$used} of {$order->total_mandays} man-days; no notification recipient is configured.",
                    );

                    continue;
                }
                try {
                    $mailHost = $settings->mail_server ?: config('mail.mailers.smtp.host');
                    $mailSender = $settings->mail_sender ?: config('mail.from.address');
                    if (! $mailHost || ! $mailSender) {
                        throw new \RuntimeException('SMTP host and sender address are not configured.');
                    }
                    WorkTrack::configureMail();
                    config(['mail.from.address' => $mailSender]);
                    if (! $settings->mail_server) {
                        config(['mail.default' => 'smtp']);
                    }
                    Mail::purge('smtp');
                    Mail::raw("PO {$order->po_number} for {$order->customer_name} has used {$used} of {$order->total_mandays} allocated man-days. Please review pending invoice and payment status.", function ($message) use ($settings, $order) {
                        $message->to($settings->po_notification_email)
                            ->subject("PO {$order->po_number} man-days completed");
                    });
                    DB::table('purchase_order')->where('id', $order->id)->update(['completion_email_sent' => true]);
                    WorkTrack::audit('purchase_order_completion_notification_sent', "PO {$order->po_number}; {$used} of {$order->total_mandays} man-days; sent to {$settings->po_notification_email}");
                    $order->completion_email_sent = true;
                    $notifications[$order->id] = "Sent to {$settings->po_notification_email}.";
                } catch (\Throwable $exception) {
                    $message = Str::limit($exception->getMessage(), 500, '');
                    WorkTrack::audit('purchase_order_completion_notification_failed', "PO {$order->po_number}; {$used} of {$order->total_mandays} man-days; recipient={$settings->po_notification_email}; error={$message}");
                    $notifications[$order->id] = 'Delivery failed: '.$message;
                }
            }
        }

        return view('billing.index', compact('assignments', 'invoices', 'orders', 'notifications'));
    }

    public function create(Request $request)
    {
        if ($request->input('form_action') === 'create_po') {
            return $this->createPurchaseOrder($request);
        }

        return $this->createInvoice($request);
    }

    private function createPurchaseOrder(Request $request)
    {
        $request->merge([
            'po_number' => trim((string) $request->input('po_number', '')),
            'po_customer_name' => trim((string) $request->input('po_customer_name', '')),
        ]);
        $data = $request->validate([
            'po_number' => ['required', 'string', 'max:120', 'unique:purchase_order,po_number'],
            'po_customer_name' => ['required', 'string', 'max:160'],
            'po_employee_id' => ['required', 'integer', 'min:1'],
            'po_site_id' => ['required', 'integer', 'min:1'],
            'po_start_date' => ['required', 'date_format:Y-m-d'],
            'po_end_date' => ['required', 'date_format:Y-m-d', 'after_or_equal:po_start_date'],
            'total_mandays' => ['required', 'integer', 'min:0', 'max:2147483647'],
        ]);
        $employee = DB::table('user')->where('id', $data['po_employee_id'])->where('role', 'employee')->where('is_active', true)->first();
        $site = DB::table('assigned_sites')->where('id', $data['po_site_id'])->where('is_active', true)->first();
        $assigned = DB::table('site_assignment')->where('employee_id', $data['po_employee_id'])
            ->where('site_id', $data['po_site_id'])->where('is_active', true)->exists();
        if (! $employee || ! $site || ! $assigned) {
            return back()->withInput()->withErrors(['po_site_id' => 'Select an active employee assigned to the selected site.']);
        }
        DB::table('purchase_order')->insert([
            'po_number' => trim($data['po_number']), 'customer_name' => trim($data['po_customer_name']),
            'employee_id' => $data['po_employee_id'], 'site_id' => $data['po_site_id'],
            'start_date' => $data['po_start_date'], 'end_date' => $data['po_end_date'],
            'total_mandays' => $data['total_mandays'], 'used_mandays' => 0,
            'invoice_generated' => false, 'money_received' => false, 'completion_email_sent' => false,
            'created_at' => WorkTrack::now(),
        ]);
        WorkTrack::audit('purchase_order_created', trim($data['po_number']).': '.$data['total_mandays'].' man-days');

        return redirect()->route('billing.index')->with('success', 'PO '.trim($data['po_number']).' saved.');
    }

    private function createInvoice(Request $request)
    {
        $request->merge([
            'customer_name' => trim((string) $request->input('customer_name', '')),
            'po_number' => trim((string) $request->input('po_number', '')),
            'description' => trim((string) $request->input('description', '')),
        ]);
        $data = $request->validate([
            'site_id' => ['required', 'integer', 'min:1'],
            'employee_id' => ['required', 'integer', 'min:1'],
            'po_number' => ['nullable', 'string', 'max:120'],
            'po_date' => ['nullable', 'date_format:Y-m-d'],
            'start_date' => ['required', 'date_format:Y-m-d'],
            'end_date' => ['required', 'date_format:Y-m-d', 'after_or_equal:start_date'],
            'billable_days' => ['required', 'integer', 'min:1'],
            'daily_rate' => ['required', 'numeric', 'gt:0', 'max:9999999999.99'],
            'customer_name' => ['required', 'string', 'max:160'],
            'description' => ['required', 'string'],
            'currency' => ['required', 'in:INR,USD,EUR,GBP,AED'],
        ]);
        $site = DB::table('assigned_sites')->where('id', $data['site_id'])->where('is_active', true)->first();
        $assignment = DB::table('site_assignment')->where('site_id', $data['site_id'])
            ->where('employee_id', $data['employee_id'])->where('is_active', true)->first();
        $employee = DB::table('user')->where('id', $data['employee_id'])->where('role', 'employee')->where('is_active', true)->first();
        if (! $site || ! $assignment || ! $employee) {
            return back()->withInput()->withErrors(['site_id' => 'Select an active engineer and one of their assigned sites.']);
        }
        $dateCount = CarbonImmutable::parse($data['start_date'])->diffInDays(CarbonImmutable::parse($data['end_date'])) + 1;
        if ((int) $data['billable_days'] > $dateCount) {
            return back()->withInput()->withErrors(['billable_days' => 'Billable days must fit the selected date range.']);
        }
        $rate = number_format((float) $data['daily_rate'], 2, '.', '');
        if ((float) $rate <= 0 || strlen(explode('.', $rate)[0]) > 10) {
            return back()->withInput()->withErrors(['daily_rate' => 'Enter a valid daily rate greater than zero.']);
        }
        $total = number_format((float) $rate * (int) $data['billable_days'], 2, '.', '');
        if (strlen(str_replace('.', '', $total)) > 14) {
            return back()->withInput()->withErrors(['daily_rate' => 'The invoice total exceeds the supported amount.']);
        }
        $invoiceNumber = 'GS-'.WorkTrack::now()->format('Ymd').'-'.Str::upper(Str::random(8));
        $id = DB::table('invoice')->insertGetId([
            'invoice_number' => $invoiceNumber, 'employee_id' => $employee->id, 'site_id' => $site->id,
            'customer_name' => trim($data['customer_name']), 'po_number' => trim($data['po_number'] ?? ''),
            'po_date' => $data['po_date'] ?? null, 'description' => trim($data['description']),
            'start_date' => $data['start_date'], 'end_date' => $data['end_date'],
            'billable_days' => $data['billable_days'], 'daily_rate' => $rate, 'total_amount' => $total,
            'currency' => $data['currency'], 'created_at' => WorkTrack::now(), 'created_by_id' => auth()->id(),
        ]);
        if (trim($data['po_number'] ?? '') !== '') {
            DB::table('purchase_order')->where('po_number', trim($data['po_number']))
                ->update(['invoice_generated' => true]);
        }
        WorkTrack::audit('invoice_created', "{$invoiceNumber}: {$data['currency']} {$total}");

        return redirect()->route('billing.index')->with('success', "Invoice {$invoiceNumber} generated.");
    }

    public function updatePurchaseOrderStatus(Request $request, int $id)
    {
        $order = DB::table('purchase_order')->where('id', $id)->firstOrFail();
        $data = $request->validate([
            'invoice_generated' => ['sometimes', 'in:yes,no'],
            'money_received' => ['sometimes', 'in:yes,no'],
        ]);
        $updates = [];
        foreach (['invoice_generated', 'money_received'] as $field) {
            if (array_key_exists($field, $data)) {
                $updates[$field] = $data[$field] === 'yes';
            }
        }
        if ($updates) {
            DB::table('purchase_order')->where('id', $id)->update($updates);
        }
        WorkTrack::audit('purchase_order_status_updated', "PO {$order->po_number}: ".json_encode($updates));

        return redirect()->route('billing.index')->with('success', "PO {$order->po_number} status updated.");
    }

    public function invoicePdf(int $id)
    {
        $invoice = DB::table('invoice as i')->join('user as u', 'u.id', '=', 'i.employee_id')
            ->join('assigned_sites as s', 's.id', '=', 'i.site_id')->where('i.id', $id)
            ->first(['i.*', 'u.name as employee_name', 's.site_name']);
        abort_unless($invoice, 404);
        $esc = fn ($value) => e((string) $value);
        $html = '<html><head><style>@page{margin:50px}body{font-family:DejaVu Sans,sans-serif;color:#17243a;font-size:12px}h1{text-align:center}table{width:100%;border-collapse:collapse;margin:22px 0}td{border:1px solid #d5dce6;padding:10px;vertical-align:top}td:first-child{width:30%;font-weight:bold;background:#eef2f7}.footer{color:#777;font-size:9px}</style></head><body><h1>WORKTRACK</h1><h2>INVOICE</h2><table>';
        $rows = [
            'Invoice Number' => $invoice->invoice_number,
            'Invoice Date' => CarbonImmutable::parse($invoice->created_at, 'UTC')->format('Y-m-d'),
            'Employee' => $invoice->employee_name, 'Customer' => $invoice->customer_name,
            'PO Number' => $invoice->po_number ?: 'Not specified',
            'PO Date' => $invoice->po_date ?: 'Not specified', 'Site' => $invoice->site_name,
            'Service Dates' => $invoice->start_date.' to '.$invoice->end_date,
            'Description' => nl2br($esc($invoice->description)), 'Billable Days' => $invoice->billable_days,
            'Rate per Day' => $invoice->currency.' '.number_format((float) $invoice->daily_rate, 2),
            'Total Due' => $invoice->currency.' '.number_format((float) $invoice->total_amount, 2),
        ];
        foreach ($rows as $label => $value) {
            $html .= '<tr><td>'.$esc($label).'</td><td>'.($label === 'Description' ? $value : $esc($value)).'</td></tr>';
        }
        $html .= '</table><div class="footer">Generated by WorkTrack</div></body></html>';
        $pdf = new Dompdf;
        $pdf->loadHtml($html);
        $pdf->setPaper('letter');
        $pdf->render();

        return response($pdf->output(), 200, [
            'Content-Type' => 'application/pdf',
            'Content-Disposition' => 'attachment; filename="'.$invoice->invoice_number.'.pdf"',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }

    private function usedMandays(object $order): int
    {
        $start = CarbonImmutable::parse($order->start_date, 'Asia/Kolkata')->startOfDay()->utc()->toDateTimeString();
        $end = CarbonImmutable::parse($order->end_date, 'Asia/Kolkata')->addDay()->startOfDay()->utc()->toDateTimeString();
        $checkins = DB::table('attendance')->where('employee_id', $order->employee_id)
            ->where('site_id', $order->site_id)->whereNotNull('checkout_time')
            ->whereIn('status', ['completed', 'report_pending'])
            ->where('checkin_time', '>=', $start)->where('checkin_time', '<', $end)->pluck('checkin_time');

        return $checkins->map(fn ($time) => CarbonImmutable::parse($time, 'UTC')->setTimezone('Asia/Kolkata')->toDateString())
            ->unique()->count();
    }
}
