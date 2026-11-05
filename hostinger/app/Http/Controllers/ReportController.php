<?php

namespace App\Http\Controllers;

use App\Services\ActivityExport;
use App\Services\LeavePolicy;
use App\Services\WorkTrack;
use Carbon\CarbonImmutable;
use Dompdf\Dompdf;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use setasign\Fpdi\PdfParser\PdfParser;
use setasign\Fpdi\PdfParser\StreamReader;
use setasign\Fpdi\PdfParser\Type\PdfDictionary;
use setasign\Fpdi\PdfParser\Type\PdfNull;
use setasign\Fpdi\PdfReader\PdfReader;

class ReportController extends Controller
{
    public function index()
    {
        $user = auth()->user();
        $reports = DB::table('attendance as a')
            ->join('site_report as r', 'r.attendance_id', '=', 'a.id')
            ->join('assigned_sites as s', 's.id', '=', 'a.site_id')
            ->join('user as u', 'u.id', '=', 'a.employee_id')
            ->where('a.status', 'completed')
            ->when($user->role !== 'admin', fn ($query) => $query->where('a.employee_id', $user->id))
            ->orderByDesc('a.checkout_time')->orderByDesc('a.id')->get([
                'a.id', 'a.employee_id', 'a.site_id', 'a.checkin_time', 'a.checkout_time',
                'a.checkin_lat', 'a.checkin_lon', 'a.checkout_lat', 'a.checkout_lon',
                'a.checkin_selfie', 'a.checkin_distance_m', 'a.checkout_distance_m',
                'a.checkout_location_time', 'a.checkout_location_source', 'a.auto_closed',
                's.site_name', 's.customer_name', 's.address', 's.latitude', 's.longitude',
                's.allowed_radius', 's.report_company', 's.commissioning_manager', 's.project_manager',
                's.report_remarks', 's.project_name', 'u.name as employee_name',
                'u.email as employee_email', 'u.mobile as employee_mobile',
                'r.work_performed', 'r.is_billable', 'r.activities_completed', 'r.issues_found',
                'r.recommendations', 'r.materials_used', 'r.remarks', 'r.engineer_name',
                'r.customer_representative_name', 'r.employee_signature', 'r.customer_signature',
                'r.report_date',
            ]);

        $sites = DB::table('assigned_sites as s')
            ->when($user->role !== 'admin', fn ($query) => $query
                ->whereExists(fn ($q) => $q->selectRaw('1')->from('site_assignment as sa')
                    ->whereColumn('sa.site_id', 's.id')->where('sa.employee_id', $user->id))
                ->orWhereExists(fn ($q) => $q->selectRaw('1')->from('attendance as a')
                    ->whereColumn('a.site_id', 's.id')->where('a.employee_id', $user->id)))
            ->orderBy('s.site_name')->get(['s.id', 's.site_name']);
        $employees = $user->role === 'admin'
            ? DB::table('user')->where('role', 'employee')->orderBy('name')->get(['id', 'name'])
            : collect();

        return view('reports.index', compact('reports', 'sites', 'employees'));
    }

    public function activity(Request $request)
    {
        $data = $request->validate([
            'start_date' => ['required', 'date_format:Y-m-d'],
            'end_date' => ['required', 'date_format:Y-m-d', 'after_or_equal:start_date'],
            'site_id' => ['required', 'integer', 'min:1'],
            'employee_id' => ['nullable', 'integer', 'min:1'],
        ]);
        $start = CarbonImmutable::parse($data['start_date'], 'Asia/Kolkata')->startOfDay();
        $end = CarbonImmutable::parse($data['end_date'], 'Asia/Kolkata')->startOfDay();
        abort_if($start->diffInDays($end) > 366, 422, 'Choose a date range of 367 days or less.');

        $employeeId = auth()->user()->role === 'admin'
            ? (int) ($data['employee_id'] ?? 0)
            : (int) auth()->id();
        abort_if($employeeId < 1, 422, 'Select an employee for the activity export.');
        $employee = DB::table('user')->where('id', $employeeId)->where('role', 'employee')->first();
        abort_unless($employee, 404);
        $site = $this->siteWithHistoryAccess((int) $data['site_id'], $employeeId);

        $startUtc = $start->utc()->toDateTimeString();
        $endUtc = $end->addDay()->utc()->toDateTimeString();
        $visits = DB::table('attendance as a')
            ->join('site_report as r', 'r.attendance_id', '=', 'a.id')
            ->where('a.site_id', $site->id)->where('a.employee_id', $employeeId)
            ->where('a.status', 'completed')->whereNotNull('a.checkout_time')
            ->where('a.checkout_time', '>=', $startUtc)->where('a.checkout_time', '<', $endUtc)
            ->orderBy('a.checkout_time')->orderBy('a.id')
            ->get(['a.*', 'r.work_performed', 'r.is_billable', 'r.activities_completed', 'r.issues_found',
                'r.recommendations', 'r.materials_used', 'r.remarks']);
        $leaves = DB::table('leave_request')->where('employee_id', $employeeId)
            ->where('status', 'approved')->where('start_date', '<=', $end->toDateString())
            ->where('end_date', '>=', $start->toDateString())->orderBy('id')->get();
        $summary = LeavePolicy::summary($employee, $start, $end);

        $export = new ActivityExport;

        return $export->download($site, $visits, $leaves, $start, $end, $employee, $summary['automatic'] ?? []);
    }

    public function activityAdmin(Request $request)
    {
        return $request->query->count() === 0
            ? redirect('/reports#activity-export')
            : $this->activity($request);
    }

    public function combined(Request $request)
    {
        $data = $request->validate([
            'start_date' => ['required', 'date_format:Y-m-d'],
            'end_date' => ['required', 'date_format:Y-m-d', 'after_or_equal:start_date'],
        ]);
        $start = CarbonImmutable::parse($data['start_date'], 'Asia/Kolkata')->startOfDay();
        $end = CarbonImmutable::parse($data['end_date'], 'Asia/Kolkata')->startOfDay();
        abort_if($start->diffInDays($end) > 3660, 422, 'Choose a date range of ten years or less.');
        $records = $this->reportRows()
            ->where('a.status', 'completed')->whereNotNull('a.checkout_time')
            ->where('a.checkout_time', '>=', $start->utc()->toDateTimeString())
            ->where('a.checkout_time', '<', $end->addDay()->utc()->toDateTimeString())
            ->orderBy('a.checkout_time')->orderBy('a.id')->limit(501)->get();
        abort_if($records->isEmpty(), 404, 'No completed reports were found for that date range.');
        abort_if($records->count() > 500, 422, 'More than 500 reports match; narrow the date range.');

        $html = '<html><head><style>'.$this->reportStyles().'.report-page{page-break-after:always}.report-page:last-child{page-break-after:auto}</style></head><body>';
        foreach ($records as $record) {
            $html .= '<section class="report-page">'.$this->siteReportBody($record).'</section>';
        }
        $html .= '</body></html>';
        $pdf = new Dompdf;
        $pdf->loadHtml($html);
        $pdf->setPaper('letter');
        $pdf->render();

        return response($pdf->output(), 200, [
            'Content-Type' => 'application/pdf',
            'Content-Disposition' => 'attachment; filename="worktrack-reports-'.$start->toDateString().'-to-'.$end->toDateString().'.pdf"',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }

    public function pdf(int $id)
    {
        $record = $this->reportRows()->where('a.id', $id)->where('a.status', 'completed')->firstOrFail();

        return response($this->siteReportPdf($record), 200, [
            'Content-Type' => 'application/pdf',
            'Content-Disposition' => 'attachment; filename="site-report-'.$record->id.'.pdf"',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }

    public function submittedIndex()
    {
        $reports = DB::table('submitted_report as r')
            ->join('user as u', 'u.id', '=', 'r.employee_id')
            ->leftJoin('assigned_sites as s', 's.id', '=', 'r.site_id')
            ->leftJoin('submitted_report_version as v', function ($join) {
                $join->on('v.report_id', '=', 'r.id')->on('v.revision_number', '=', 'r.revision_number');
            })
            ->when(auth()->user()->role !== 'admin', fn ($query) => $query->where('r.employee_id', auth()->id()))
            ->orderByDesc('r.created_at')->orderByDesc('r.id')->paginate(20, [
                'r.id', 'r.employee_id', 'r.site_id', 'r.created_at', 'r.revision_number',
                'u.name as employee_name', 's.site_name', 'v.start_date', 'v.end_date', 'v.total_mandays',
                'v.filename', 'v.original_filename',
            ]);

        return view('reports.submitted-index', compact('reports'));
    }

    public function submittedCreate()
    {
        return $this->submittedForm(null);
    }

    public function submittedEdit(int $id)
    {
        return $this->submittedForm($this->ownedSubmittedReport($id));
    }

    public function submittedStore(Request $request, ?int $id = null)
    {
        $report = $id === null ? null : $this->ownedSubmittedReport($id);
        $current = $report ? DB::table('submitted_report_version')
            ->where('report_id', $report->id)->where('revision_number', $report->revision_number)->firstOrFail() : null;
        $sites = $this->assignedSites();
        $input = $request->validate([
            'site_id' => ['required', 'integer', 'min:1'],
            'start_date' => ['required', 'date_format:Y-m-d'],
            'end_date' => ['required', 'date_format:Y-m-d', 'after_or_equal:start_date'],
            'total_mandays' => ['required', 'integer', 'min:1', 'max:2147483647'],
            'revision_number' => ['nullable', 'integer', 'min:1'],
            'pdf' => [$current ? 'nullable' : 'required', 'file', 'max:5120'],
        ]);
        $site = $sites->firstWhere('id', (int) $input['site_id']);
        if (! $site && $report && (int) $report->site_id === (int) $input['site_id']) {
            $site = DB::table('assigned_sites')->where('id', $report->site_id)->first();
        }
        abort_unless($site, 422, 'Select an active site assigned to you.');
        if ($report && (int) ($input['revision_number'] ?? 0) !== (int) $report->revision_number) {
            return back()->withInput()->withErrors(['revision_number' => 'This report has changed. Reload the page before editing it again.'])->setStatusCode(409);
        }

        $upload = $request->file('pdf');
        $filename = null;
        $original = $current?->original_filename ?? '';
        if ($upload) {
            [$filename, $original] = $this->storeSubmittedPdf($upload);
        }
        if ($current && ! $filename
            && $input['start_date'] === $current->start_date
            && $input['end_date'] === $current->end_date
            && (int) $input['total_mandays'] === (int) $current->total_mandays
            && (int) $site->id === (int) $report->site_id) {
            return redirect()->route('submitted-reports.show', $report->id)->with('info', 'No report changes to save.');
        }

        try {
            $savedId = DB::transaction(function () use ($report, $site, $input, $filename, $original, $current) {
                $now = WorkTrack::now();
                if ($report) {
                    $locked = DB::table('submitted_report')->where('id', $report->id)->lockForUpdate()->first();
                    abort_unless($locked, 404);
                    if ((int) $locked->revision_number !== (int) $input['revision_number']) {
                        abort(409, 'This report was updated elsewhere. Reload the page before editing it again.');
                    }
                    $nextRevision = (int) $locked->revision_number + 1;
                    DB::table('submitted_report')->where('id', $report->id)->update([
                        'site_id' => $site->id, 'revision_number' => $nextRevision,
                    ]);
                    $reportId = (int) $report->id;
                } else {
                    $nextRevision = 1;
                    $reportId = DB::table('submitted_report')->insertGetId([
                        'employee_id' => auth()->id(), 'site_id' => $site->id,
                        'created_at' => $now, 'revision_number' => 1,
                    ]);
                }
                DB::table('submitted_report_version')->insert([
                    'report_id' => $reportId, 'revision_number' => $nextRevision,
                    'start_date' => $input['start_date'], 'end_date' => $input['end_date'],
                    'total_mandays' => $input['total_mandays'],
                    'filename' => $filename ?? $current->filename,
                    'original_filename' => $original, 'pdf_replaced' => $filename !== null,
                    'created_at' => $now,
                ]);
                $action = $current === null ? 'report_submitted' : ($filename ? 'report_pdf_replaced' : 'report_details_updated');
                WorkTrack::audit($action, "Submitted report {$reportId}; version {$nextRevision}; site={$site->site_name}; {$input['start_date']} to {$input['end_date']}; man-days={$input['total_mandays']}; file={$original}");

                return $reportId;
            });
        } catch (\Throwable $exception) {
            if ($filename) {
                @unlink(WorkTrack::uploadPath($filename));
            }
            throw $exception;
        }

        return redirect()->route('submitted-reports.show', $savedId)
            ->with('success', $current ? 'Report updated. Previous versions have been retained.' : 'Report submitted.');
    }

    public function submittedShow(int $id)
    {
        $report = $this->ownedSubmittedReport($id);
        $versions = DB::table('submitted_report_version')->where('report_id', $id)
            ->orderByDesc('revision_number')->get();
        $site = $report->site_id ? DB::table('assigned_sites')->where('id', $report->site_id)->first() : null;
        $employee = DB::table('user')->where('id', $report->employee_id)->first();

        return view('reports.submitted-show', compact('report', 'versions', 'site', 'employee'));
    }

    public function submittedDownload(int $id, int $versionId)
    {
        $this->ownedSubmittedReport($id);
        $version = DB::table('submitted_report_version')->where('id', $versionId)->where('report_id', $id)->firstOrFail();
        $path = WorkTrack::uploadPath($version->filename);
        abort_unless(is_file($path), 404, 'The report PDF is unavailable. Please contact the administrator.');

        return response()->download($path, $version->original_filename, [
            'Content-Type' => 'application/pdf', 'Cache-Control' => 'private, no-store',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }

    private function submittedForm(?object $report)
    {
        $current = $report ? DB::table('submitted_report_version')->where('report_id', $report->id)
            ->where('revision_number', $report->revision_number)->first() : null;
        $sites = $this->assignedSites();
        if ($report && $report->site_id && ! $sites->contains('id', $report->site_id)) {
            $oldSite = DB::table('assigned_sites')->where('id', $report->site_id)->first();
            if ($oldSite) {
                $sites->push($oldSite);
            }
        }
        $values = [
            'site_id' => $report?->site_id ?? '',
            'start_date' => $current?->start_date ?? WorkTrack::today()->toDateString(),
            'end_date' => $current?->end_date ?? WorkTrack::today()->toDateString(),
            'total_mandays' => $current?->total_mandays ?? '',
            'revision_number' => $report?->revision_number ?? '',
        ];

        return view('reports.submitted-form', compact('report', 'current', 'sites', 'values'));
    }

    private function ownedSubmittedReport(int $id): object
    {
        return DB::table('submitted_report')->where('id', $id)
            ->when(auth()->user()->role !== 'admin', fn ($query) => $query->where('employee_id', auth()->id()))
            ->firstOrFail();
    }

    private function assignedSites()
    {
        return DB::table('assigned_sites')->where('is_active', true)
            ->whereExists(fn ($query) => $query->selectRaw('1')->from('site_assignment')
                ->whereColumn('site_assignment.site_id', 'assigned_sites.id')
                ->where('site_assignment.employee_id', auth()->id())->where('site_assignment.is_active', true))
            ->orderBy('site_name')->get();
    }

    private function siteWithHistoryAccess(int $siteId, int $employeeId): object
    {
        return DB::table('assigned_sites')->where('id', $siteId)
            ->where(function ($query) use ($employeeId) {
                $query->whereExists(fn ($q) => $q->selectRaw('1')->from('site_assignment')
                    ->whereColumn('site_assignment.site_id', 'assigned_sites.id')->where('site_assignment.employee_id', $employeeId))
                    ->orWhereExists(fn ($q) => $q->selectRaw('1')->from('attendance')
                        ->whereColumn('attendance.site_id', 'assigned_sites.id')->where('attendance.employee_id', $employeeId));
            })->firstOrFail();
    }

    private function reportRows()
    {
        return DB::table('attendance as a')
            ->join('site_report as r', 'r.attendance_id', '=', 'a.id')
            ->join('assigned_sites as s', 's.id', '=', 'a.site_id')
            ->join('user as u', 'u.id', '=', 'a.employee_id')
            ->select([
                'a.id', 'a.employee_id', 'a.site_id', 'a.checkin_time', 'a.checkout_time',
                'a.checkin_lat', 'a.checkin_lon', 'a.checkout_lat', 'a.checkout_lon',
                'a.checkin_selfie', 'a.checkin_distance_m', 'a.checkout_distance_m',
                'a.checkout_location_time', 'a.checkout_location_source', 'a.auto_closed',
                's.site_name', 's.customer_name', 's.address', 's.latitude', 's.longitude',
                's.allowed_radius', 's.report_company', 's.commissioning_manager', 's.project_manager',
                's.report_remarks', 's.project_name', 'u.name as employee_name',
                'u.email as employee_email', 'u.mobile as employee_mobile',
                'r.work_performed', 'r.is_billable', 'r.activities_completed', 'r.issues_found',
                'r.recommendations', 'r.materials_used', 'r.remarks', 'r.engineer_name',
                'r.customer_representative_name', 'r.employee_signature', 'r.customer_signature',
                'r.report_date',
            ]);
    }

    private function siteReportPdf(object $record): string
    {
        $pdf = new Dompdf;
        $pdf->loadHtml('<html><head><style>'.$this->reportStyles().'</style></head><body>'.$this->siteReportBody($record).'</body></html>');
        $pdf->setPaper('letter');
        $pdf->render();

        return $pdf->output();
    }

    private function reportStyles(): string
    {
        return '@page{margin:38px 42px}body{font-family:DejaVu Sans,sans-serif;color:#17243a;font-size:10px}h1{text-align:center;color:#1d3768}h2{font-size:13px;margin:17px 0 6px;color:#294978}table{width:100%;border-collapse:collapse}th,td{border:1px solid #d5dce6;padding:6px;text-align:left;vertical-align:top}th{width:29%;background:#eef2f7}.signatures td{width:50%;height:72px}.signature-image{max-width:230px;max-height:70px}.signature-line{padding:10px 0;color:#555}.footer{margin-top:24px;color:#777;font-size:8px}';
    }

    private function siteReportBody(object $record): string
    {
        $esc = fn ($value) => e((string) ($value ?? '-'));
        $signature = function (?string $filename): string {
            if (! $filename) {
                return '<div class="signature-line">____________________________<br>Signature / Date</div>';
            }
            $path = WorkTrack::uploadPath(basename($filename));
            if (! is_file($path)) {
                return '<div class="signature-line">____________________________<br>Signature / Date</div>';
            }
            $mime = mime_content_type($path) ?: '';
            if (! in_array($mime, ['image/png', 'image/jpeg'], true)) {
                return '<div class="signature-line">____________________________<br>Signature / Date</div>';
            }

            return '<img class="signature-image" src="data:'.$mime.';base64,'.base64_encode(file_get_contents($path)).'">';
        };
        $time = fn ($value) => $value ? CarbonImmutable::parse($value, 'UTC')->setTimezone('Asia/Kolkata')->format('d M Y H:i:s').' IST' : 'Not recorded';
        $details = $record->is_billable === null
            ? '<tr><th>Work Performed</th><td>'.$esc($record->work_performed).'</td></tr>'
              .'<tr><th>Activities Completed</th><td>'.$esc($record->activities_completed).'</td></tr>'
              .'<tr><th>Issues Found</th><td>'.$esc($record->issues_found).'</td></tr>'
              .'<tr><th>Recommendations</th><td>'.$esc($record->recommendations).'</td></tr>'
              .'<tr><th>Materials Used</th><td>'.$esc($record->materials_used).'</td></tr>'
              .'<tr><th>Remarks</th><td>'.$esc($record->remarks).'</td></tr>'
            : '<tr><th>Activity Detail</th><td>'.$esc($record->work_performed).'</td></tr>'
              .'<tr><th>Billing</th><td>'.($record->is_billable ? 'Billable (1 day)' : 'Non-billable (0 days)').'</td></tr>';
        $html = '<h1>SITE VISIT REPORT</h1>'
            .'<h2>Engineer Details</h2><table><tr><th>Name</th><td>'.$esc($record->employee_name).'</td></tr><tr><th>Email</th><td>'.$esc($record->employee_email).'</td></tr><tr><th>Mobile</th><td>'.$esc($record->employee_mobile).'</td></tr></table>'
            .'<h2>Customer Details</h2><table><tr><th>Customer</th><td>'.$esc($record->customer_name).'</td></tr><tr><th>Site</th><td>'.$esc($record->site_name).'</td></tr><tr><th>Address</th><td>'.$esc($record->address).'</td></tr></table>'
            .'<h2>Check-In / Check-Out</h2><table><tr><th>Check-In</th><td>'.$time($record->checkin_time).'</td></tr><tr><th>Check-In coordinates</th><td>'.$esc($record->checkin_lat).', '.$esc($record->checkin_lon).'</td></tr><tr><th>Check-In distance</th><td>'.($record->checkin_distance_m === null ? 'Not recorded' : number_format($record->checkin_distance_m / 1000, 3).' km').'</td></tr><tr><th>Check-Out</th><td>'.$time($record->checkout_time).'</td></tr><tr><th>Check-Out coordinates</th><td>'.$esc($record->checkout_lat).', '.$esc($record->checkout_lon).'</td></tr><tr><th>Check-Out distance</th><td>'.($record->checkout_distance_m === null ? 'Not recorded' : number_format($record->checkout_distance_m / 1000, 3).' km').'</td></tr><tr><th>Location source</th><td>'.$esc($record->checkout_location_source ?: 'Not recorded').'</td></tr><tr><th>Stop method</th><td>'.($record->auto_closed ? 'Automatic (23:59 India)' : 'Manual').'</td></tr></table>'
            .'<h2>Site Activity</h2><table>'.$details.'<tr><th>Customer Representative</th><td>'.($record->customer_representative_name ? $esc($record->customer_representative_name) : '____________________________').'</td></tr></table>'
            .'<h2>Signatures / Offline Approval</h2><table class="signatures"><tr><th>Engineer Signature</th><td>'.$signature($record->employee_signature).'</td><th>Customer Signature</th><td>'.$signature($record->customer_signature).'</td></tr></table>'
            .'<div class="footer">Generated by WorkTrack - '.$time(WorkTrack::now()).'</div>';

        return $html;
    }

    private function storeSubmittedPdf($upload): array
    {
        $original = basename(str_replace('\\', '/', $upload->getClientOriginalName()));
        $original = Str::limit($original, 255, '');
        abort_unless(Str::lower(pathinfo($original, PATHINFO_EXTENSION)) === 'pdf', 422, 'Choose a PDF file with a filename of at most 255 characters.');
        $bytes = file_get_contents($upload->getRealPath());
        abort_if($bytes === false || $bytes === '', 422, 'The PDF file is empty.');
        abort_if(strlen($bytes) > 5 * 1024 * 1024, 422, 'The PDF file exceeds the 5 MB limit.');
        abort_unless(str_starts_with($bytes, '%PDF-'), 422, 'Upload a valid PDF report, not a renamed file.');
        try {
            $parser = new PdfParser(StreamReader::createByString($bytes, 6 * 1024 * 1024));
            $parser->getPdfVersion();
            $trailer = $parser->getCrossReference()->getTrailer();
            $encrypted = ! (PdfDictionary::get($trailer, 'Encrypt') instanceof PdfNull);
            $pages = (new PdfReader($parser))->getPageCount();
        } catch (\Throwable $exception) {
            abort(422, 'The PDF could not be read. Upload a valid, unencrypted PDF.');
        }
        abort_if($encrypted, 422, 'Password-protected PDF reports are not supported.');
        abort_if($pages < 1, 422, 'The PDF report must contain at least one page.');
        $filename = Str::uuid()->toString().'.pdf';
        if (file_put_contents(WorkTrack::uploadPath($filename), $bytes, LOCK_EX) !== strlen($bytes)) {
            @unlink(WorkTrack::uploadPath($filename));
            abort(500, 'The PDF could not be saved. Please try again.');
        }

        return [$filename, $original];
    }
}
