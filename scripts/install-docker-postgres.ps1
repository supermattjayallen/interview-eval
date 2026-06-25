#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install Docker on Windows Server 2019 and start PostgreSQL for interview-eval.

.NOTES
  - Requires a reboot after enabling Hyper-V and Containers.
  - On KVM/cloud VMs, nested virtualization may need to be enabled by your provider.
  - Re-run this script after reboot; it resumes from the Docker install step.
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StateFile = Join-Path $ProjectRoot ".docker-install-state"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-FeatureEnabled([string]$Name) {
    $feature = Get-WindowsOptionalFeature -Online -FeatureName $Name -ErrorAction SilentlyContinue
    return $feature -and $feature.State -eq "Enabled"
}

function Ensure-WindowsFeatures {
    $needsReboot = $false

    if (-not (Test-FeatureEnabled "Containers")) {
        Write-Step "Enabling Windows Containers feature"
        $result = Install-WindowsFeature -Name Containers -IncludeManagementTools
        if ($result.RestartNeeded -eq "Yes") { $needsReboot = $true }
    } else {
        Write-Host "Containers feature already enabled."
    }

    if (-not (Test-FeatureEnabled "Microsoft-Hyper-V")) {
        Write-Step "Enabling Hyper-V (required for Linux containers / Postgres image)"
        $result = Install-WindowsFeature -Name Hyper-V -IncludeManagementTools
        if ($result.RestartNeeded -eq "Yes") { $needsReboot = $true }
    } else {
        Write-Host "Hyper-V already enabled."
    }

    if ($needsReboot) {
        "features" | Set-Content -Path $StateFile -Encoding ascii
        Write-Host ""
        Write-Host "A reboot is required to finish Docker setup." -ForegroundColor Yellow
        Write-Host "After reboot, run this script again:" -ForegroundColor Yellow
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\install-docker-postgres.ps1"
        $answer = Read-Host "Reboot now? (y/N)"
        if ($answer -match '^[Yy]') {
            Restart-Computer -Force
        }
        exit 0
    }
}

function Ensure-DockerEngine {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Host "Docker CLI already installed."
        return
    }

    Write-Step "Installing Docker provider and engine"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
    Install-Module -Name DockerMsftProvider -Repository PSGallery -Force
    Install-Package -Name docker -ProviderName DockerMsftProvider -Force
    Restart-Service docker
    Start-Sleep -Seconds 5
}

function Ensure-DockerCompose {
    $composePath = Join-Path $env:ProgramFiles "docker\docker-compose.exe"
    if (Test-Path $composePath) {
        return
    }

    Write-Step "Installing docker-compose"
    $version = "2.24.5"
    $url = "https://github.com/docker/compose/releases/download/v$version/docker-compose-windows-x86_64.exe"
    $dockerDir = Join-Path $env:ProgramFiles "docker"
    New-Item -ItemType Directory -Force -Path $dockerDir | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $composePath -UseBasicParsing
}

function Start-ProjectPostgres {
    Write-Step "Starting PostgreSQL via docker compose"
    Set-Location $ProjectRoot

    $env:COMPOSE_CONVERT_WINDOWS_PATHS = "1"
    & docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed. Linux containers may require nested virtualization on this VM."
    }

    Write-Step "Waiting for Postgres healthcheck"
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 3
        $health = & docker inspect --format "{{.State.Health.Status}}" (docker compose ps -q postgres) 2>$null
        if ($health -eq "healthy") { break }
    } while ((Get-Date) -lt $deadline)

    if ($health -ne "healthy") {
        Write-Host "Postgres container is running but healthcheck not healthy yet. Check: docker compose logs postgres" -ForegroundColor Yellow
    } else {
        Write-Host "PostgreSQL is healthy." -ForegroundColor Green
    }
}

function Ensure-EnvDatabaseUrl {
    $envFile = Join-Path $ProjectRoot ".env"
    $databaseUrl = "DATABASE_URL=postgresql+psycopg2://interview_eval:interview_eval@localhost:5432/interview_eval"
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $ProjectRoot ".env.example") $envFile
    }
    $content = Get-Content $envFile -Raw
    if ($content -match "(?m)^DATABASE_URL=") {
        $content = $content -replace "(?m)^DATABASE_URL=.*$", $databaseUrl
    } else {
        if (-not $content.EndsWith("`n")) { $content += "`n" }
        $content += "`n$databaseUrl`n"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envFile, $content.TrimEnd() + "`n", $utf8NoBom)
    Write-Host "Updated .env with DATABASE_URL"
}

function Invoke-Backfill {
    Write-Step "Backfilling question bank from JSON (if any)"
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Host "Skipping backfill - .venv not found. Run install-vps.ps1 first."
        return
    }
    & $venvPython (Join-Path $ProjectRoot "scripts\backfill_postgres.py")
}

Write-Step "Interview Eval - Docker + PostgreSQL setup"
Ensure-WindowsFeatures
Ensure-DockerEngine
Ensure-DockerCompose
Start-ProjectPostgres
Ensure-EnvDatabaseUrl
Invoke-Backfill

if (Test-Path $StateFile) { Remove-Item $StateFile -Force }

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "DATABASE_URL=postgresql+psycopg2://interview_eval:interview_eval@localhost:5432/interview_eval"
Write-Host "Restart the app: .\scripts\start-server.ps1"
Write-Host "Verify: curl http://localhost:8002/health"
