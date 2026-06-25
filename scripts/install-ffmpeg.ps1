$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ToolsDir = Join-Path $Root "tools\ffmpeg"
$BinDir = Join-Path $ToolsDir "bin"
$FfmpegExe = Join-Path $BinDir "ffmpeg.exe"

if (Test-Path $FfmpegExe) {
    Write-Host "ffmpeg already installed at $FfmpegExe"
    exit 0
}

$ZipPath = Join-Path $env:TEMP "ffmpeg-win64.zip"
$ExtractDir = Join-Path $env:TEMP "ffmpeg-extract"

Write-Host "Downloading ffmpeg..."
Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $ZipPath

if (Test-Path $ExtractDir) {
    Remove-Item $ExtractDir -Recurse -Force
}
Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir

$SourceRoot = Get-ChildItem $ExtractDir -Directory | Select-Object -First 1
if (-not $SourceRoot) {
    Write-Error "Could not find ffmpeg folder in downloaded archive."
}

if (Test-Path $ToolsDir) {
    Remove-Item $ToolsDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

Copy-Item (Join-Path $SourceRoot.FullName "bin\ffmpeg.exe") $BinDir
Copy-Item (Join-Path $SourceRoot.FullName "bin\ffprobe.exe") $BinDir

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
Remove-Item $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Installed ffmpeg to $BinDir"
