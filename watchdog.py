#!/usr/bin/env python3
import time
import subprocess
import requests
import logging

# 11.6: External Watchdog (Self-Healing)
# This script runs independently of main.py to monitor liveness.

LOG_FILE = ".proxy.log"
PID_FILE = ".proxy.pid"
HEALTH_URL = "http://127.0.0.1:8090/health"
CHECK_INTERVAL = 30 # seconds

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - WATCHDOG - %(levelname)s - %(message)s'
)
logger = logging.getLogger("watchdog")

def is_proxy_alive():
    try:
        response = requests.get(HEALTH_URL, timeout=5)
        return response.status_code == 200
    except Exception:
        return False

def restart_proxy():
    logger.warning("Proxy unresponsive! Initiating emergency restart...")
    try:
        # Use our FAANG-level proxy.sh for the heavy lifting
        subprocess.run(["./proxy.sh", "restart"], check=True)
        logger.info("Emergency restart command dispatched.")
    except Exception as e:
        logger.error(f"Failed to restart proxy: {e}")

def main():
    logger.info("Isolated Watchdog started. Monitoring LLMPROXY...")

    while True:
        if not is_proxy_alive():
            logger.error("LLMPROXY HEALTH CHECK FAILED!")
            restart_proxy()
        else:
            logger.info("Heartbeat: LLMPROXY is healthy.")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
