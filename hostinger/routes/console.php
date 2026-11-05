<?php

use App\Models\User;
use App\Services\AttendanceService;
use App\Services\WorkTrack;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Schedule;
use Illuminate\Support\Facades\Schema;

Artisan::command('worktrack:setup', function () {
    if (! is_file(base_path('.env'))) {
        $this->error('Create private worktrack/.env from .env.example with real MySQL and SMTP values first.');

        return 1;
    }
    if (config('database.default') !== 'mysql' || config('app.debug')) {
        $this->error('Setup requires DB_CONNECTION=mysql and APP_DEBUG=false. No database changes made.');

        return 1;
    }
    if (! config('app.key') && $this->call('key:generate', ['--force' => true]) !== 0) {
        return 1;
    }
    if ($this->call('migrate', ['--force' => true]) !== 0) {
        return 1;
    }

    return $this->call('worktrack:database-check', ['--files' => true]);
});

Artisan::command('worktrack:close-overdue-visits', function (AttendanceService $service) {
    $this->info('Automatically stopped '.$service->closeOverdue().' visit(s).');
});

Schedule::command('worktrack:close-overdue-visits')->everyMinute()->withoutOverlapping(5);

Artisan::command('worktrack:reset-password {email}', function () {
    $user = User::where('email', strtolower($this->argument('email')))->firstOrFail();
    $password = $this->secret('New password (at least 10 characters)');
    if (! is_string($password) || strlen($password) < 10) {
        $this->error('Password must contain at least 10 characters.');

        return 1;
    }
    DB::transaction(function () use ($user, $password) {
        $locked = User::whereKey($user->id)->lockForUpdate()->firstOrFail();
        $locked->password_hash = Hash::make($password);
        $locked->auth_version++;
        $locked->save();
    });
    $this->info('Password reset. Existing sessions will be revoked.');

    return 0;
});

Artisan::command('worktrack:create-admin', function () {
    if (User::where('role', 'admin')->exists()) {
        $this->error('An administrator already exists. Use worktrack:reset-password instead.');

        return 1;
    }
    $values = ['name' => $this->ask('Name'), 'email' => strtolower((string) $this->ask('Email')), 'mobile' => $this->ask('Mobile'), 'password' => $this->secret('Password')];
    validator($values, ['name' => 'required|string|max:120', 'email' => 'required|email|max:255|unique:user,email', 'mobile' => 'required|string|max:40', 'password' => 'required|string|min:10|max:255'])->validate();
    DB::transaction(function () use ($values) {
        User::create(['name' => $values['name'], 'email' => $values['email'], 'mobile' => $values['mobile'], 'password_hash' => Hash::make($values['password']), 'role' => 'admin', 'designation' => 'Administrator', 'created_date' => WorkTrack::now(), 'is_active' => true, 'auth_version' => 0]);
        if (! DB::table('attendance_policy')->where('id', 1)->exists()) {
            DB::table('attendance_policy')->insert(['id' => 1, 'effective_from' => WorkTrack::today()->toDateString()]);
        }
    });
    $this->info('Administrator created.');

    return 0;
});

Artisan::command('worktrack:database-check {--files : Verify uploaded files referenced by the database}', function () {
    $definitions = json_decode(file_get_contents(database_path('schema/worktrack.json')), true, 512, JSON_THROW_ON_ERROR);
    $failures = [];
    foreach ($definitions as $table) {
        if (! Schema::hasTable($table['name'])) {
            $failures[] = 'Missing table '.$table['name'];

            continue;
        }
        foreach ($table['columns'] as $column) {
            if (! Schema::hasColumn($table['name'], $column['name'])) {
                $failures[] = "Missing column {$table['name']}.{$column['name']}";
            }
        }
        $this->line($table['name'].': '.DB::table($table['name'])->count().' row(s)');
        foreach ($table['foreign'] as $foreign) {
            if (! Schema::hasTable($foreign['table']) || ! Schema::hasColumn($table['name'], $foreign['column'])) {
                continue;
            }
            $orphans = DB::table($table['name'].' as child')
                ->whereNotNull('child.'.$foreign['column'])
                ->whereNotExists(fn ($q) => $q->selectRaw('1')->from($foreign['table'].' as parent')
                    ->whereColumn('parent.'.$foreign['target'], 'child.'.$foreign['column']))->count();
            if ($orphans) {
                $failures[] = "{$table['name']}.{$foreign['column']}: {$orphans} orphan row(s)";
            }
        }
    }
    if ($this->option('files') && ! $failures) {
        foreach (['attendance' => ['checkin_selfie'], 'site_report' => ['employee_signature', 'customer_signature'], 'submitted_report_version' => ['filename'], 'candidate_referral' => ['resume_filename'], 'app_settings' => ['brand_logo_filename']] as $table => $columns) {
            foreach ($columns as $column) {
                foreach (DB::table($table)->whereNotNull($column)->where($column, '<>', '')->pluck($column) as $filename) {
                    if (! preg_match('/\A[a-zA-Z0-9_.-]+\z/D', $filename) || in_array($filename, ['.', '..'], true) || ! is_file(WorkTrack::uploadPath($filename))) {
                        $failures[] = "{$table}.{$column}: missing or invalid private file";
                    }
                }
            }
        }
    }
    foreach ($failures as $failure) {
        $this->error($failure);
    }
    $legacy = Schema::hasTable('user') ? DB::table('user')->where('password_hash', 'like', 'scrypt:%')->count() : 0;
    if ($legacy) {
        $this->warn("{$legacy} account(s) require password recovery or an administrator password reset before PHP login.");
    }
    if ($failures) {
        return 1;
    }
    $this->info('Schema, row relationships'.($this->option('files') ? ' and referenced private files' : '').' verified. Compare these counts with your final EC2 backup.');

    return 0;
});
