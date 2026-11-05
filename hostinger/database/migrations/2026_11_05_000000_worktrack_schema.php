<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        $tables = json_decode(file_get_contents(database_path('schema/worktrack.json')), true, 512, JSON_THROW_ON_ERROR);
        $existing = array_filter($tables, fn ($table) => Schema::hasTable($table['name']));
        if ($existing) {
            foreach ($tables as $table) {
                if (! Schema::hasTable($table['name'])) {
                    throw new RuntimeException("Incomplete imported database: missing {$table['name']}. Restore a complete backup before proceeding.");
                }
                foreach ($table['columns'] as $column) {
                    if (! Schema::hasColumn($table['name'], $column['name'])) {
                        throw new RuntimeException("Imported database is missing {$table['name']}.{$column['name']}. Upgrade the Flask schema before exporting.");
                    }
                }
            }
        } else {
            foreach ($tables as $definition) {
                Schema::create($definition['name'], function (Blueprint $table) use ($definition) {
                    foreach ($definition['columns'] as $column) {
                        if ($column['autoincrement']) {
                            $field = $table->integer($column['name'], true);
                        } else {
                            $field = match ($column['type']) {
                                'string' => $table->string($column['name'], $column['length']),
                                'decimal' => $table->decimal($column['name'], $column['precision'], $column['scale']),
                                default => $table->{$column['type']}($column['name']),
                            };
                            if ($column['primary']) {
                                $field->primary();
                            }
                            // Flask uses signed INTEGER primary/foreign keys.
                            if ($column['nullable']) {
                                $field->nullable();
                            }
                            if (array_key_exists('default', $column)) {
                                $field->default($column['type'] === 'text'
                                    ? DB::raw("('".str_replace("'", "''", $column['default'])."')")
                                    : $column['default']);
                            } elseif ($column['current'] ?? false) {
                                $field->useCurrent();
                            }
                        }
                    }
                    foreach ($definition['unique'] as $columns) {
                        $table->unique($columns);
                    }
                });
            }
            foreach ($tables as $definition) {
                Schema::table($definition['name'], function (Blueprint $table) use ($definition) {
                    foreach ($definition['foreign'] as $foreign) {
                        $table->foreign($foreign['column'])->references($foreign['target'])->on($foreign['table']);
                    }
                });
            }
        }
        if (! Schema::hasTable('password_reset_tokens')) {
            Schema::create('password_reset_tokens', function (Blueprint $table) {
                $table->string('email')->primary();
                $table->string('token');
                $table->timestamp('created_at')->nullable();
            });
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Automatic rollback is disabled to protect imported WorkTrack data. Restore a verified backup instead.');
    }
};
