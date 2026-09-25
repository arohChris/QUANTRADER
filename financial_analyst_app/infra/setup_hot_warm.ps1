#Requires -RunAsAdministrator
# WHY: one-shot bootstrap for the G1 storage engines (environment prep only — no
#      application code, therefore no AGENTS §2 slice). Bible hot path: HOT=Valkey,
#      WARM=TimescaleDB (System_Development_Manual.md:3286-3288).
#   Phase 1 — enable WSL2 + VirtualMachinePlatform, then REBOOT (re-run after reboot).
#   Phase 2 — silent Docker Desktop install (Personal tier = $0 under 250 emp / $10M rev).
# Usage (elevated PowerShell, from repo root):
#   powershell -ExecutionPolicy Bypass -File financial_analyst_app\infra\setup_hot_warm.ps1
$ErrorActionPreference = 'Stop'

function Get-FeatureState([string]$Name) {
    (Get-WindowsOptionalFeature -Online -FeatureName $Name -ErrorAction SilentlyContinue).State
}

$wslState = Get-FeatureState 'Microsoft-Windows-Subsystem-Linux'
$vmState  = Get-FeatureState 'VirtualMachinePlatform'
$hv       = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent
Write-Host "[state] WSL=$wslState  VirtualMachinePlatform=$vmState  HypervisorPresent=$hv"

if ($wslState -ne 'Enabled' -or $vmState -ne 'Enabled') {
    Write-Host '[phase 1] Enabling WSL2 + VirtualMachinePlatform (no forced reboot)...'
    wsl --install --no-distribution 2>$null
    if ((Get-FeatureState 'Microsoft-Windows-Subsystem-Linux') -ne 'Enabled' -or
        (Get-FeatureState 'VirtualMachinePlatform') -ne 'Enabled') {
        dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
        dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
    }
    Write-Host '[phase 1] DONE -> REBOOT REQUIRED, then run this script again.'
    exit 0
}

$dockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
if (Test-Path $dockerExe) {
    Write-Host '[phase 2] Docker Desktop already installed.'
} else {
    $installer = Join-Path $env:TEMP 'DockerDesktopInstaller.exe'
    if (-not (Test-Path $installer)) {
        Write-Host '[phase 2] Downloading Docker Desktop installer (~500 MB)...'
        Invoke-WebRequest -Uri 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe' -OutFile $installer
    }
    Write-Host '[phase 2] Installing Docker Desktop (quiet, accept-license)...'
    Start-Process -FilePath $installer -ArgumentList 'install','--quiet','--accept-license' -Wait
    Write-Host '[phase 2] Install finished.'
}

Write-Host ''
Write-Host 'NEXT STEPS (as your normal user, NOT elevated):'
Write-Host '  1. Start Docker Desktop from the Start menu; wait until the engine is running.'
Write-Host '  2. Copy .env.example to .env and set TIMESCALE_DEV_PASSWORD (dev-only value).'
Write-Host '  3. docker compose --env-file .env -f financial_analyst_app/infra/docker-compose.yml up -d --wait'
