<#
.SYNOPSIS
    Read the REAL reason the monitor task will not execute, and undo a redundant policy edit.

.DESCRIPTION
    Supersedes scripts/fix_batch_logon_right.ps1, which reached a WRONG conclusion on
    2026-09-30 and must not be trusted.

    What went wrong: the policy showed SeBatchLogonRight already granting
    *S-1-5-32-544 (BUILTIN\Administrators), *S-1-5-32-551 (Backup Operators) and
    *S-1-5-32-559 (Network Configuration Operators). The operator was running ELEVATED, i.e.
    a member of Administrators, so batch logon was ALREADY held through group membership.
    The old script compared only the literal account name and ignored group membership, so
    it decided the right was missing and attempted a grant that was never needed.

    Two consequences handled here:
      1. UNDO - if that grant landed, the redundant entry is removed so the policy is back
         to what it was. It was harmless (a user who already had the right via group), but
         leaving a hand-edit in a security template is not something to do quietly.
      2. DIAGNOSE - batch logon was never the cause, so this reads the authoritative
         sources instead of guessing: LastTaskResult, the Task Scheduler operational event
         log for this task, and whether the action itself is even well-formed.

    No guesses, no policy changes unless -Undo is passed and a redundant entry is found.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\diagnose_monitor_task.ps1
    powershell -ExecutionPolicy Bypass -File scripts\diagnose_monitor_task.ps1 -Undo
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'QuantTrader Data Monitor',
    [switch]$Undo
)

$ErrorActionPreference = 'Continue'
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "elevated: $isAdmin`n"

# ---------------------------------------------------------------- 1. undo
$cfg = Join-Path $env:TEMP ("secpol_" + [guid]::NewGuid().ToString('N') + '.cfg')
if ($Undo) {
    if (-not $isAdmin) { Write-Host "UNDO needs elevation." -ForegroundColor Red; exit 1 }
    secedit /export /cfg $cfg /quiet | Out-Null
    if (Test-Path -LiteralPath $cfg) {
        $lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $cfg)
        $i = ($lines | Select-String -Pattern '^\s*SeBatchLogonRight\s*=' | Select-Object -First 1).LineNumber
        if ($i) {
            Write-Host "SeBatchLogonRight currently:"
            Write-Host "   $($lines[$i-1])"
            $vals = (($lines[$i-1] -replace '^\s*SeBatchLogonRight\s*=\s*','') -split ',' |
                     ForEach-Object { $_.Trim() } | Where-Object { $_ })
            $i2 = $i
            while ($i2 -lt $lines.Count -and $lines[$i2] -notmatch '^\s*\w+\s*=' ) {
                if ($lines[$i2] -match '^\s+\S') {
                    $vals += ($lines[$i2] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
                }
                $i2++
            }
            $name = "$env:USERDOMAIN\$env:USERNAME"
            $bare = $env:USERNAME
            $kept = $vals | Where-Object { $_ -ne $name -and $_ -ne "*$bare" -and $_ -ne $bare }
            if ($kept.Count -eq $vals.Count) {
                Write-Host "no redundant per-user entry found - nothing to undo." -ForegroundColor Green
            } else {
                Write-Host "removing redundant: $name  (already held via *S-1-5-32-544 Administrators)"
                for ($k = 0; $k -lt $lines.Count; $k++) {
                    if ($lines[$k] -match '^\s*SeBatchLogonRight\s*=') {
                        $lines[$k] = "SeBatchLogonRight = " + ($kept -join ',')
                        for ($m = $k + 1; $m -lt $i2; $m++) { $lines.RemoveAt($m) }
                        break
                    }
                }
                [System.IO.File]::WriteAllLines($cfg, $lines)
                secedit /configure /cfg $cfg /quiet | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "policy restored." -ForegroundColor Green
                } else {
                    Write-Host "secedit /configure FAILED ($LASTEXITCODE) - left unchanged." -ForegroundColor Red
                }
            }
        }
    } else { Write-Host "could not export policy." -ForegroundColor Red }
    if (Test-Path -LiteralPath $cfg) { Remove-Item -LiteralPath $cfg -Force }
    Write-Host ""
}

# ---------------------------------------------------------------- 2. the real answer
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $t) {
    Write-Host "TASK '$TaskName' DOES NOT EXIST." -ForegroundColor Red
    Write-Host "  Register it elevated:" -ForegroundColor Yellow
    Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\register_data_monitor.ps1" -ForegroundColor Cyan
    exit 1
}
$i = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host "=== task as registered ==="
Write-Host "  state          : $($t.State)"
Write-Host "  logon type     : $($t.Principal.LogonType)   (run level $($t.Principal.RunLevel))"
Write-Host "  user id        : $($t.Principal.UserId)"
Write-Host "  execute        : $($t.Actions[0].Execute)"
Write-Host "  arguments      : $($t.Actions[0].Arguments)"
Write-Host "  working dir    : $($t.Actions[0].WorkingDirectory)"
Write-Host "  trigger        : $($t.Triggers[0].CimClass.CimClassName) every $($t.Triggers[0].Repetition.Interval)"
Write-Host "  last run       : $($i.LastRunTime)"
Write-Host "  last result    : $($i.LastTaskResult)   <-- 0xHEX is the real failure code"
Write-Host "  next run       : $($i.NextRunTime)"
Write-Host "  missed runs    : $($i.NumberOfMissedRuns)"

# LastTaskResult is the single most useful line above; decode the common ones.
$code = [uint32]($i.LastTaskResult)
$known = @{
    0x0 = 'success'
    0x41301 = 'the task is currently running'
    0x41303 = 'the task has not yet run'
    0x8007052E = 'logon failure - the account or its logon type was rejected'
    0x80070005 = 'access denied'
    0x80070002 = 'file not found - the action path is wrong'
    0x80070032 = 'logon failure - the account has no batch logon right'
    0x80070422 = 'service disabled - the Schedule service is not running'
}
if ($known.ContainsKey($code)) {
    Write-Host "  meaning        : $($known[$code])" -ForegroundColor Yellow
} elseif ($code -ne 0) {
    Write-Host "  meaning        : undecoded; see the event log below" -ForegroundColor Yellow
}

Write-Host "`n=== Task Scheduler event log for this task (authoritative) ==="
$found = $false
foreach ($log in @('Microsoft-Windows-TaskScheduler/Operational', 'System')) {
    try {
        $ev = Get-WinEvent -FilterHashtable @{LogName = $log; StartTime = (Get-Date).AddHours(-6)} -ErrorAction Stop |
              Where-Object { $_.Message -like "*$TaskName*" }
        if ($ev) {
            $found = $true
            Write-Host "  [$log]"
            $ev | Select-Object -First 6 | ForEach-Object {
                Write-Host "   $($_.TimeCreated.ToString('HH:mm:ss')) id=$($_.Id) lvl=$($_.LevelDisplayName)"
                Write-Host "     $(($_.Message -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 2) -join ' | ')"
            }
        }
    } catch { }
}
if (-not $found) {
    Write-Host "  no events found. Task Scheduler > History may be disabled - enable it via:" -ForegroundColor Yellow
    Write-Host "    wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true" -ForegroundColor Cyan
}
Write-Host "  (event id 140 = task started, 141 = action started, 102 = completed, 201 = action failed)"
Write-Host "  (201 carries the real reason - that is the line that matters)"
