import subprocess
import logging

logger = logging.getLogger(__name__)

def get_tailscale_ip() -> str:
    """Discovers the Tailscale IPv4 address if available."""
    try:
        # Run tailscale ip -4
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            ip = result.stdout.strip()
            if ip:
                logger.info(f"Tailscale IP discovered: {ip}")
                return ip
    except Exception as e:
        logger.debug(f"Tailscale not found or inactive: {e}")
    
    return "0.0.0.0"
