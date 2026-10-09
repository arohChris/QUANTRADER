<#
.SYNOPSIS
    Daily data monitor: catch up the candles, then audit them against the venue.

.DESCRIPTION
    One entry point for unattended data health, so nobody has to remember three commands
    and read three logs. Order matters and is enforced here:

      1. sync_backfill_cursor_to_data -- reconcile cursors to the DATA FRONTIER.
      2. load_universe                -- incremental catch-up (small window, not a bulk load).
      3. verify_universe_data         -- audit, including the class F gap-provenance check.

    Step 1 is not optional bookkeeping. On 2026-09-30 a killed 250-day load left 24 cursors
    at 2026-01-23 while the data began 2026-08-31; the next incremental run would have
    resumed from January and silently retried the whole bulk load. Reconciling first means
    a cursor can never again be further behind than the data we actually hold.

    Step 3 runs EVEN IF step 2 fails, on purpose: an audit of stale data still tells you
    whether the store is internally consistent, and "the load broke AND the audit is also
    wrong" is a very different page from "the load broke but the store is fine". The exit
    code reports the worse of the two so a scheduler notices either.

    WHAT COUNTS AS FAILURE. Audit class F1 only: a bar the VENUE has and we do not. That
    is our loss and a backfill can repair it. B2 is informational by design - most of it is
    venue absence nobody can fix (a daily one-minute crypto hole near 21:07Z, a multi-hour
    hole in Deriv's own metals history), and a monitor that is always red is a monitor
    people stop reading.

.PARAMETER CatchupHours
    How far back the incremental load looks. 48h covers a weekend plus a day of downtime.
    Deliberately small: this is a catch-up, not a bulk load.

.PARAMETER GapSample
    Missing minutes to ask the venue about per instrument (class F). Higher is more
    thorough and slower; each probe is one broker request.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_data_monitor.ps1
#>
[CmdletBinding()]
param(
    [int]$CatchupHours = 48,
    [int]$Sample = 3,
    [int]$GapSample = 4,
    [int]$MaxRepairs = 25
)

$ErrorActionPreference = 'Continue'
$AppDir = Split-Path -Parent $PSScriptRoot
Set-Location $AppDir

$LogDir = Join-Path $AppDir 'logs\monitor'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$Log = Join-Path $LogDir "monitor_$Stamp.log"

function Write-Line {
    param([string]$Text)
    $line = "[{0}] {1}" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ssZ'), $Text
    Write-Host $line
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
}

function Invoke-Step {
    # NOT $Args. Naming a parameter $Args collides with PowerShell's automatic $args
    # variable, so the splat silently expanded to nothing: python was launched with no
    # script path, opened an interactive REPL, hit EOF on stdin and exited 0. The first
    # run of this monitor therefore reported GREEN in one second having checked nothing -
    # a monitor that always says GREEN and never looks is worse than no monitor at all.
    param([string]$Name, [string[]]$PyArgs)

    Write-Line "BEGIN $Name"
    $out = & python @PyArgs 2>&1
    $code = $LASTEXITCODE
    $out | Select-Object -Last 25 | ForEach-Object { Add-Content -LiteralPath $Log -Value "    $_" -Encoding utf8 }
    $out | Where-Object { $_ -match '^(RESULT|  - [A-F]\d)' } |
        Select-Object -Last 12 | ForEach-Object { Write-Line "  $_" }

    # Trust the OUTPUT, not just the exit code. Every one of these scripts prints a line
    # starting RESULT. If that line is missing the process did not actually do the work -
    # a REPL, a bad path, an import crash on stderr - and an exit code of 0 from a shell
    # that never ran the program must NOT be reported as a pass. This guard is the reason
    # the REPL bug above could not hide.
    if (-not ($out | Where-Object { $_ -match '^RESULT' })) {
        Write-Line "  ERROR $Name produced no RESULT line - the check did not run (exit=$code)"
        $code = 90
    }
    Write-Line "END   $Name exit=$code"
    return $code
}

Write-Line "=== data monitor start (catchup=${CatchupHours}h sample=$Sample gap=$GapSample) ==="

# Pre-flight: the DB must be up or every later step fails for the same uninformative reason.
$dockerUp = $false
try { docker ps --filter "name=timescaledb" --format '{{.Names}}' 2>$null | Out-Null; $dockerUp = $true } catch { }
if (-not $dockerUp) { Write-Line "WARN docker not reachable; assuming Timescale is already running" }

$syncCode = Invoke-Step 'sync-cursors' @('scripts\sync_backfill_cursor_to_data.py', '--apply')
$loadCode = Invoke-Step 'load-catchup' @('scripts\load_universe.py', '--hours', "$CatchupHours", '--top', '25')

# Repair BEFORE the audit, and this ordering is the point. The loader walks forward from the
# cursor, so a hole sitting BEHIND the cursor is invisible to it - measured 2026-09-30, load
# reported "PASS - all 25 instruments complete" with a known-missing bar still absent.
# repair_detected_gaps.py does a targeted padded fetch instead and never touches a cursor, so
# it fixes exactly that case. The audit then confirms the result.
$repairCode = Invoke-Step 'repair-gaps' @('scripts\repair_detected_gaps.py', '--top', '25', '--max-repairs', "$MaxRepairs", '--apply')
$auditCode = Invoke-Step 'audit' @('scripts\verify_universe_data.py', '--sample', "$Sample", '--gap-sample', "$GapSample", '--use-watermark')

# The audit is the verdict; the load failing is a cause worth surfacing but the store may
# still be sound, so the audit's own code leads.
$overall = if ($auditCode -ne 0) { $auditCode } elseif ($loadCode -ne 0) { $loadCode } else { $syncCode }

$verdict = switch ($overall) {
    0       { 'GREEN - store matches the venue on every check that can fail' }
    90      { 'RED (exit 90) - a check did not RUN (no RESULT line). Treat as unverified, not as a pass.' }
    default { "RED (exit $overall) - see log; the audit is the authority" }
}
Write-Line "RESULT: $verdict"
Write-Line "log: $Log"

# Keep the last 30 runs so disk does not creep, but never fewer than a week.
Get-ChildItem $LogDir -Filter 'monitor_*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 |
    ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

exit $overall
