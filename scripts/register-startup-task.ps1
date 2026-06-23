$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$TaskName = "InterviewEvalService"
$StartScript = Join-Path $Root "scripts\start-server.ps1"

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Error "Run scripts\install-vps.ps1 first."
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' to start on boot."
Write-Host "Start now with: Start-ScheduledTask -TaskName '$TaskName'"
