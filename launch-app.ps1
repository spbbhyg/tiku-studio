<#
  tiku-studio desktop launcher
  - starts the scheduled task if the service is down, waits for /api/stats
  - opens a chromeless app window (Edge/Chrome --app, or the Chromium bundled with Playwright)
#>
$ErrorActionPreference = 'SilentlyContinue'
$base = 'http://127.0.0.1:8100/'
$stats = $base + 'api/stats'

function Test-Up {
    try {
        $r = Invoke-WebRequest -UseBasicParsing $stats -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch { return $false }
}

if (-not (Test-Up)) {
    schtasks /Run /TN "tiku-studio" | Out-Null
    for ($i = 0; $i -lt 120; $i++) {
        if (Test-Up) { break }
        Start-Sleep -Milliseconds 500
    }
}

if (-not (Test-Up)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "tiku-studio service did not start. Try start.bat to see the error.",
        "tiku-studio") | Out-Null
    exit 1
}

$candidates = @(
    "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
# Playwright 自带的 Chromium（这台机器上目前只有它）
$pw = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'ms-playwright') -Filter 'chromium-*' -Directory |
      Sort-Object Name -Descending | Select-Object -First 1
if ($pw) {
    $candidates += (Join-Path $pw.FullName 'chrome-win64\chrome.exe')
    $candidates += (Join-Path $pw.FullName 'chrome-win\chrome.exe')
}

$exe = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

$profileDir = Join-Path $env:LOCALAPPDATA 'tiku-studio\app'
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

if ($exe) {
    Start-Process -FilePath $exe -ArgumentList @(
        "--app=$base",
        "--window-size=1500,950",
        "--user-data-dir=$profileDir",
        "--no-first-run",
        "--no-default-browser-check"
    )
} else {
    Start-Process $base
}
