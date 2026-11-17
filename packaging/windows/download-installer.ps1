param(
    [Parameter(Mandatory=$true)][string]$DownloadUrl,
    [Parameter(Mandatory=$true)][string]$ExpectedSHA256,
    [Parameter(Mandatory=$true)][long]$ExpectedBytes,
    [Parameter(Mandatory=$true)][string]$Destination,
    [switch]$AllowLoopback
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$logPath = Join-Path $env:TEMP 'CaptionFlow-online-download.log'
function Write-Message([string]$Message) {
    Write-Output $Message
    Add-Content -LiteralPath $logPath -Value $Message -ErrorAction SilentlyContinue
}
Set-Content -LiteralPath $logPath -Value ('Starting download: ' + $DownloadUrl) -ErrorAction SilentlyContinue
$partial = $Destination + '.partial'
$client = $null; $response = $null; $inputStream = $null; $outputStream = $null; $deadline = $null
try {
    $uri = [Uri]$DownloadUrl
    $loopback = $AllowLoopback -and $uri.Scheme -eq 'http' -and $uri.Host -eq '127.0.0.1'
    if (-not $uri.IsAbsoluteUri -or ($uri.Scheme -ne 'https' -and -not $loopback)) {
        throw 'The installer download requires HTTPS.'
    }
    if ($ExpectedSHA256 -notmatch '^[0-9a-f]{64}$' -or $ExpectedBytes -lt 1) {
        throw 'Invalid installer verification metadata.'
    }
    if (Test-Path -LiteralPath $Destination) { throw 'Installer destination already exists.' }
    Add-Type -AssemblyName System.Net.Http
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $client = New-Object Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(20)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('CaptionFlow-Installer')
    $deadline = New-Object Threading.CancellationTokenSource
    $deadline.CancelAfter([TimeSpan]::FromMinutes(20))
    Write-Message 'Downloading app components...'
    $response = $client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead, $deadline.Token).GetAwaiter().GetResult()
    $response.EnsureSuccessStatusCode() | Out-Null
    $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $outputStream = [IO.File]::Open($partial, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $buffer = New-Object byte[] (1024 * 1024)
    [long]$received = 0; [long]$lastReport = 0
    while (($count = $inputStream.ReadAsync($buffer, 0, $buffer.Length, $deadline.Token).GetAwaiter().GetResult()) -gt 0) {
        $received += $count
        if ($received -gt $ExpectedBytes) { throw 'Downloaded installer exceeds its expected size.' }
        $outputStream.Write($buffer, 0, $count)
        if ($received - $lastReport -ge 16MB) {
            $lastReport = $received
            Write-Message ('Downloaded {0:N0} of {1:N0} MB' -f ($received / 1MB), ($ExpectedBytes / 1MB))
        }
    }
    $outputStream.Dispose(); $outputStream = $null
    if ($received -ne $ExpectedBytes) { throw 'The installer download is incomplete.' }
    $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSHA256) { throw 'Installer checksum mismatch. Nothing was installed.' }
    [IO.File]::Move($partial, $Destination)
    Write-Message 'App components verified.'
} catch {
    Write-Message ('Download failed: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($outputStream) { $outputStream.Dispose() }
    if ($inputStream) { $inputStream.Dispose() }
    if ($response) { $response.Dispose() }
    if ($client) { $client.Dispose() }
    if ($deadline) { $deadline.Dispose() }
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
}
