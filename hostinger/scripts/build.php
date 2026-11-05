<?php

declare(strict_types=1);

$root = dirname(__DIR__);
$composer = $argv[1] ?? '';
if (! is_file($composer)) {
    fwrite(STDERR, "Usage: php scripts/build.php /absolute/path/to/composer.phar\n");
    exit(1);
}
$dist = $root.DIRECTORY_SEPARATOR.'dist';
if (! is_dir($dist) && ! mkdir($dist, 0700, true)) {
    throw new RuntimeException('Cannot create dist directory.');
}
$stage = $dist.DIRECTORY_SEPARATOR.'release-'.bin2hex(random_bytes(6));
$app = $stage.DIRECTORY_SEPARATOR.'worktrack';
$public = $stage.DIRECTORY_SEPARATOR.'public_html'.DIRECTORY_SEPARATOR.'worktrack';
mkdir($app, 0700, true);
mkdir($public, 0755, true);

function copyTree(string $source, string $destination): void
{
    if (is_dir($source)) {
        if (! is_dir($destination) && ! mkdir($destination, 0755, true)) {
            throw new RuntimeException('Cannot create '.$destination);
        }
        foreach (new DirectoryIterator($source) as $entry) {
            if ($entry->isDot()) {
                continue;
            }
            if ($entry->isLink()) {
                throw new RuntimeException('Symbolic links are not packaged: '.$entry->getPathname());
            }
            copyTree($entry->getPathname(), $destination.DIRECTORY_SEPARATOR.$entry->getFilename());
        }
    } elseif (! copy($source, $destination)) {
        throw new RuntimeException('Cannot copy '.$source);
    }
}

function removeStage(string $stage): void
{
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($stage, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
    $entries = [];
    foreach ($iterator as $entry) {
        $entries[] = [$entry->getPathname(), $entry->isDir()];
    }
    foreach ($entries as [$path, $directory]) {
        if (! ($directory ? rmdir($path) : unlink($path))) {
            throw new RuntimeException('Cannot clean staging path '.$path);
        }
    }
    if (! rmdir($stage)) {
        throw new RuntimeException('Cannot remove staging directory '.$stage);
    }
}

try {
    foreach (['app', 'config', 'routes', 'resources', 'database/migrations', 'database/schema', 'bin'] as $directory) {
        copyTree($root.DIRECTORY_SEPARATOR.str_replace('/', DIRECTORY_SEPARATOR, $directory), $app.DIRECTORY_SEPARATOR.str_replace('/', DIRECTORY_SEPARATOR, $directory));
    }
    foreach (['artisan', 'composer.json', 'composer.lock', '.env.example', 'README.md'] as $filename) {
        copyTree($root.DIRECTORY_SEPARATOR.$filename, $app.DIRECTORY_SEPARATOR.$filename);
    }
    mkdir($app.DIRECTORY_SEPARATOR.'bootstrap'.DIRECTORY_SEPARATOR.'cache', 0755, true);
    foreach (['app.php', 'providers.php'] as $filename) {
        copyTree($root.DIRECTORY_SEPARATOR.'bootstrap'.DIRECTORY_SEPARATOR.$filename, $app.DIRECTORY_SEPARATOR.'bootstrap'.DIRECTORY_SEPARATOR.$filename);
    }
    foreach (['app/private/uploads', 'app/private/downloads', 'framework/cache/data', 'framework/sessions', 'framework/views', 'logs'] as $directory) {
        $path = $app.DIRECTORY_SEPARATOR.'storage'.DIRECTORY_SEPARATOR.str_replace('/', DIRECTORY_SEPARATOR, $directory);
        mkdir($path, 0755, true);
        file_put_contents($path.DIRECTORY_SEPARATOR.'.gitignore', "*\n!.gitignore\n");
    }
    copyTree($root.DIRECTORY_SEPARATOR.'public', $public);
    $index = file_get_contents($public.DIRECTORY_SEPARATOR.'index.php');
    $index = str_replace("__DIR__.'/../", "dirname(__DIR__, 2).'/worktrack/", $index);
    file_put_contents($public.DIRECTORY_SEPARATOR.'index.php', $index);
    $process = proc_open([PHP_BINARY, realpath($composer), 'install', '--no-dev', '--prefer-dist', '--no-interaction', '--optimize-autoloader', '--working-dir='.$app], [STDIN, STDOUT, STDERR], $pipes);
    if (! is_resource($process) || proc_close($process) !== 0) {
        throw new RuntimeException('Production dependency installation failed.');
    }
    foreach (glob($app.DIRECTORY_SEPARATOR.'bootstrap'.DIRECTORY_SEPARATOR.'cache'.DIRECTORY_SEPARATOR.'*.php') ?: [] as $cache) {
        unlink($cache);
    }
    file_put_contents($app.DIRECTORY_SEPARATOR.'bootstrap'.DIRECTORY_SEPARATOR.'cache'.DIRECTORY_SEPARATOR.'.gitignore', "*\n!.gitignore\n");
    copyTree($root.DIRECTORY_SEPARATOR.'README.md', $stage.DIRECTORY_SEPARATOR.'UPLOAD-INSTRUCTIONS.md');
    $archive = $dist.DIRECTORY_SEPARATOR.'worktrack-hostinger.zip';
    $zip = new ZipArchive;
    if ($zip->open($archive, ZipArchive::CREATE | ZipArchive::OVERWRITE) !== true) {
        throw new RuntimeException('Cannot create release ZIP.');
    }
    $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($stage, FilesystemIterator::SKIP_DOTS));
    $count = 0;
    foreach ($iterator as $entry) {
        $path = $entry->getPathname();
        $relative = str_replace(DIRECTORY_SEPARATOR, '/', substr($path, strlen($stage) + 1));
        if (basename($path) === '.env' || preg_match('/\.(sqlite|db|pem|jks|keystore)$/i', $relative)) {
            throw new RuntimeException('Private material detected in release: '.$relative);
        }
        if (! $zip->addFile($path, $relative)) {
            throw new RuntimeException('Could not archive '.$relative);
        }
        $count++;
    }
    if (! $zip->close()) {
        throw new RuntimeException('Release ZIP could not be finalized.');
    }
    $manifest = ['archive' => basename($archive), 'sha256' => hash_file('sha256', $archive), 'files' => $count, 'php' => '8.3+', 'contains_database_rows' => false, 'contains_private_uploads' => false];
    file_put_contents($dist.DIRECTORY_SEPARATOR.'manifest.json', json_encode($manifest, JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR)."\n");
    echo "Created {$archive}\nSHA256: {$manifest['sha256']}\n{$count} files; no credentials, database rows or private uploads.\n";
} finally {
    removeStage($stage);
}
