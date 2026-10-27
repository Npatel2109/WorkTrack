param(
    [string]$SdkRoot = "$env:LOCALAPPDATA\WorkTrackAndroid\sdk",
    [string]$KeyStore = "",
    [string]$KeyAlias = "androiddebugkey",
    [switch]$PublishToRepository
)
$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$repository = Split-Path $project -Parent
$tools = Join-Path $SdkRoot "build-tools\35.0.0"
$platform = Join-Path $SdkRoot "platforms\android-35\android.jar"
foreach ($required in @($platform, (Join-Path $tools "aapt2.exe"), (Join-Path $tools "d8.bat"), (Join-Path $tools "apksigner.bat"))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing Android SDK component: $required" }
}
function Invoke-BuildTool {
    param([string]$Tool, [string[]]$Arguments)
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Tool failed with exit code $LASTEXITCODE" }
}
$build = Join-Path $project ("build\" + [guid]::NewGuid().ToString("N"))
$classes = Join-Path $build "classes"
New-Item -ItemType Directory -Path $classes -Force | Out-Null
$resources = Join-Path $build "resources.zip"
$unsigned = Join-Path $build "unsigned.apk"
$aligned = Join-Path $build "aligned.apk"
$signed = Join-Path $build "WorkTrack.apk"
Invoke-BuildTool (Join-Path $tools "aapt2.exe") @("compile", "--dir", (Join-Path $project "res"), "-o", $resources)
Invoke-BuildTool (Join-Path $tools "aapt2.exe") @("link", "-o", $unsigned, "-I", $platform, "--manifest", (Join-Path $project "AndroidManifest.xml"), $resources)
$sources = @(Get-ChildItem -LiteralPath (Join-Path $project "src") -Recurse -Filter "*.java" | ForEach-Object { $_.FullName })
Invoke-BuildTool "javac" (@("-encoding", "UTF-8", "--release", "8", "-classpath", $platform, "-d", $classes) + $sources)
$testClasses = Join-Path $build "test-classes"
New-Item -ItemType Directory -Path $testClasses -Force | Out-Null
Invoke-BuildTool "javac" @("--release", "8", "-classpath", $classes, "-d", $testClasses, (Join-Path $project "tests\ServerAddressTest.java"))
Invoke-BuildTool "java" @("-classpath", "$classes;$testClasses", "com.worktrack.mobile.ServerAddressTest")
$classFiles = @(Get-ChildItem -LiteralPath $classes -Recurse -Filter "*.class" | ForEach-Object { $_.FullName })
Invoke-BuildTool (Join-Path $tools "d8.bat") (@("--lib", $platform, "--min-api", "26", "--output", $build) + $classFiles)
Invoke-BuildTool "jar" @("uf", $unsigned, "-C", $build, "classes.dex")
Invoke-BuildTool (Join-Path $tools "zipalign.exe") @("-P", "16", "-f", "4", $unsigned, $aligned)
if (-not $KeyStore) {
    Write-Warning "Building a development-signed APK. Use your private release keystore before production distribution."
    $keyDirectory = Join-Path $env:LOCALAPPDATA "WorkTrackAndroid\keys"
    New-Item -ItemType Directory -Path $keyDirectory -Force | Out-Null
    $KeyStore = Join-Path $keyDirectory "development.keystore"
    if (-not (Test-Path -LiteralPath $KeyStore)) {
        Invoke-BuildTool "keytool" @("-genkeypair", "-keystore", $KeyStore, "-storepass", "android", "-keypass", "android", "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=WorkTrack Development")
    }
    $KeyAlias = "androiddebugkey"
    $passwordArguments = @("--ks-pass", "pass:android", "--key-pass", "pass:android")
} elseif (-not $env:WORKTRACK_STORE_PASSWORD -or -not $env:WORKTRACK_KEY_PASSWORD) {
    throw "Set WORKTRACK_STORE_PASSWORD and WORKTRACK_KEY_PASSWORD for your release keystore."
} else {
    $passwordArguments = @("--ks-pass", "env:WORKTRACK_STORE_PASSWORD", "--key-pass", "env:WORKTRACK_KEY_PASSWORD")
}
Invoke-BuildTool (Join-Path $tools "apksigner.bat") (@("sign", "--ks", $KeyStore, "--ks-key-alias", $KeyAlias) + $passwordArguments + @("--out", $signed, $aligned))
Invoke-BuildTool (Join-Path $tools "apksigner.bat") @("verify", "--verbose", $signed)
Invoke-BuildTool (Join-Path $tools "aapt2.exe") @("dump", "badging", $signed)
$publish = Join-Path $repository "instance\downloads"
New-Item -ItemType Directory -Path $publish -Force | Out-Null
$temporary = Join-Path $publish "worktrack.apk.tmp"
Copy-Item -LiteralPath $signed -Destination $temporary -Force
Move-Item -LiteralPath $temporary -Destination (Join-Path $publish "worktrack.apk") -Force
if ($PublishToRepository) {
    $distribution = Join-Path $project "downloads"
    New-Item -ItemType Directory -Path $distribution -Force | Out-Null
    Copy-Item -LiteralPath $signed -Destination (Join-Path $distribution "worktrack.apk") -Force
    Write-Output "Verified APK also published to $distribution\worktrack.apk"
}
Remove-Item -LiteralPath $build -Recurse -Force
Write-Output "Verified APK published to $publish\worktrack.apk"
