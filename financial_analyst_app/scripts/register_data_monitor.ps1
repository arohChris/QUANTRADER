<#
.SYNOPSIS
    Register (or remove) the daily data monitor as a Windows scheduled task.

.DESCRIPTION
    There is no git remote on this repo, so GitHub Actions is not an option - the monitor
    has to run on THIS machine. Windows Task Scheduler is the right tool: it survives
    reboot, runs without a logged-in session if configured, and its own history is visible
    in the Task Scheduler UI rather than hidden in a CI log nobody opens.

    WHY NOT A HAND-WRITTEN DAILY SCHEDULE. A daily task can sit through a full day of a
    broken feed before anyone notices: the load would report PASS, the store would go hours
    behind the venue, and a strategy reading it would be working on stale candles with no
    signal that anything was wrong. 30 minutes bounds that blind window to something a human
    would actually call an incident, and it is cheap - the runner's idle gate means a healthy
    store costs one TCP probe and one cheap DB query and does no venue work at all.

    WHY S4U AND NOT INTERACTIVE. This repository has ONE shared Timescale database that
    every user of the machine reads and writes. Data health is therefore a property of that
    shared store, not of whoever is logged in - so the monitor must not be tied to an
    interactive desktop session. Registered with the default principal (LogonType
    Interactive) the task only runs while a user is signed in, and in practice it sat in
    state=Queued and NEVER EXECUTED at all. S4U ("service for your account") runs whether or
    not anyone is logged on, keeps the audit trail attributed to a real account rather than
    SYSTEM, and - unlike storing a password - needs no secret in this repo. It does need the
    "Log on as a batch job" right, which Windows grants to Administrators by default.

    The task runs `run_data_monitor.ps1`, which is the single tested entry point. This
    script does NOT re-implement any of it. If you are ever unsure what the task does,
    read that file, not this one.

    Idempotent: registering twice updates the existing task rather than failing or
    duplicating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_data_monitor.ps1
    powershell -ExecutionPolicy Bypass -File scripts\register_data_monitor.ps1 -Unregister
    powershell -ExecutionPolicy Bypass -File scripts\register_data_monitor.ps1 -RunNow
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'QuantTrader Data Monitor',
    [string]$At = '03:05',
    [int]$EveryMinutes = 30,
    [switch]$Unregister,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'
$AppDir = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot 'run_data_monitor.ps1'

# Elevation is detected, not assumed, because it changes what a failure MEANS. Run elevated
# and S4U must work; if it does not, that is a real problem to report, not something to paper
# over with the broken Interactive mode. Run unelevated and the Interactive fallback is a
# labelled stopgap. The point is that the operator is never left believing they have a
# working unattended task when they do not.
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# RESOLVE THE ACCOUNT FROM THE TOKEN, NOT FROM ENV VARS.
# MEASURED 2026-09-30: this used "$env:USERDOMAIN\$env:USERNAME". In an ELEVATED shell
# $env:USERDOMAIN came back EMPTY, so the principal was registered with the bare, unqualified
# string "MY PC" instead of "DESKTOP-QT25Q1V\MY PC". Task Scheduler cannot resolve an
# unqualified name for an S4U logon, so the task registered fine, reported `state: Ready`,
# and then sat in `state: Queued` forever without ever starting a process. The
# WindowsIdentity token always carries the fully-qualified name, so use that - and refuse
# to register at all if it somehow is not qualified, because an unqualified principal is a
# silent no-op that looks like success.
$accountName = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
if ($accountName -notmatch '\\') {
    $accountName = (whoami.exe 2>$null)
}
if (-not $accountName) {
    throw "could not determine the current account name. Refusing rather than registering a " +
          "task whose principal would be unresolved."
}

# TEST RESOLVABILITY, NOT STRING SHAPE. An earlier version of this script rejected any
# principal without a backslash, reasoning that an unqualified name cannot be resolved for
# an S4U logon. MEASURED 2026-09-30: that guard was WRONG. The name was registered fully
# qualified as DESKTOP-QT25Q1V\MY PC and Task Scheduler canonically stored it as the bare
# "MY PC" - a legitimate representation for a local account - so the guard refused a
# perfectly valid task and, worse, would have refused it forever.
#
# What actually matters is whether the name maps to a real account, so ASK the OS. If it
# translates to a SID, it resolves, and a bare name is fine.
$accountSid = $null
try {
    $accountSid = ([System.Security.Principal.NTAccount]::new($accountName)).Translate(
        [System.Security.Principal.SecurityIdentifier]).Value
} catch { $accountSid = $null }
if (-not $accountSid) {
    throw ("account '$accountName' does not resolve to a known account (no SID). Refusing " +
           "rather than registering a task that would queue forever.")
}

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed task '$TaskName'"
    } else {
        Write-Host "no task named '$TaskName' - nothing to remove"
    }
    return
}

if (-not (Test-Path $Runner)) { throw "runner not found: $Runner" }

# Built as a variable rather than inline: nesting escaped quotes inside a parenthesised
# argument list is how you get a parser error at 3am the night before you need this.
$argLine = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Runner
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine `
    -WorkingDirectory $AppDir
# Repeating trigger: first run at $At, then every $EveryMinutes for 10 years (the practical
# maximum for a repetition duration). A daily trigger would let a broken feed go a full day
# unnoticed; see the header for why the shorter window is the point.
$startAt = (Get-Date).Date.Add([TimeSpan]::Parse($At))
if ($startAt -le (Get-Date)) { $startAt = $startAt.AddDays(1) }
$trigger = New-ScheduledTaskTrigger -Once -At $startAt `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# Run whether or not someone is logged on, and do not stop just because the machine was
# busy: a missed health check is how a silent data hole survives for a month.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

# S4U, not the default Interactive principal - see the header for why. Without this the
# task only runs while a user is signed in, and it does not run at all in practice.
$principal = New-ScheduledTaskPrincipal -UserId $accountName `
    -LogonType S4U -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description (
        'Catches up candles_1m from Deriv, auto-repairs gaps the venue has and we do not, ' +
        'then audits the shared store. See scripts/run_data_monitor.ps1.') -Force | Out-Null
    Write-Host "registered '$TaskName' (S4U - runs whether or not anyone is logged on)" -ForegroundColor Green
} catch {
    # 0x80070005 = access denied. S4U is an Administrator-only operation, so a normal shell
    # CANNOT create the correct task. Two very different responses, and conflating them is
    # how someone ends up believing they have an unattended monitor when they do not:
    #   * ELEVATED and it still failed -> a genuine problem. Report it and DO NOT fall back,
    #     because falling back here would create an Interactive task that looks fine and
    #     silently is not the thing that was asked for.
    #   * NOT elevated -> expected. Register the labelled stopgap so something exists, and
    #     print the exact elevated command.
    if ($isAdmin) {
        Write-Host "FAILED while ELEVATED - this is not a permissions problem." -ForegroundColor Red
        Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  No task was created. Nothing has been silently downgraded." -ForegroundColor Red
        exit 1
    }
    Write-Host "NOT elevated - S4U needs Administrator, so this is expected here." -ForegroundColor Yellow
    Write-Host "  For the correct unattended task, right-click PowerShell > Run as" -ForegroundColor Yellow
    Write-Host "  administrator, and run this ONCE:" -ForegroundColor Yellow
    Write-Host "    powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -ForegroundColor Cyan
    Write-Host "  Falling back to LogonType=Interactive, which ONLY runs while you are" -ForegroundColor Yellow
    Write-Host "  logged in - a labelled stopgap, NOT the shared-database task you want." -ForegroundColor Yellow
    $fallback = New-ScheduledTaskPrincipal -UserId $accountName `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $fallback -Description (
        'Catches up candles_1m, auto-repairs venue gaps, audits the shared store. ' +
        'INTERACTIVE FALLBACK - re-register as Administrator for S4U.') -Force | Out-Null
}

# READ THE PRINCIPAL BACK. Do not trust that registration did what was asked: a task can be
# created with the wrong logon type and still report Ready, and that is exactly the failure
# that produced a task which sat in state=Queued forever and never executed. If the logon
# type is not what was intended, say so here rather than letting it be discovered by
# noticing that no logs appear.
$t = Get-ScheduledTask -TaskName $TaskName
$logon = $t.Principal.LogonType
$expected = if ($isAdmin) { 'S4U' } else { 'Interactive' }
Write-Host "  first run: $startAt, then every $EveryMinutes min"
Write-Host "  command : $Runner"
Write-Host "  log dir : $(Join-Path $AppDir 'logs\monitor')"
Write-Host "  logon   : $logon (expected: $expected)  elevated: $isAdmin"
Write-Host "  account : $($t.Principal.UserId)   (resolves to SID $accountSid)"
# Report the stored name, but judge it by whether it RESOLVES - Task Scheduler canonically
# stores a local account in its bare form, and that is fine. Only a name that resolves to
# nothing is fatal, and that was already rejected above before we ever registered.
try {
    $stored = ([System.Security.Principal.NTAccount]::new($t.Principal.UserId)).Translate(
        [System.Security.Principal.SecurityIdentifier]).Value
    Write-Host "  stored principal resolves to SID $stored  (OK)"
} catch {
    Write-Host "  FATAL: the stored principal '$($t.Principal.UserId)' does not resolve to any" -ForegroundColor Red
    Write-Host "  account, so this task could never execute. Removing it rather than" -ForegroundColor Red
    Write-Host "  leaving something that only looks registered." -ForegroundColor Red
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    exit 1
}

# ENABLE THE TASK SCHEDULER EVENT LOG. MEASURED 2026-09-30: it was disabled on this machine,
# so when the task would not start there was NO way to learn why - no event 201, no failure
# code, nothing but a guess. Enabling it now costs nothing and makes the next failure
# self-explaining. Skipped silently if the channel does not exist on this SKU.
try {
    if (-not (Get-WinEvent -ListLog 'Microsoft-Windows-TaskScheduler/Operational' `
            -ErrorAction Stop).IsEnabled) {
        wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true 2>$null | Out-Null
        if ((Get-WinEvent -ListLog 'Microsoft-Windows-TaskScheduler/Operational').IsEnabled) {
            Write-Host "  event log: ENABLED (was off - this is why the failure was invisible)" -ForegroundColor Green
        }
    } else {
        Write-Host "  event log: already enabled"
    }
} catch {
    Write-Host "  event log: could not inspect - failures may be silent" -ForegroundColor Yellow
}
if ($logon -ne $expected) {
    Write-Host "  WARNING: logon type is $logon but $expected was requested." -ForegroundColor Red
    exit 1
}
Write-Host "  state   : $($t.State)   next run: $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"

if ($RunNow) {
    Write-Host "running it once now to prove the registration works..."
    $before = (Get-ChildItem (Join-Path $AppDir 'logs\monitor') -Filter 'monitor_*.log' -ErrorAction SilentlyContinue).Count
    Start-ScheduledTask -TaskName $TaskName
    for ($i = 0; $i -lt 18; $i++) {
        Start-Sleep -Seconds 10
        $n = (Get-ChildItem (Join-Path $AppDir 'logs\monitor') -Filter 'monitor_*.log' -ErrorAction SilentlyContinue).Count
        $st = (Get-ScheduledTask -TaskName $TaskName).State
        if ($n -gt $before) {
            Write-Host "  PROVED: the task ran and wrote a new log (state was $st)" -ForegroundColor Green
            break
        }
        if ($st -ne 'Running' -and $i -gt 3) {
            Write-Host "  FAILED: state=$st and no log after 40s. The task is not executing." -ForegroundColor Red
            Write-Host "  Check Task Scheduler > History, and that the account has the" -ForegroundColor Red
            Write-Host "  'Log on as a batch job' right (secpol.msc > Local Policies >" -ForegroundColor Red
            Write-Host "  Security Options > Log on as a batch job)." -ForegroundColor Red
            exit 1
        }
    }
}
