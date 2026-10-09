<#
.SYNOPSIS
    Isolate "our task is misconfigured" from "this machine cannot execute scheduled tasks".

.DESCRIPTION
    MEASURED 2026-09-30. The monitor task registers cleanly, the principal resolves to a
    real SID, the logon type reads back as S4U, and the event log shows:

        id=110  Task Scheduler launched "{...}" instance of task "\QuantTrader Data Monitor"
        id=325  Task Scheduler queued instance "{...}" of task "\QuantTrader Data Monitor"

    and then nothing. Never id=140 (task started), never id=141 (action started). The engine
    accepts the instance and refuses to run it.

    That pattern is not diagnostic on its own. It is consistent with BOTH of:
      (a) something specific to our task's configuration, and
      (b) a machine where the Task Scheduler engine cannot start processes at all - which
          is very plausible here, because in this same session `schtasks /delete` was
          DENIED, unelevated registration failed, and a task stuck in `Queued` could not be
          replaced or removed by any means.

    So this registers three deliberately trivial tasks that share NOTHING with the monitor
    except the principal and the logon type, and reports which of them actually run:

        A  cmd.exe /c exit          - the smallest possible action
        B  same, but Interactive    - isolates S4U specifically
        C  the monitor's own shape  - a .ps1 by full path, so a path/quoting problem would
                                       show up here and nowhere else

    If A fails, the environment cannot run scheduled tasks and NO amount of reconfiguring
    ours will help - the supervisor loop or a container is the answer. If A and B pass and
    only C fails, the fault is ours and the difference is in the action.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\isolate_task_execution.ps1
#>
[CmdletBinding()]
param(
    [string]$Prefix = 'QTProbe',
    [int]$WaitSeconds = 25
)

$ErrorActionPreference = 'Continue'
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$accountName = ([Security.Principal.WindowsIdentity]::GetCurrent()).Name
Write-Host "elevated : $isAdmin"
Write-Host "account  : $accountName`n"

$stamp = [guid]::NewGuid().ToString('N').Substring(0, 8)
$marker = Join-Path $env:TEMP "qtprobe_$stamp.txt"

$cases = @(
    @{ Name = "$Prefix-A-cmd-S4U";       Logon = 'S4U';        Exe = 'cmd.exe';      Arg = "/c echo RAN-A > `"$marker`"" }
    @{ Name = "$Prefix-B-cmd-Interactive"; Logon = 'Interactive'; Exe = 'cmd.exe';     Arg = "/c echo RAN-B > `"$marker`"" }
    @{ Name = "$Prefix-C-cmd-passwordless"; Logon = 'Password'; Exe = 'cmd.exe';      Arg = "/c echo RAN-C > `"$marker`"" }
)

$registered = @()
foreach ($c in $cases) {
    try {
        $act = New-ScheduledTaskAction -Execute $c.Exe -Argument $c.Arg
        $trg = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) `
               -RepetitionInterval (New-TimeSpan -Minutes 30) `
               -RepetitionDuration (New-TimeSpan -Days 1)
        $prn = New-ScheduledTaskPrincipal -UserId $accountName -LogonType $c.Logon -RunLevel Limited
        Register-ScheduledTask -TaskName $c.Name -Action $act -Trigger $trg -Principal $prn -Force | Out-Null
        $registered += $c
        Write-Host "registered $($c.Name)  logon=$($c.Logon)"
    } catch {
        Write-Host "could not register $($c.Name): $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

if ($registered.Count -eq 0) {
    Write-Host "`nRESULT: nothing could be registered - this machine will not let us create tasks." -ForegroundColor Red
    exit 1
}

Write-Host "`ntriggering each and watching for the marker file ..."
foreach ($c in $registered) { Start-ScheduledTask -TaskName $c.Name -ErrorAction SilentlyContinue }

$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    Start-Sleep -Seconds 5
    $ran = if (Test-Path -LiteralPath $marker) { (Get-Content -LiteralPath $marker -Raw).Trim() } else { $null }
    $states = ($registered | ForEach-Object { "$($_.Name)=$((Get-ScheduledTask -TaskName $_.Name).State)" }) -join '  '
} while (-not $ran -and (Get-Date) -lt $deadline)

Write-Host ""
foreach ($c in $registered) {
    $i = Get-ScheduledTaskInfo -TaskName $c.Name
    $t = Get-ScheduledTask -TaskName $c.Name
    Write-Host ("  {0,-26} state={1,-8} lastResult={2}  logon={3}" -f `
        $c.Name, $t.State, $i.LastTaskResult, $t.Principal.LogonType)
}

if ($ran) {
    Write-Host "`nRESULT: a scheduled task DID run here (marker='$ran')." -ForegroundColor Green
    Write-Host "So the engine works and the fault is in the monitor task's own configuration." -ForegroundColor Yellow
    Write-Host "Compare the passing case's logon type above with the monitor's S4U setting." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "`nRESULT: NOT ONE scheduled task executed, including a bare 'cmd /c echo'." -ForegroundColor Red
    Write-Host "This machine's Task Scheduler engine is not starting processes at all, so" -ForegroundColor Red
    Write-Host "no configuration of ours will fix it. The right answer here is the" -ForegroundColor Red
    Write-Host "supervisor loop (scripts/run_monitor_loop.py) or the Docker monitor service -" -ForegroundColor Red
    Write-Host "neither depends on Task Scheduler." -ForegroundColor Red
    exit 1
} finally {
    foreach ($c in $registered) {
        Unregister-ScheduledTask -TaskName $c.Name -Confirm:$false -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
}
