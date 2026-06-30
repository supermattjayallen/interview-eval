$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Virtual environment not found. Run scripts\install-vps.ps1 first."
}

$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Error ".env not found. Copy .env.example to .env and set OPENAI_API_KEY."
}

$envContent = Get-Content $EnvFile -Raw
if ($envContent -notmatch 'OPENAI_API_KEY=sk-[A-Za-z0-9_-]{10,}') {
    Write-Warning "OPENAI_API_KEY may not be set in .env. Analysis will fail until you add it."
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "tmp") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data\results") | Out-Null

$FfmpegBin = Join-Path $Root "tools\ffmpeg\bin"
if (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe")) {
    $env:PATH = "$FfmpegBin;$env:PATH"
}

Write-Host "Starting Interview Evaluation Service on http://0.0.0.0:8002"
& $Python -m uvicorn app.main:app --host 0.0.0.0 --port 8002
