@extends('layouts.app')

@section('content')
<div class="container py-4">
    <h1 class="h3 mb-4">Purchase orders &amp; invoices</h1>
    @foreach (['success' => 'success', 'info' => 'info'] as $key => $type)
        @if (session($key))<div class="alert alert-{{ $type }}">{{ session($key) }}</div>@endif
    @endforeach
    @if ($errors->any())<div class="alert alert-danger"><ul class="mb-0">@foreach ($errors->all() as $error)<li>{{ $error }}</li>@endforeach</ul></div>@endif

    <div class="row g-4 mb-4">
        <div class="col-lg-7">
            <div class="card h-100"><div class="card-header">Create invoice</div><div class="card-body">
                <form method="post" action="{{ route('billing.create') }}" class="row g-3">
                    @csrf
                    <div class="col-md-6"><label class="form-label" for="employee_id">Employee / site</label>
                        <select class="form-select" name="site_id" id="employee_id" required><option value="">Select an employee and site</option>
                            @foreach ($assignments as $assignment)<option value="{{ $assignment->site_id }}" data-employee="{{ $assignment->employee_id }}" @selected(old('site_id') == $assignment->site_id && old('employee_id') == $assignment->employee_id)>{{ $assignment->employee_name }} — {{ $assignment->site_name }} (employee {{ $assignment->employee_id }})</option>@endforeach
                        </select>
                    </div>
                    <input type="hidden" name="employee_id" id="invoice_employee_id" value="{{ old('employee_id') }}">
                    <div class="col-md-6"><label class="form-label" for="customer_name">Customer</label><input class="form-control" id="customer_name" name="customer_name" maxlength="160" value="{{ old('customer_name') }}" required></div>
                    <div class="col-md-6"><label class="form-label" for="po_number">PO number (optional)</label><input class="form-control" id="po_number" name="po_number" maxlength="120" value="{{ old('po_number') }}"></div>
                    <div class="col-md-6"><label class="form-label" for="po_date">PO date (optional)</label><input class="form-control" type="date" id="po_date" name="po_date" value="{{ old('po_date') }}"></div>
                    <div class="col-md-6"><label class="form-label" for="start_date">Service start</label><input class="form-control" type="date" id="start_date" name="start_date" value="{{ old('start_date') }}" required></div>
                    <div class="col-md-6"><label class="form-label" for="end_date">Service end</label><input class="form-control" type="date" id="end_date" name="end_date" value="{{ old('end_date') }}" required></div>
                    <div class="col-md-4"><label class="form-label" for="billable_days">Billable days</label><input class="form-control" type="number" min="1" id="billable_days" name="billable_days" value="{{ old('billable_days') }}" required></div>
                    <div class="col-md-4"><label class="form-label" for="daily_rate">Daily rate</label><input class="form-control" type="number" step="0.01" min="0.01" id="daily_rate" name="daily_rate" value="{{ old('daily_rate') }}" required></div>
                    <div class="col-md-4"><label class="form-label" for="currency">Currency</label><select class="form-select" id="currency" name="currency" required>
                        @foreach (['INR', 'USD', 'EUR', 'GBP', 'AED'] as $currency)<option value="{{ $currency }}" @selected(old('currency', 'INR') === $currency)>{{ $currency }}</option>@endforeach
                    </select></div>
                    <div class="col-12"><label class="form-label" for="description">Description</label><textarea class="form-control" id="description" name="description" rows="3" required>{{ old('description') }}</textarea></div>
                    <div class="col-12"><button class="btn btn-primary">Generate invoice</button></div>
                </form>
            </div></div>
        </div>
        <div class="col-lg-5">
            <div class="card h-100"><div class="card-header">Create purchase order</div><div class="card-body">
                <form method="post" action="{{ route('billing.create') }}" class="row g-3">
                    @csrf<input type="hidden" name="form_action" value="create_po">
                    <div class="col-md-6"><label class="form-label" for="po_number_new">PO number</label><input class="form-control" id="po_number_new" name="po_number" maxlength="120" value="{{ old('po_number') }}" required></div>
                    <div class="col-md-6"><label class="form-label" for="po_customer_name">Customer</label><input class="form-control" id="po_customer_name" name="po_customer_name" maxlength="160" value="{{ old('po_customer_name') }}" required></div>
                    <div class="col-12"><label class="form-label" for="po_employee_id">Employee / site</label>
                        <select class="form-select" id="po_employee_id" name="po_employee_id" required><option value="">Select an employee and site</option>
                            @foreach ($assignments as $assignment)<option value="{{ $assignment->employee_id }}" data-site="{{ $assignment->site_id }}" @selected(old('po_employee_id') == $assignment->employee_id && old('po_site_id') == $assignment->site_id)>{{ $assignment->employee_name }} — {{ $assignment->site_name }} (site {{ $assignment->site_id }})</option>@endforeach
                        </select>
                    </div>
                    <input type="hidden" name="po_site_id" id="po_site_id" value="{{ old('po_site_id') }}">
                    <div class="col-md-6"><label class="form-label" for="po_start_date">Start date</label><input class="form-control" type="date" id="po_start_date" name="po_start_date" value="{{ old('po_start_date') }}" required></div>
                    <div class="col-md-6"><label class="form-label" for="po_end_date">End date</label><input class="form-control" type="date" id="po_end_date" name="po_end_date" value="{{ old('po_end_date') }}" required></div>
                    <div class="col-12"><label class="form-label" for="total_mandays">Allocated man-days</label><input class="form-control" type="number" min="0" id="total_mandays" name="total_mandays" value="{{ old('total_mandays', 0) }}" required></div>
                    <div class="col-12"><button class="btn btn-outline-primary">Save PO</button></div>
                </form>
            </div></div>
        </div>
    </div>

    <h2 class="h4 mt-4">Purchase orders</h2>
    <div class="table-responsive mb-4"><table class="table table-striped align-middle">
        <thead><tr><th>PO</th><th>Customer</th><th>Employee / site</th><th>Period</th><th>Used / allocated</th><th>Status</th><th>Notification</th><th>Update</th></tr></thead>
        <tbody>@forelse ($orders as $order)
            <tr><td>{{ $order->po_number }}</td><td>{{ $order->customer_name }}</td>
                <td>{{ $order->employee_name }}<br>{{ $order->site_name }}</td>
                <td>{{ $order->start_date }} – {{ $order->end_date }}</td>
                <td>{{ $order->used_mandays }} / {{ $order->total_mandays }}</td>
                <td>{{ $order->invoice_generated ? 'Invoice generated' : 'No invoice' }}<br>{{ $order->money_received ? 'Payment received' : 'Payment pending' }}</td>
                <td>{{ $notifications[$order->id] ?? ($order->completion_email_sent ? 'Sent' : 'Not due / not sent') }}</td>
                <td><form method="post" action="{{ route('billing.purchase-orders.status', $order->id) }}" class="d-flex flex-column gap-1">
                    @csrf
                    <select class="form-select form-select-sm" name="invoice_generated"><option value="yes" @selected($order->invoice_generated)>Invoice generated</option><option value="no" @selected(! $order->invoice_generated)>No invoice</option></select>
                    <select class="form-select form-select-sm" name="money_received"><option value="yes" @selected($order->money_received)>Payment received</option><option value="no" @selected(! $order->money_received)>Payment pending</option></select>
                    <button class="btn btn-sm btn-outline-primary">Update</button>
                </form></td>
            </tr>
        @empty<tr><td colspan="8" class="text-center text-muted py-4">No purchase orders found.</td></tr>@endforelse</tbody>
    </table></div>

    <h2 class="h4">Invoices</h2>
    <div class="table-responsive"><table class="table table-striped align-middle">
        <thead><tr><th>Invoice</th><th>Customer</th><th>Employee</th><th>Site</th><th>Service period</th><th>Amount</th><th></th></tr></thead>
        <tbody>@forelse ($invoices as $invoice)
            <tr><td>{{ $invoice->invoice_number }}<br><small>{{ $invoice->created_at }}</small></td>
                <td>{{ $invoice->customer_name }}</td><td>{{ $invoice->employee_name }}</td><td>{{ $invoice->site_name }}</td>
                <td>{{ $invoice->start_date }} – {{ $invoice->end_date }}<br>{{ $invoice->billable_days }} billable days</td>
                <td>{{ $invoice->currency }} {{ number_format((float) $invoice->total_amount, 2) }}</td>
                <td><a class="btn btn-sm btn-outline-secondary" href="{{ route('billing.invoice.pdf', $invoice->id) }}">PDF</a></td></tr>
        @empty<tr><td colspan="7" class="text-center text-muted py-4">No invoices found.</td></tr>@endforelse</tbody>
    </table></div>
</div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    const invoiceSite = document.getElementById('employee_id');
    const invoiceEmployee = document.getElementById('invoice_employee_id');
    const poChoice = document.getElementById('po_employee_id');
    const poSite = document.getElementById('po_site_id');
    if (invoiceSite) invoiceSite.addEventListener('change', () => invoiceEmployee.value = invoiceSite.selectedOptions[0]?.dataset.employee || '');
    if (poChoice) poChoice.addEventListener('change', () => poSite.value = poChoice.selectedOptions[0]?.dataset.site || '');
});
</script>
@endsection
