"""Fallback scheduler: run the data monitor on an interval, forever, detached from any
Windows user session.

WHY THIS EXISTS. `register_data_monitor.ps1` creates a Windows scheduled task, which is the
right answer on a normal desktop - but in this environment the task registers, reports
`Ready`, sits in `Queued` forever and never spawns a process. That is not a code fault (the
runner is proven green end to end); it is the Task Scheduler service refusing to launch
processes here. And even where it does work, the task is bound to one interactive logon,
which is wrong for a repository with a SINGLE shared database that every user reads.

So the trigger is separated from the work. This supervisor owns only the timing; the actual
checks are still the same tested scripts:

    scripts/sync_backfill_cursor_to_data.py --apply
    scripts/load_universe.py --hours <n> --top 25
    scripts/repair_detected_gaps.py --top 25 --max-repairs <n> --apply
    scripts/verify_universe_data.py --use-watermark

Design points that matter for something that runs unattended for months:

  * NEVER OVERLAP. A tick that arrives while the previous run is still going is skipped and
    logged. Two concurrent loaders writing the same 800k-row table is exactly the kind of
    thing that produces duplicate keys and half-written cursors.

  * A FAILED RUN IS NOT FATAL. The loop logs the non-zero exit and keeps going. A monitor
    that dies on the first bad night is worse than no monitor, because the next bad night is
    the one that matters.

  * EXIT 90 MEANS "DID NOT RUN", not "PASS". `run_data_monitor.ps1` uses it when a step
    produced no RESULT line at all. This supervisor treats it as a failure and says so,
    rather than filing it under transient noise.

  * The verdict is appended to a single rolling JSONL file, so a later session can answer
    "was it green?" by reading one file instead of guessing.

Run it detached however suits the host:
    python scripts/run_monitor_loop.py --interval-minutes 60          # foreground
    python scripts/run_monitor_loop.py --interval-minutes 60 --once   # single pass
    docker compose -f infra/docker-compose.yml up -d monitor           # survives reboot

Usage: python scripts/run_monitor_loop.py [--interval-minutes N] [--catchup-hours N] [--once]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Running `python scripts/run_monitor_loop.py` puts scripts/ on sys.path, NOT the app root,
# so `import scripts.verify_universe_data` failed with ModuleNotFoundError. Measured
# 2026-09-30: the idle probe then degraded to "assume work to do" on every single tick -
# the safe direction by luck, but it meant the idle gate was never actually exercised.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# exit code run_data_monitor.ps1 uses for "a step did not run", as opposed to "a check ran
# and failed". Treated as a failure here, and named, because the difference matters: one is
# a data problem, the other is a broken monitor, and only one of them is about the data.
EXIT_DID_NOT_RUN = 90

#: How old the newest stored bar may be before the loop treats it as WORK rather than idling.
#:
#: WHY THIS EXISTS (measured defect, 2026-10-07). Without it the loop compared the newest bar
#: to the AUDIT watermark only. When the loader had not run, those two were equal, the loop
#: declared IDLE and slept - repeatedly, forever - while the store aged past 96 hours. The
#: monitor was reporting "nothing to do" precisely because nothing was arriving, which is the
#: one condition it exists to notice.
#:
#: WHY 15 MINUTES. The store is 1-minute bars and the venue closes each minute, so a frontier
#: older than ~15 minutes means the feed stopped - not that the market is quiet. Crypto trades
#: 24/7 so there is no quiet period to excuse it; for FX and metals the calendar-aware
#: `check_gap_provenance` still reports closure correctly, but the LOADER must be invoked
#: either way, because only it can move the frontier.
FRONTIER_STALE_AFTER_SEC = 15 * 60

# On Windows the tested runner is the PowerShell script (it is the one that has been run
# end to end repeatedly). Elsewhere - a container, for instance - PowerShell may not exist,
# so fall back to driving the same steps directly. Both orders are identical, which is the
# point of listing them here rather than in two places.
PS_RUNNER = ROOT / "scripts" / "run_data_monitor.ps1"

# Both dependencies must be reachable before a run is worth attempting: the venue (no
# network = no bars, no audit) and the database (nothing to check). Probed as a TCP connect
# only - cheap, no protocol handshake, no venue request quota spent on a health check.
VENUE_HOST, VENUE_PORT = "api.derivws.com", 443
# MEASURED 2026-09-30: these were pinned to 127.0.0.1:5432, which is right on the host and
# wrong in the `monitor` container, where Timescale is reachable by compose service name.
# The container's first tick reported "database unreachable at 127.0.0.1:5432" and skipped
# every run - a monitor that looks alive and checks nothing. Same env contract as dsn().
DB_HOST = os.environ.get("TIMESCALE_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("TIMESCALE_PORT", "5432"))


def probe(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def dependencies_ready() -> tuple[bool, str]:
    """Is it worth running at all? Returns (ready, reason)."""
    if not probe(DB_HOST, DB_PORT):
        return False, f"database unreachable at {DB_HOST}:{DB_PORT}"
    if not probe(VENUE_HOST, VENUE_PORT):
        return False, f"venue unreachable at {VENUE_HOST}:{VENUE_PORT}"
    return True, "network and database both reachable"


def _steps(catchup_hours: int, max_repairs: int) -> list[list[str]]:
    py = sys.executable or "python"
    return [
        [py, "scripts/sync_backfill_cursor_to_data.py", "--apply"],
        [py, "scripts/load_universe.py", "--hours", str(catchup_hours), "--top", "25"],
        [py, "scripts/repair_detected_gaps.py", "--top", "25",
         "--max-repairs", str(max_repairs), "--apply"],
        [py, "scripts/verify_universe_data.py", "--use-watermark"],
    ]


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _newest_bar_and_watermark() -> tuple[Any, Any]:
    async def _q() -> tuple[Any, Any]:
        import asyncpg

        from scripts.verify_universe_data import dsn

        conn = await asyncpg.connect(
            dsn(), timeout=15
        )
        try:
            newest = await conn.fetchval("SELECT max(interval_start) FROM candles_1m")
            audited = await conn.fetchval(
                "SELECT max(verified_through) FROM data_audit_watermark"
            )
            return newest, audited
        finally:
            await conn.close()

    return asyncio.run(_q())


def idle_check(last_verdict_green: bool) -> tuple[bool, str]:
    """Is there anything NEW to do? This is the 'sits idle when the data is correct' part.

The audit is already incremental via its watermark, so a run with no new bars would
    walk nothing and prove nothing - it would spend a venue request and print GREEN, which
    is indistinguishable from a real verification. That is the worst kind of green: the
    appearance of a check with none of the substance. So if the newest bar we hold is not
    newer than the newest bar already audited, and the previous verdict was green, there is
    genuinely nothing to do and the loop reports IDLE rather than pretending to check.

    WHY FRESHNESS IS CHECKED BEFORE IDLING (measured defect, 2026-10-07). The
    newest-vs-watermark test only says the AUDIT is up to date. It says NOTHING about whether
    the STORE is. When the loader has not run, the newest bar is old AND already audited, so
    this function returned IDLE and the loop slept - repeatedly, forever. That is how the
    store reached 96.9 hours stale while the monitor printed a healthy IDLE every cycle: the
    safety net was asleep exactly when it was needed. An idle verdict is only honest if
    idling means "correct", not "nothing arrived", so the frontier's AGE is asserted first.

    Returns (has_work, reason).
    """
    try:
        newest, audited = _newest_bar_and_watermark()
    except Exception as exc:  # noqa: BLE001 - if we cannot tell, assume there IS work
        return True, f"idle check unavailable ({type(exc).__name__}) - assuming work to do"
    if newest is None:
        return True, "no bars stored at all - the load has never run"
    if audited is None:
        return True, "never audited - a full audit is due"
    if newest > audited:
        return True, (f"new bars since last audit: {newest.isoformat()} > "
                      f"{audited.isoformat()}")
    frontier_age_s = (datetime.now(UTC) - newest).total_seconds()
    if frontier_age_s > FRONTIER_STALE_AFTER_SEC:
        return True, (f"store frontier is stale: newest bar {newest.isoformat()} is "
                      f"{int(frontier_age_s // 60)} min old - the loader must run")
    if not last_verdict_green:
        # Data has not moved, but the last verdict was RED. A defect does not heal by
        # waiting, and re-running is the only way to find out. Never go idle on RED.
        return True, "no new bars, but the last verdict was RED - re-checking"
    return False, f"idle: newest bar {newest.isoformat()} already audited, last verdict GREEN"


def run_once(catchup_hours: int, max_repairs: int) -> tuple[int, str]:
    """One full pass. Returns (overall_exit, one-line_verdict)."""
    if _is_windows() and PS_RUNNER.exists():
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(PS_RUNNER)]
        # Pass the interval knobs through so the two paths cannot drift apart.
        cmd += ["-CatchupHours", str(catchup_hours), "-MaxRepairs", str(max_repairs)]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        # NOT startswith: the PowerShell runner timestamps every line, so a verdict reads
        # "[2026-09-30 13:11:06Z] RESULT: GREEN - ...". Matching the bare prefix silently
        # reported "the monitor did not run" about a run that had just gone green - the
        # exact false-negative this whole script exists to prevent.
        for line in reversed(out.splitlines()):
            if "RESULT:" in line:
                return proc.returncode, line.split("RESULT:", 1)[1].strip()
        return proc.returncode, "no RESULT line - the monitor did not run"

    worst, verdict = 0, "PASS - all steps clean"
    for step in _steps(catchup_hours, max_repairs):
        proc = subprocess.run(step, cwd=ROOT, capture_output=True, text=True, check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        # Trust output, not just the exit code: a process that printed no RESULT line did
        # not do the work, whatever it returned. This is the same lesson as the $Args bug.
        if not any("RESULT" in l for l in out.splitlines()):
            worst = max(worst, EXIT_DID_NOT_RUN)
            verdict = f"RED - {Path(step[1]).name} produced no RESULT line (did not run)"
            continue
        if proc.returncode != 0 and worst == 0:
            worst = proc.returncode
            verdict = f"RED (exit {proc.returncode}) - {Path(step[1]).name} failed"
    return worst, verdict


def should_announce(streak: int) -> bool:
    """Is this failure worth a reminder line?

    WHY. A single red line in a log gets missed, and a problem that only ever says
    something once is a problem that gets forgotten - which is the opposite of the
    "no failure" the caller actually wants. So the first three consecutive failures are
    each announced, then every tenth keeps it visible without becoming spam. Pure and
    side-effect free so it can be tested directly rather than inferred from a log.
    """
    return streak in (1, 2, 3) or streak % 10 == 0


def failure_verdict(streak: int, verdict: str, first_seen: str) -> str:
    """The text for a failed run, escalated while the problem is not yet resolved."""
    if not should_announce(streak):
        return verdict
    return f"REMINDER {streak}/3+ - {verdict} (unresolved since {first_seen})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval-minutes", type=int, default=60)
    ap.add_argument("--offline-poll-minutes", type=int, default=2,
                    help="how often to re-probe while the network or database is down")
    ap.add_argument("--retry-minutes", type=int, default=10,
                    help="how soon to retry after a failed run (never longer than the interval)")
    ap.add_argument("--catchup-hours", type=int, default=48)
    ap.add_argument("--max-repairs", type=int, default=25)
    ap.add_argument("--once", action="store_true", help="single pass, then exit")
    args = ap.parse_args()

    log_dir = ROOT / "logs" / "monitor"
    log_dir.mkdir(parents=True, exist_ok=True)
    verdict_log = log_dir / "verdicts.jsonl"
    state_file = log_dir / "supervisor_state.json"

    # WHY A STATE FILE. "No failure, fully autonomous" must not mean "a problem is
    # forgotten once it stops being true". The streak survives restarts, so a monitor that
    # is restarted mid-incident does not start counting from one and look fine.
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or corrupt state file is a fresh start
        state = {"streak": 0, "first_seen": None, "reminded_at_streak": []}
    streak = int(state.get("streak") or 0)
    first_seen = state.get("first_seen")
    reminded = set(state.get("reminded_at_streak") or [])

    def save_state() -> None:
        state.update({"streak": streak, "first_seen": first_seen,
                      "reminded_at_streak": sorted(reminded)})
        state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def record(code: int, verdict: str, **extra: Any) -> None:
        line = json.dumps({
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "exit": code,
            "verdict": verdict,
            "trigger": "once" if args.once else f"every {args.interval_minutes}m",
            **extra,
        })
        with verdict_log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)

    if args.once:
        code, verdict = run_once(args.catchup_hours, args.max_repairs)
        record(code, verdict)
        return code

    print(f"monitor loop: every {args.interval_minutes}m, "
          f"catchup={args.catchup_hours}h, verdicts -> {verdict_log}", flush=True)
    last_green = True
    running = False
    while True:
        if running:
            # Skipped rather than queued. A backlog of runs is how a monitor turns into a
            # self-inflicted denial of service against the venue.
            print("SKIP - previous run still going; not overlapping", flush=True)
            time.sleep(args.interval_minutes * 60)
            continue
        running = True
        started = time.monotonic()

        # NETWORK / DB GATE. Attempting a full pass with the network down burns a run,
        # produces a RED that says nothing about the DATA, and - worse - trains you to
        # ignore RED. An outage is an outage, not a data defect, so it is reported as its
        # own verdict and the loop then polls cheaply and runs the moment the network
        # returns instead of sitting out the full interval. This is also what covers the
        # machine going offline and coming back: nothing is queued, nothing is lost, and
        # the first tick after the network returns does the work.
        ready, reason = dependencies_ready()
        if not ready:
            record(0, f"SKIP - {reason}; nothing attempted", kind="offline")
            wait = args.offline_poll_minutes * 60
            print(f"offline; re-probing in {args.offline_poll_minutes}m", flush=True)
            running = False
            time.sleep(wait)
            continue

        # IDLE GATE. Healthy data with nothing new is the common case, and the right
        # response to it is to do nothing and say so. Re-auditing unchanged data would
        # print a GREEN indistinguishable from a real verification.
        has_work, why = idle_check(last_green)
        if not has_work:
            record(0, f"IDLE - {why}", kind="idle")
            print(f"idle; next check in {args.interval_minutes}m", flush=True)
            running = False
            time.sleep(args.interval_minutes * 60)
            continue
        print(f"work to do: {why}", flush=True)

        try:
            code, verdict = run_once(args.catchup_hours, args.max_repairs)
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything a step throws
            code, verdict = 1, f"RED - supervisor caught {type(exc).__name__}: {exc}"
        finally:
            running = False
        elapsed = time.monotonic() - started

        if code == 0:
            last_green = True
            if streak:
                # A recovery is only trustworthy if it is VISIBLE as a recovery. Recording
                # the resolution next to the failures is what stops an incident from
                # looking like it never happened.
                record(0, f"RESOLVED after {streak} failed run(s) "
                          f"(first seen {first_seen}) - {verdict}",
                       kind="resolved", prior_streak=streak)
            streak, first_seen, reminded = 0, None, set()
            save_state()
        else:
            last_green = False
            streak += 1
            first_seen = first_seen or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            # "AT LEAST TWO REMINDERS". A single red line in a log gets missed; a problem
            # that only ever says something once is a problem that gets forgotten. The
            # escalation rule lives in failure_verdict() so it is testable on its own.
            verdict = failure_verdict(streak, verdict, first_seen)
            record(code, verdict, kind="failed", streak=streak, since=first_seen)
            reminded.add(streak)
        save_state()

        # A green run waits the full interval. A red one retries sooner, because the
        # likeliest causes - a flapping network, a container restarting, the venue rate
        # limiting - all clear on their own within minutes, and a monitor that waits an
        # hour to notice it recovered has told you nothing useful in between.
        if code == 0:
            sleep_s = max(30.0, args.interval_minutes * 60 - elapsed)
        else:
            sleep_s = max(60.0, min(args.retry_minutes, args.interval_minutes) * 60)
        print(f"next run in {int(sleep_s // 60)}m", flush=True)
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
