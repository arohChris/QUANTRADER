# WHY THIS IMAGE EXISTS. The `monitor` service in docker-compose.yml has to run the data
# health loop on a schedule without depending on the Windows Task Scheduler, which on this
# host accepts task instances and then never executes them (event 110 "launched" followed by
# event 325 "queued", never 140 or 141 - reproduced even with a bare `cmd /c echo`).
#
# The image is deliberately BORROWING, not owning. The repository is bind-mounted in
# read-only, so the monitor always runs the code that is actually on disk. There is no image
# rebuild step in the daily path, and no way for the container to be running stale logic
# while the working tree has moved on - which for a health check is the difference between
# a monitor and a liar.
FROM python:3.12-slim

# WHY 3.12 AND NOT THE HOST'S 3.14. The host runs 3.14.6, which is newer than most published
# wheels; pinning the container to 3.12 keeps asyncpg, websockets and pyarrow on published
# wheels, so the image builds reproducibly instead of needing a toolchain. Nothing in the
# monitor path uses 3.13/3.14-only syntax.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# curl is for the container healthcheck below; libpq is not needed because asyncpg is a
# pure-python driver, but ca-certificates is, or TLS to the Deriv socket fails.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# WHY INSTALL FROM pyproject.toml RATHER THAN COPYING THE APP. The dependency list is the
# one thing that must be baked in; the code itself is bind-mounted at runtime. So a code
# change needs no rebuild, and this layer only changes when a dependency does.
COPY pyproject.toml /app/pyproject.toml

# Parsed out of the [project] dependencies list rather than duplicated here, because a
# hand-copied list is a list that silently rots the first time someone adds a dependency.
RUN python - <<'PY'
import re, pathlib, subprocess, sys
text = pathlib.Path("/app/pyproject.toml").read_text(encoding="utf-8")
block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
if not block:
    sys.exit("could not find [project] dependencies in pyproject.toml")
deps = re.findall(r'"([^"]+)"', block.group(1))
# Trim to the monitor's actual needs. Installing fastapi/uvicorn/pyarrow/cryptography here
# would triple the image for code paths this container never imports.
needed = ("asyncpg", "websockets", "pydantic", "pydantic-settings", "pyyaml", "structlog")
keep = [d for d in deps if d.split(">=")[0].split("[")[0].strip().lower() in needed]
print("installing:", keep, flush=True)
subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *keep])
PY

# WHY THIS HEALTHCHECK. It verifies the loop is ALIVE AND WORKING, not that the data is
# good. An earlier version of this file curled a port nothing serves, which would have made
# the container permanently unhealthy - a healthcheck that is always red is noise, and Docker
# would restart-loop on it.
#
# The honest liveness signal is the supervisor's own rolling verdict file: it is rewritten
# on every tick, so a recent mtime means the loop ticked, and a stale one means it wedged.
# A 45-minute window is used because the loop runs every 30 minutes, so this tolerates one
# missed tick before calling it dead.
#
# It deliberately does NOT assert the data is clean. That is the monitor's job to report,
# not the healthcheck's: a container whose health depends on the data being good would be
# restarted precisely when a human most needs to read why it went red.
HEALTHCHECK --interval=60s --timeout=20s --start-period=45s --retries=3 \
    CMD f=/app/logs/monitor/verdicts.jsonl; \
        if [ ! -f "$$f" ]; then echo "no verdicts file yet - supervisor has not completed a tick"; exit 1; fi; \
        age=$$(( $$(date +%s) - $$(stat -c %Y "$$f") )); \
        if [ "$$age" -gt 2700 ]; then echo "verdicts file is $${age}s old - loop is wedged"; exit 1; fi; \
        echo "loop alive; last verdict $${age}s ago"; exit 0

CMD ["python", "scripts/run_monitor_loop.py", "--interval-minutes", "30"]
