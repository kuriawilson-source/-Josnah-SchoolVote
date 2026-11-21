$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    if ($pythonLauncher) {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the project virtual environment. Install Python 3 and try again."
    }
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python environment was not created at $python."
}

& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. Check the internet connection and requirements.txt."
}

& $python manage.py migrate
if ($LASTEXITCODE -ne 0) {
    throw "Database migration failed. The server was not started."
}

$hosts = @("localhost", "127.0.0.1", [Net.Dns]::GetHostName())
$addresses = @(
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -ne "127.0.0.1" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.AddressState -eq "Preferred"
        } |
        Select-Object -ExpandProperty IPAddress -Unique
)
$hosts += $addresses
$env:DJANGO_ALLOWED_HOSTS = ($hosts | Select-Object -Unique) -join ","

Write-Host ""
Write-Host "SchoolVote is starting on this computer."
Write-Host "On this computer: http://127.0.0.1:8000/"
Write-Host "On the school network:"
foreach ($address in $addresses) {
    Write-Host "  http://${address}:8000/"
}
Write-Host "Allow Python through Windows Firewall on a Private network if prompted."
Write-Host "Keep this window open while staff are using SchoolVote. Press Ctrl+C to stop."
Write-Host ""

& $python manage.py runserver 0.0.0.0:8000
if ($LASTEXITCODE -ne 0) {
    throw "SchoolVote stopped with an error (exit code $LASTEXITCODE)."
}
