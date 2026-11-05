<?php

declare(strict_types=1);
use Symfony\Component\Console\Input\ArgvInput;

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

$root = dirname(__DIR__);
chdir($root);
require $root.'/vendor/autoload.php';
$app = require $root.'/bootstrap/app.php';
exit($app->handleCommand(new ArgvInput(['artisan', 'worktrack:setup'])));
