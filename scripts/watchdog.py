#!/usr/bin/env python3
"""External watchdog for the bare-metal (`./proxy.sh`) deployment only.

Scope, stated because it was not: this restarts the proxy through `proxy.sh`,
which means it applies to the install.sh / proxy.sh path and to nothing else.
Docker, Compose and Kubernetes each supervise the process natively — the
Dockerfile declares a HEALTHCHECK, docker-compose.yml sets
`restart: unless-stopped`, and the Helm chart wires a liveness probe — so
running this alongside any of them would fight the supervisor rather than help
it.

It also used to import `requests`, which is declared in neither
requirements.txt nor requirements-dev.txt, so on any install from those files
it failed at import before doing anything: a self-healing mechanism that could
not start. The health check is one GET, so it uses urllib from the standard
library instead and now depends on nothing.

Usage:
    python scripts/watchdog.py                 # poll every 30s, restart on failure
    python scripts/watchdog.py --url URL       # non-default host/port
    python scripts/watchdog.py --interval 60
    python scripts/watchdog.py --once          # single check, exit 0/1, no restart
"""

import argparse
import logging
import os
import subprocess  # nosec B404 — fixed argv, no shell
import sys
import time
import urllib.error
import urllib.request

DEFAULT_HEALTH_URL = "http://127.0.0.1:8090/health"
DEFAULT_INTERVAL = 30  # seconds
HEALTH_TIMEOUT = 5  # seconds

#: Written by install.sh and proxy.sh. Its absence means this watchdog is
#: pointed at a deployment it cannot restart.
PID_FILE = ".proxy.pid"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - WATCHDOG - %(levelname)s - %(message)s"
)
logger = logging.getLogger("watchdog")


def is_proxy_alive(url: str) -> bool:
    """True when /health answers 200 within the timeout."""
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT) as response:  # nosec B310
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def restart_proxy() -> None:
    logger.warning("Proxy unresponsive! Initiating emergency restart...")
    try:
        subprocess.run(["./proxy.sh", "restart"], check=True)  # nosec B603
        logger.info("Emergency restart command dispatched.")
    except Exception as e:
        logger.error(f"Failed to restart proxy: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit 0 (healthy) or 1 (not); never restarts.",
    )
    args = parser.parse_args()

    if args.once:
        return 0 if is_proxy_alive(args.url) else 1

    if not os.path.exists(PID_FILE):
        logger.warning(
            "%s not found. This watchdog restarts via ./proxy.sh and applies to "
            "the bare-metal deployment only — under Docker, Compose or "
            "Kubernetes the supervisor already restarts the container, and "
            "running this too will fight it.",
            PID_FILE,
        )

    logger.info("Isolated Watchdog started. Monitoring %s", args.url)
    while True:
        if not is_proxy_alive(args.url):
            logger.error("LLMPROXY HEALTH CHECK FAILED!")
            restart_proxy()
        else:
            logger.info("Heartbeat: LLMPROXY is healthy.")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
