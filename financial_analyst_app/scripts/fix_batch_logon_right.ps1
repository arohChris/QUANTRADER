<#
.SYNOPSIS
    SUPERSEDED AND KNOWN WRONG - see scripts/diagnose_monitor_task.ps1. Do not run this.
    Diagnose and grant "Log on as a batch job" so the S4U monitor task can actually run.

.DESCRIPTION
    The task registers correctly as S4U and reports `state: Ready`, then sits in
    `state: Queued` forever and never spawns a process. Measured 2026-09-30: S4U
    registration succeeded and the logon type read back as S4U, but `Start-ScheduledTask`
    left the task Queued with no log written after 40s.

    That signature - registers fine, reports Ready, never executes - is the textbook
    symptom of the account lacking the "Log on as a batch job" right. It is NOT a password
    problem and NOT a path problem, which is why storing a password or editing the action
    would be wasted effort.

    This script reads the effective policy, tells you exactly what it found, and grants the
    right only if it is genuinely missing. It changes nothing if the right is already held,
    so it is safe to run before you understand the problem.

    WHY SCRIPTED INSTEAD OF A secpol.msc CLICK-PATH. The secedit round trip is
    export -> edit the SeBatchLogonRight line -> import. Doing that by hand means rewriting
    one line of a security template by eye, and getting it wrong silently resets unrelated
    policy on import. This does it additively and idempotently, and prints before/after.

.NOTES
    Run ELEVATED. Changes to logon rights are Administrator-only and are deliberately so.
#>
[CmdletBinding()]
param(
    [string]$Account = "$env:USERDOMAIN\$env:USERNAME",
    [switch]$ReportOnly
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "account : $Account"
Write-Host "elevated: $isAdmin"
if (-not $isAdmin) {
    Write-Host "`nMUST be elevated to read or change logon rights." -ForegroundColor Red
    Write-Host "Right-click PowerShell > Run as administrator, then re-run this script." -ForegroundColor Yellow
    exit 1
}

$cfg = Join-Path $env:TEMP ("secpol_" + [guid]::NewGuid().ToString('N') + '.cfg')
try {
    secedit /export /cfg $cfg /quiet | Out-Null
    if (-not (Test-Path $cfg)) { throw "secedit /export produced no file" }
    $lines = [System.Collections.Generic.List[string]](Get-Content $cfg)

    function Get-RightValue {
        param([string]$Name)
        $i = ($lines | Select-String -Pattern "^\s*$Name\s*=" | Select-Object -First 1).LineNumber
        if (-not $i) { return @() }
        # Rights are a comma-separated list on the assignment line and may wrap onto
        # continuation lines that are indented. Collect until a line that starts a new key.
        $vals = @()
        $j = $i - 1
        $tail = $lines[$j] -replace "^\s*$Name\s*=\s*", ''
        if ($tail.Trim()) { $vals += ($tail -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
        for ($k = $i; $k -lt $lines.Count; $k++) {
            if ($lines[$k] -match '^\s*\w+\s*=' ) { break }
            if ($lines[$k] -match '^\s+\S') { $vals += ($lines[$k] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
        }
        return $vals
    }

    $allow = Get-RightValue 'SeBatchLogonRight'
    $deny = Get-RightValue 'SeDenyBatchLogonRight'

    Write-Host "`n--- SeBatchLogonRight (allowed) ---"
    if ($allow.Count) { $allow | ForEach-Object { Write-Host "   $_" } } else { Write-Host "   (EMPTY - no account may log on as a batch job)" }
    Write-Host "--- SeDenyBatchLogonRight (blocked, wins over allow) ---"
    if ($deny.Count) { $deny | ForEach-Object { Write-Host "   $_" } } else { Write-Host "   (empty)" }

    $user = $Account.Split('\')[-1]
    $has = $allow | Where-Object { $_ -eq $Account -or $_ -eq "*$user" -or $_ -eq $user }
    $blocked = $deny | Where-Object { $_ -eq $Account -or $_ -eq "*$user" -or $_ -eq $user }

    if ($blocked) {
        Write-Host "`nRESULT: this account is EXPLICITLY DENIED batch logon." -ForegroundColor Red
        Write-Host "A deny always beats an allow, so granting the right will not help until" -ForegroundColor Red
        Write-Host "the account is removed from SeDenyBatchLogonRight (Local Group Policy / GPO)." -ForegroundColor Red
        exit 2
    }
    if ($has) {
        Write-Host "`nRESULT: the account ALREADY has 'Log on as a batch job'." -ForegroundColor Green
        Write-Host "So the right is not the cause. Next suspects, in order:" -ForegroundColor Yellow
        Write-Host "  1. The machine is domain-joined and a GPO overrides this local setting." -ForegroundColor Yellow
        Write-Host "  2. Task Scheduler > History shows the real failure code (0x8007052E etc.)." -ForegroundColor Yellow
        Write-Host "  3. S4U cannot reach network resources; a UNC path or mapped drive in the" -ForegroundColor Yellow
        Write-Host "     action would fail - ours is a local path, so this is unlikely." -ForegroundColor Yellow
        exit 0
    }
    if ($ReportOnly) {
        Write-Host "`nRESULT: right is MISSING and -ReportOnly was given, so nothing changed." -ForegroundColor Yellow
        exit 3
    }

    # Additive and idempotent: keep every existing entry, append ours.
    Write-Host "`ngranting 'Log on as a batch job' to $Account ..."
    $new = ($allow + $Account) -join ','
    $replaced = $false
    for ($k = 0; $k -lt $lines.Count; $k++) {
        if ($lines[$k] -match '^\s*SeBatchLogonRight\s*=') {
            $lines[$k] = "SeBatchLogonRight = $new"
            $replaced = $true
            break
        }
    }
    if (-not $replaced) {
        for ($k = 0; $k -lt $lines.Count; $k++) {
            if ($lines[$k] -match '^\s*\[Privilege Rights\]') {
                $lines.Insert($k + 1, "SeBatchLogonRight = $new")
                $replaced = $true
                break
            }
        }
    }
    if (-not $replaced) { throw "could not find SeBatchLogonRight or [Privilege Rights] in the template" }

    [System.IO.File]::WriteAllLines($cfg, $lines)
    secedit /configure /cfg $cfg /quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "secedit /configure failed with $LASTEXITCODE" }

    Write-Host "`nRESULT: right granted. Verify and re-test with:" -ForegroundColor Green
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -ReportOnly" -ForegroundColor Cyan
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandRoot\register_data_monitor.ps1`" -RunNow" -ForegroundColor Cyan
    exit 0
} finally {
    Remove-Item $cfg -Force -ErrorAction SilentlyContinue
}
