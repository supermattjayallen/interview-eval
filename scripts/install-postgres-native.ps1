#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install PostgreSQL 16 on Windows without Docker (recommended on Windows Server 2019 VPS).

  Creates database/user matching docker-compose defaults and updates .env.
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PgPassword = "interview_eval"
$DbName = "interview_eval"
$DbUser = "interview_eval"
$InstallerUrl = "https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64.exe"
$InstallerPath = Join-Path $env:TEMP "postgresql-16-installer.exe"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-PostgresBin {
    $roots = @(
        "C:\Program Files\PostgreSQL\16\bin",
        "C:\Program Files\PostgreSQL\17\bin"
    )
    foreach ($root in $roots) {
        if (Test-Path (Join-Path $root "psql.exe")) { return $root }
    }
    return $null
}

Write-Step "Interview Eval - native PostgreSQL setup"

$pgBin = Find-PostgresBin
if (-not $pgBin) {
    Write-Step "Downloading PostgreSQL 16 installer"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath -UseBasicParsing

    Write-Step "Installing PostgreSQL (postgres superuser password: $PgPassword)"
    $installArgs = @(
        "--mode", "unattended",
        "--unattendedmodeui", "minimal",
        "--superpassword", $PgPassword,
        "--servicename", "postgresql-x64-16",
        "--servicepassword", $PgPassword,
        "--serverport", "5432"
    )
    Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait
    $pgBin = Find-PostgresBin
    if (-not $pgBin) {
        throw "PostgreSQL install finished but psql.exe was not found."
    }
} else {
    Write-Host "PostgreSQL already installed at $pgBin"
}

$psql = Join-Path $pgBin "psql.exe"
$env:PGPASSWORD = $PgPassword

Write-Step "Creating database and user"
$roleSql = @'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'interview_eval') THEN
    CREATE ROLE interview_eval LOGIN PASSWORD 'interview_eval';
  END IF;
END
$$;
'@
$sqlFile = Join-Path $env:TEMP "interview-eval-init.sql"
$roleSql | Set-Content -Path $sqlFile -Encoding ascii
& $psql -U postgres -h localhost -p 5432 -d postgres -v ON_ERROR_STOP=1 -f $sqlFile

$dbExists = (& $psql -U postgres -h localhost -p 5432 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DbName'").Trim()
if (-not $dbExists) {
    & $psql -U postgres -h localhost -p 5432 -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DbName OWNER $DbUser;"
}
& $psql -U postgres -h localhost -p 5432 -d postgres -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE $DbName TO $DbUser;"

$envFile = Join-Path $ProjectRoot ".env"
$databaseUrl = "DATABASE_URL=postgresql+psycopg2://${DbUser}:${PgPassword}@localhost:5432/${DbName}"
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

Write-Step "Backfilling question bank"
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython -m pip install -q sqlalchemy psycopg2-binary
    & $venvPython (Join-Path $ProjectRoot "scripts\backfill_postgres.py")
} else {
    Write-Host "Run install-vps.ps1 first, then: python scripts\backfill_postgres.py"
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host $databaseUrl
Write-Host "Restart the app: .\scripts\start-server.ps1"
