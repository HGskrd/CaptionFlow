param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [string]$OnlineInstaller = 'CaptionFlow-Setup.exe',
    [switch]$SkipModelCheck
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Directory = (Resolve-Path $Directory).Path
$work = Join-Path $env:RUNNER_TEMP 'CaptionFlow installer acceptance'
New-Item -ItemType Directory -Path $work -Force | Out-Null
$install = Join-Path $work 'Installed App'
$data = Join-Path $env:LOCALAPPDATA 'CaptionFlow'
New-Item -ItemType Directory -Path $data -Force | Out-Null
$sentinel = Join-Path $data 'installer-acceptance-preserve.txt'
Set-Content -LiteralPath $sentinel -Value 'Keep user data'
$server = $null
function Run-Installer([string]$Path) {
    $p = Start-Process -FilePath $Path -ArgumentList @('/S', "/D=$install") -PassThru
    if (-not $p.WaitForExit(600000)) { $p.Kill(); throw 'Installer timed out' }
    if ($p.ExitCode -notin @(0,3010)) { throw "Installer failed: $Path ($($p.ExitCode))" }
    if (-not (Test-Path -LiteralPath (Join-Path $install 'Uninstall.exe'))) { throw 'Uninstaller missing' }
}
function Inventory {
    $files = @{}
    Get-ChildItem -LiteralPath $install -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($install.Length + 1)
        $files[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    return $files
}
function Smoke([string]$Name) {
    $env:CAPTIONFLOW_SMOKE_EVIDENCE = Join-Path $work "$Name-smoke.json"
    $p = Start-Process -FilePath (Join-Path $install 'CaptionFlowGPUI.exe') -ArgumentList '--smoke-test' -PassThru
    if (-not $p.WaitForExit(60000)) { $p.Kill(); throw 'GPUI smoke timed out' }
    if ($p.ExitCode -ne 0) { throw 'GPUI launch failed' }
    $e = Get-Content -LiteralPath $env:CAPTIONFLOW_SMOKE_EVIDENCE | ConvertFrom-Json
    if (-not $e.preview -or -not $e.shutdown_received -or $e.original_characters -lt 1) { throw 'Caption preview/shutdown failed' }
    Remove-Item Env:CAPTIONFLOW_SMOKE_EVIDENCE
}
function Uninstall {
    $p = Start-Process -FilePath (Join-Path $install 'Uninstall.exe') -ArgumentList '/S' -PassThru
    if (-not $p.WaitForExit(60000)) { $p.Kill(); throw 'Uninstall timed out' }
    $until = (Get-Date).AddSeconds(30)
    while ((Test-Path -LiteralPath $install) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 200 }
    if (Test-Path -LiteralPath $install) { throw 'Application files remained after uninstall' }
    if (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CaptionFlow') { throw 'Uninstall entry remained' }
    if (-not (Test-Path -LiteralPath $sentinel)) { throw 'Uninstall deleted user data' }
}
$env:PYTHONDONTWRITEBYTECODE = '1'
try {
    # Local transport exercises refusal/cleanup without executing untrusted bytes.
    Set-Content -LiteralPath (Join-Path $Directory 'bad.bin') -Value 'bad bytes' -NoNewline
    $server = Start-Process -FilePath (Get-Command python).Source -ArgumentList @('-u','-m','http.server','8847','--bind','127.0.0.1','--directory',"`"$Directory`"") -PassThru -RedirectStandardOutput (Join-Path $work 'http.log') -RedirectStandardError (Join-Path $work 'http-error.log')
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        try { Invoke-WebRequest 'http://127.0.0.1:8847/bad.bin' -TimeoutSec 2 | Out-Null; $ready=$true; break } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $ready) { throw 'Fixture server failed to start' }
    $badDestination = Join-Path $work 'unverified.exe'
    $helper = Join-Path $PSScriptRoot '../packaging/windows/download-installer.ps1'
    $badSize = (Get-Item -LiteralPath (Join-Path $Directory 'bad.bin')).Length
    & "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -File $helper -DownloadUrl 'http://127.0.0.1:8847/bad.bin' -AllowLoopback -ExpectedSHA256 ('0'*64) -ExpectedBytes $badSize -Destination $badDestination
    if ($LASTEXITCODE -eq 0 -or (Test-Path -LiteralPath $badDestination) -or (Test-Path -LiteralPath ($badDestination+'.partial'))) { throw 'Bad checksum was accepted or left a payload behind' }
    Run-Installer (Join-Path $Directory 'CaptionFlow-Setup-All-in-One.exe')
    $full = Inventory
    Smoke 'all-in-one'
    $python = Join-Path $install 'python/python.exe'
    & $python -B (Join-Path $PSScriptRoot 'check-windows-code-bundle.py') --root $install
    if ($LASTEXITCODE -ne 0) { throw 'Bundled code availability check failed' }
    Uninstall
    Run-Installer (Join-Path $Directory $OnlineInstaller)
    $normal = Inventory
    if ($full.Count -ne $normal.Count) { throw 'Installer file counts differ' }
    foreach ($name in $full.Keys) { if ($normal[$name] -ne $full[$name]) { throw "Installers differ: $name" } }
    Smoke 'normal'
    Copy-Item -LiteralPath (Join-Path $install 'portable/caption_audio.dll') -Destination (Join-Path $PSScriptRoot '../portable/caption_audio.dll') -Force
    & (Join-Path $install 'python/python.exe') -B -m unittest discover -s (Join-Path $PSScriptRoot '../tests_portable') -v
    if ($LASTEXITCODE -ne 0) { throw 'Portable acceptance tests failed' }
    if (-not $SkipModelCheck) {
        & (Join-Path $install 'python/python.exe') -B (Join-Path $PSScriptRoot 'test_small_models.py') --cache (Join-Path $work 'model-cache') --backend cpu --download --output (Join-Path $work 'phonon-offline.json')
        if ($LASTEXITCODE -ne 0) { throw 'Offline Phonon code/model check failed' }
    }
    if (Test-Path -LiteralPath (Join-Path $work 'phonon-offline.json')) { Copy-Item -LiteralPath (Join-Path $work 'phonon-offline.json') -Destination (Join-Path $Directory 'phonon-offline.json') }
    Uninstall
    @{ portable_tests_verified=$true; same_installed_files=$true; installed_file_count=$full.Count; normal_preview=$true; all_in_one_preview=$true; uninstallation_verified=$true; user_data_preserved=$true; tampered_download_rejected=$true; all_in_one_code_verified=$true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Directory 'installer-test-report.json')
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}

exit 0
