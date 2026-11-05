<?php

namespace App\Services;

use Carbon\CarbonImmutable;
use Illuminate\Support\Collection;
use PhpOffice\PhpSpreadsheet\Cell\DataType;
use PhpOffice\PhpSpreadsheet\IOFactory;
use PhpOffice\PhpSpreadsheet\Shared\Date as ExcelDate;
use PhpOffice\PhpSpreadsheet\Writer\Xlsx;
use Symfony\Component\HttpFoundation\StreamedResponse;

class ActivityExport
{
    public function download(object $site, Collection $visits, Collection $leaves, CarbonImmutable $start, CarbonImmutable $end, object $employee, array $automaticLeave = []): StreamedResponse
    {
        $template = resource_path('templates/activity_report.xlsx');
        abort_unless(is_file($template), 500, 'The activity report template is unavailable.');
        $spreadsheet = IOFactory::load($template);
        $sheet = $spreadsheet->getSheetByName('SAR-R0');
        abort_unless($sheet && $spreadsheet->getIndex($sheet) === 0, 500, 'The activity report template is invalid.');
        $remarksRow = null;
        foreach ($sheet->getColumnIterator('A', 'A') as $column) {
            foreach ($column->getCellIterator() as $cell) {
                if ($cell->getValue() === 'REMARKS') {
                    $remarksRow = $cell->getRow();
                    break 2;
                }
            }
        }
        abort_unless($remarksRow && $remarksRow > 9, 500, 'The activity report template is missing its activity rows.');

        $activities = [];
        foreach ($visits as $visit) {
            $date = CarbonImmutable::parse($visit->checkout_time, 'UTC')->setTimezone('Asia/Kolkata')->toDateString();
            $activities[$date][] = $visit;
        }
        $automatic = [];
        foreach ($automaticLeave as $item) {
            $automatic[$item['date']][] = $item;
        }
        $remarks = [];
        for ($offset = 1; $offset <= 5; $offset++) {
            $remarks[$offset - 1] = trim((string) ($site->report_remarks ?? '')) === ''
                ? ''
                : (explode("\n", (string) $site->report_remarks)[$offset - 1] ?? '');
        }

        $rows = [];
        $day = $start;
        while ($day <= $end) {
            $date = $day->toDateString();
            $billableRecorded = false;
            $details = [];
            foreach ($activities[$date] ?? [] as $visit) {
                $detail = (string) $visit->work_performed;
                if ($visit->is_billable === null) {
                    $legacy = array_filter([
                        $visit->activities_completed, $visit->issues_found, $visit->recommendations,
                        $visit->materials_used, $visit->remarks,
                    ], fn ($value) => $value !== null && $value !== '');
                    if ($legacy) {
                        $detail .= "\n".implode("\n", $legacy);
                    }
                    $detail .= "\n[Billing not recorded in legacy report; counted as 0]";
                }
                $billable = $visit->is_billable !== null && (bool) $visit->is_billable && ! $billableRecorded;
                $billableRecorded = $billableRecorded || $billable;
                $details[] = $detail;
            }
            foreach ($leaves as $leave) {
                if ($leave->start_date <= $date && $leave->end_date >= $date) {
                    $period = ['morning' => 'Morning', 'afternoon' => 'Afternoon'][$leave->period] ?? null;
                    $details[] = ($period ? $period.': ' : '').$leave->reason;
                }
            }
            foreach ($automatic[$date] ?? [] as $item) {
                $days = rtrim(rtrim(number_format((float) $item['days'], 1, '.', ''), '0'), '.');
                $details[] = "Automatic leave ({$days} day): no site visit or approved work confirmation.";
            }
            if (! isset($activities[$date]) && ! $this->hasLeave($leaves, $date) && empty($automatic[$date])) {
                $details[] = $day->isSunday() ? 'Sunday' : 'No activity recorded';
            }
            $rows[] = [$day, $billableRecorded ? 1 : 0, implode("\n", $details)];
            $day = $day->addDay();
        }

        $existingActivityRows = $remarksRow - 9;
        $extra = max(0, count($rows) - $existingActivityRows);
        if ($extra > 0) {
            $sheet->insertNewRowBefore($remarksRow, $extra);
            for ($offset = 0; $offset < $extra; $offset++) {
                $row = $remarksRow + $offset;
                $sourceRow = $remarksRow - 1;
                $sheet->getRowDimension($row)->setRowHeight($sheet->getRowDimension($sourceRow)->getRowHeight());
                for ($column = 1; $column <= 12; $column++) {
                    $from = $sheet->getCell([$column, $sourceRow]);
                    $to = $sheet->getCell([$column, $row]);
                    if ($from->getStyle()) {
                        $sheet->duplicateStyle($from->getStyle(), $to->getCoordinate());
                    }
                }
                $sheet->mergeCells("E{$row}:L{$row}");
            }
        }

        $this->label($sheet, 'A3', (string) ($site->project_name ?: 'Not specified'));
        $this->label($sheet, 'A4', (string) $site->customer_name);
        $this->label($sheet, 'A5', WorkTrack::today()->format('d M Y'));
        $this->label($sheet, 'A6', (string) $site->site_name);
        $this->label($sheet, 'A7', (string) $employee->name);
        foreach ($rows as $offset => [$date, $billable, $detail]) {
            $row = 9 + $offset;
            $sheet->setCellValue("A{$row}", $offset + 1);
            $this->string($sheet, "B{$row}", (string) $site->site_name);
            $sheet->setCellValue("C{$row}", ExcelDate::PHPToExcel($date->toDateTime()));
            $sheet->setCellValue("D{$row}", $billable);
            $this->string($sheet, "E{$row}", $detail);
            $lineCount = 0;
            foreach (explode("\n", $detail) as $line) {
                $lineCount += max(1, (int) ceil(mb_strlen($line) / 80));
            }
            $sheet->getRowDimension($row)->setRowHeight(min(409, max(15, $lineCount * 15 + 8)));
        }

        $remarksRow += $extra;
        for ($offset = 0; $offset < 5; $offset++) {
            $this->string($sheet, 'C'.($remarksRow + 1 + $offset), $remarks[$offset]);
        }
        $sheet->setCellValue('K'.($remarksRow + 6), array_sum(array_column($rows, 1)));
        $this->label($sheet, 'A'.($remarksRow + 7), (string) $site->customer_name);
        $this->string($sheet, 'I'.($remarksRow + 7), 'M/S Blackpurple automation');
        $this->label($sheet, 'A'.($remarksRow + 8), (string) ($site->commissioning_manager ?? ''));
        $this->label($sheet, 'E'.($remarksRow + 8), (string) ($site->project_manager ?? ''));
        $this->label($sheet, 'I'.($remarksRow + 8), (string) $employee->name);

        $filename = "site-activity-{$site->id}-{$start->toDateString()}-{$end->toDateString()}.xlsx";

        return response()->streamDownload(function () use ($spreadsheet) {
            (new Xlsx($spreadsheet))->save('php://output');
            $spreadsheet->disconnectWorksheets();
        }, $filename, [
            'Content-Type' => 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'X-Content-Type-Options' => 'nosniff',
        ]);
    }

    private function hasLeave(Collection $leaves, string $date): bool
    {
        foreach ($leaves as $leave) {
            if ($leave->start_date <= $date && $leave->end_date >= $date) {
                return true;
            }
        }

        return false;
    }

    private function label($sheet, string $address, string $value): void
    {
        $label = (string) $sheet->getCell($address)->getValue();
        abort_unless(str_contains($label, ':'), 500, "Activity template is missing a label at {$address}.");
        [$prefix, $suffix] = explode(':', $label, 2);
        preg_match('/\s*$/u', $suffix, $match);
        $this->string($sheet, $address, $prefix.': '.$value.($match[0] ?? ' '));
    }

    private function string($sheet, string $address, string $value): void
    {
        $sheet->setCellValueExplicit($address, mb_substr($value, 0, 32767), DataType::TYPE_STRING);
    }
}
