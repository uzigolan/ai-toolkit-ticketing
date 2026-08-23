<#
.SYNOPSIS
    Start the rad-agent-toolkit ticketing app. Press Ctrl+C to stop it.

.DESCRIPTION
    Creates the virtual environment on first run (using the py launcher, since
    a bare `python` on PATH is often just the Microsoft Store stub), installs
    requirements when they change, then runs the Flask app in the foreground so
    Ctrl+C stops it cleanly.

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Port 8080
    .\start.ps1 -SkipInstall
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$SkipInstall,
    [switch]$Production
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

# --- Virtual environment -------------------------------------------------
if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating virtual environment (.venv)...' -ForegroundColor Cyan
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3 -m venv .venv
    }
    else {
        # No py launcher: fall back to python, but reject the Store alias stub.
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python -or $python.Source -like '*\WindowsApps\python.exe') {
            throw 'No usable Python found. Install Python 3.10+ from python.org, then re-run this script.'
        }
        & python -m venv .venv
    }
    if (-not (Test-Path $venvPython)) { throw 'Failed to create the virtual environment.' }
}

# --- Dependencies --------------------------------------------------------
# Reinstall only when requirements.txt is newer than the last successful install.
$stamp = Join-Path $PSScriptRoot '.venv\.requirements.stamp'
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
$needsInstall = -not (Test-Path $stamp) -or
                ((Get-Item $requirements).LastWriteTimeUtc -gt (Get-Item $stamp).LastWriteTimeUtc)

if (-not $SkipInstall -and $needsInstall) {
    Write-Host 'Installing dependencies...' -ForegroundColor Cyan
    & $venvPython -m pip install --disable-pip-version-check -q -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
    Set-Content -Path $stamp -Value (Get-Date -Format o)
}

# --- Session signing key -------------------------------------------------
# Persisted to .secret_key so sessions survive a restart. Keep it out of git.
if (-not $env:TICKETING_SECRET_KEY) {
    $keyFile = Join-Path $PSScriptRoot '.secret_key'
    if (-not (Test-Path $keyFile)) {
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        Set-Content -Path $keyFile -Value ([Convert]::ToBase64String($bytes)) -NoNewline
        Write-Host "Generated a new session key in .secret_key" -ForegroundColor Yellow
    }
    $env:TICKETING_SECRET_KEY = (Get-Content -Path $keyFile -Raw).Trim()
}

# --- Run -----------------------------------------------------------------
# An explicit -Port wins; otherwise app.py takes the port (and TLS) from
# config.ini, defaulting to 5000.
if ($Port -gt 0) { $env:FLASK_RUN_PORT = $Port } else { Remove-Item Env:FLASK_RUN_PORT -ErrorAction SilentlyContinue }

$configPath = Join-Path $PSScriptRoot 'config.ini'
$httpsOn = $false
if (Test-Path $configPath) {
    $configText = Get-Content $configPath -Raw
    if ($configText -match '(?ms)^\[HTTPS\](.*?)(^\[|\z)') {
        $httpsOn = $Matches[1] -match '(?m)^\s*enabled\s*=\s*(true|yes|1|on)\s*$'
    }
}

Write-Host ''
Write-Host "$(if ($httpsOn) { 'Ticketing app starting over HTTPS' } else { 'Ticketing app starting' }) - the URL is printed below." -ForegroundColor Green
if (Test-Path $configPath) {
    Write-Host 'config.ini found - LDAP login available if [LDAP] enabled = true.' -ForegroundColor DarkGray
}
else {
    Write-Host 'No config.ini - local accounts only, plain HTTP on port 5000.' -ForegroundColor DarkGray
}
Write-Host 'Press Ctrl+C to stop.' -ForegroundColor DarkGray
Write-Host ''

try {
    if ($Production) {
        & $venvPython -m pip install --disable-pip-version-check -q waitress
        if ($httpsOn) {
            Write-Host 'waitress does not terminate TLS; run app.py directly or put a proxy in front.' -ForegroundColor Yellow
        }
        if ($Port -gt 0) { $env:FLASK_RUN_PORT = "$Port" }
        & $venvPython serve.py
    }
    else {
        & $venvPython app.py
    }
}
finally {
    Write-Host ''
    Write-Host 'Ticketing app stopped.' -ForegroundColor Cyan
}
