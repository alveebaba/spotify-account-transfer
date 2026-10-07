param(
    [ValidateRange(1024, 65535)][int]$Port = 8787,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$candidates = @()
if ($env:SPOTIFY_TRANSFER_PYTHON) {
    $candidates += [PSCustomObject]@{ Exe=$env:SPOTIFY_TRANSFER_PYTHON; Prefix=@() }
} else {
    foreach ($name in @('py', 'python', 'python3')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            $prefix = @()
            if ($name -eq 'py') { $prefix = @('-3') }
            $candidates += [PSCustomObject]@{ Exe=$command.Source; Prefix=$prefix }
        }
    }
}
$selected = $null
foreach ($candidate in $candidates) {
    try {
        $probe = @($candidate.Prefix) + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)')
        & $candidate.Exe @probe 2>$null
        if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
    } catch { continue }
}
if (-not $selected) { throw 'Python 3.10+ is required. Install it from python.org, or set SPOTIFY_TRANSFER_PYTHON to its executable path.' }
$script = Join-Path $PSScriptRoot 'server.py'
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) { throw "Port $Port is already in use. Inspect that service, or use -Port with a different port and update the Spotify redirect URI." }
$arguments = @($selected.Prefix) + @('"' + $script + '"', '--port', "$Port")
$process = Start-Process -FilePath $selected.Exe -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'server.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server-error.log')
$deadline = (Get-Date).AddSeconds(10)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $process.Refresh()
    if ($process.HasExited) { throw 'The service did not start. Check server-error.log or run python server.py for diagnostics.' }
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 1
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 200 }
}
if (-not $ready) { throw 'The local service did not become ready. Check server-error.log before retrying.' }
Write-Host "Spotify transfer tool: http://127.0.0.1:$Port"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
