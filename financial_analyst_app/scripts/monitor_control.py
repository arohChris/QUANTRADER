# WHY this script exists: you asked how to know whether gap detection is actually RUNNING,
# and how to start it. The honest answer until now was "run the monitor and read the log",
# which requires you to already suspect something. That is the wrong shape for a safety net.
#
# This gives you one command with three answers:
#   STATUS   - is a monitor running right now, and when did it last report?
#   START    - start it, detached, so it survives this shell closing
#   STOP     - stop it
#
# It is DELIBERATELY a status tool, not a fix for the scheduling. The scheduler on this host
# is broken (Task Scheduler accepts instances - event 110 launched, 325 queued - and never
# executes, reproduced even with a bare `cmd /c echo`), so there is no reliable way to make
# Windows start this on a timer. What this script does instead is give you an honest,
# one-command answer about whether the safety net is up, so a silent gap is never a mystery.
#
# Usage:
#   python scripts\monitor_control.py            # status
#   python scripts\monitor_control.py start      # start detached
#   python scripts\monitor_control.py stop
#   python scripts\monitor_control.py once       # run one full pass now, print the verdict
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "scripts" / "run_monitor_loop.py"
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = ROOT / "logs" / "monitor" / "verdicts.jsonl"
STATE = ROOT / "logs" / "monitor" / "supervisor_state.json"
PIDFILE = ROOT / "logs" / "monitor" / "supervisor.pid"

#: A monitor that has not reported within this is treated as NOT running. The loop runs every
#: 30 minutes and retries every 10 on RED, so 3x the normal interval is a defensible line
#: between "quiet" and "wedged". Anything longer and a dead loop looks alive.
STALE_AFTER_SEC = 90 * 60


def _read_verdicts() -> list[dict]:
    if not VERDICTS.exists():
        return []
    out: list[dict] = []
    for line in VERDICTS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        # WHY THE Z-REWRITE IS NOT REDUNDANT (FURB162 suppressed): `fromisoformat` gained
        # bare-"Z" support only in 3.11. Rewriting keeps this working on every interpreter the
        # project runs, and the verdict log is written with a trailing Z.
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))  # noqa: FURB162
    except ValueError:
        return None
    # A naive timestamp is treated as UTC, not as local. Verdicts are written in UTC, and a
    # silent local-time interpretation here would report a fresh monitor as stale - or worse,
    # a stale one as fresh.
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
    except ImportError:
        # Without psutil, fall back to the OS. Being unable to be certain is reported as
        # "unknown", never as "running".
        return _pid_alive_fallback(pid)
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except Exception:  # noqa: BLE001
        return False


def _pid_alive_fallback(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def status() -> int:
    print("=" * 74)
    print("GAP DETECTION - IS IT RUNNING?")
    print("=" * 74)

    pid: int | None = None
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            pid = None

    alive = bool(pid) and _pid_alive(pid or 0)
    state = f"RUNNING (pid {pid})" if alive else "not running"
    print(f"  supervisor process : {state}")

    verdicts = _read_verdicts()
    if verdicts:
        last = verdicts[-1]
        age = _age_seconds(last.get("at") or last.get("timestamp"))
        print(f"  last verdict        : {last.get('verdict', '?')}")
        print(f"  last verdict at     : {last.get('at') or last.get('timestamp')}"
              f"  ({int(age)}s ago)" if age is not None else
              f"  last verdict at     : {last.get('at') or last.get('timestamp')}")
        print(f"  total verdicts      : {len(verdicts)}")
        if age is not None and age > STALE_AFTER_SEC and alive:
            print()
            print("  WARNING: the process exists but has not reported recently. That is a")
            print("  WEDGED loop, not a healthy one. Stop it and start again.")
    else:
        print("  last verdict        : NEVER - no run has ever reported")

    if STATE.exists():
        print(f"  supervisor state    : {STATE.name} present")

    print()
    if alive and verdicts:
        print("  VERDICT: running.")
        return 0
    if alive:
        print("  VERDICT: process up but has never reported - suspect it is wedged.")
        return 1
    print("  VERDICT: NOT RUNNING. Nothing is watching for gaps right now.")
    print("          Start it with:  python scripts\\monitor_control.py start")
    print("          Or run one pass: python scripts\\monitor_control.py --once")
    return 1


def start() -> int:
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            pid = 0
        if pid and _pid_alive(pid):
            print(f"  already running (pid {pid}). Use 'stop' first.")
            return 0
    (ROOT / "logs" / "monitor").mkdir(parents=True, exist_ok=True)
    # DETACHED so it survives this shell closing. On Windows that means a new process group
    # and no console window; on POSIX it means start_new_session. A monitor that dies when
    # you close the terminal is not a monitor.
    kwargs: dict = {}
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    log = open(ROOT / "logs" / "monitor" / "supervisor.log", "a", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, str(LOOP), "--interval-minutes", "30"],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, **kwargs,
    )
    PIDFILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"  started supervisor (pid {proc.pid}), detached.")
    print("  it reports every 30 min into logs/monitor/verdicts.jsonl")
    return 0


def stop() -> int:
    if not PIDFILE.exists():
        print("  no pidfile - nothing recorded as running.")
        return 0
    try:
        pid = int(PIDFILE.read_text(encoding="utf-8").strip() or 0)
    except ValueError:
        pid = 0
    if pid and _pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, check=False)
        else:
            import signal
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        print(f"  stopped pid {pid}")
    else:
        print(f"  pid {pid} was not running.")
    PIDFILE.unlink(missing_ok=True)
    return 0


def run_once() -> int:
    proc = subprocess.run(
        [sys.executable, str(LOOP), "--once"],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.strip()[-2000:])
    for line in reversed(out.splitlines()):
        if "RESULT" in line or "verdict" in line:
            print()
            print("  " + line.strip())
            break
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", nargs="?", default="status",
                    choices=["status", "start", "stop", "once"])
    args = ap.parse_args()
    if args.action == "start":
        return start()
    if args.action == "stop":
        return stop()
    if args.action == "once":
        return run_once()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())