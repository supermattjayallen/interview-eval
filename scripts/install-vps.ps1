$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== Interview Eval VPS setup ==="

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Error "Python launcher (py) not found. Install Python 3.12+ first."
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    py -m venv .venv
}

Write-Host "Installing dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt

New-Item -ItemType Directory -Force -Path "tmp" | Out-Null
New-Item -ItemType Directory -Force -Path "data\results" | Out-Null
New-Item -ItemType Directory -Force -Path "data\jobs" | Out-Null

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $password = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    (Get-Content ".env") `
        -replace 'TEAM_USERNAME=', 'TEAM_USERNAME=team' `
        -replace 'TEAM_PASSWORD=', "TEAM_PASSWORD=$password" |
        Set-Content ".env"
    Write-Host ""
    Write-Host "Created .env with team login:"
    Write-Host "  Username: team"
    Write-Host "  Password: $password"
    Write-Host ""
    Write-Host "IMPORTANT: Edit .env and set your OPENAI_API_KEY before starting."
} else {
    Write-Host ".env already exists - leaving it unchanged."
}

$ruleName = "Interview Eval (TCP 8002)"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Opening Windows Firewall port 8002..."
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8002 | Out-Null
} else {
    Write-Host "Firewall rule already exists."
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown'
} | Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "Setup complete."
Write-Host "1. Edit .env and set OPENAI_API_KEY"
Write-Host "2. Run: .\scripts\start-server.ps1"
Write-Host "3. Share with teammates: http://$ip`:8002"
