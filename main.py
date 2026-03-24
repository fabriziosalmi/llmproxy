"""LLMProxy — LLM Security Gateway. Entry point."""

import asyncio
import logging
import os
import yaml
from dotenv import load_dotenv

from proxy.rotator import RotatorAgent
from core.metrics import start_metrics_server
from core.discovery_utils import get_tailscale_ip
from core.tracing import TraceManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("llmproxy")


async def main():
    # Supply chain integrity check (pre-startup)
    from scripts.verify_deps import verify_all
    if not verify_all(strict=False):
        logger.critical("Supply chain integrity check FAILED. Aborting startup.")
        return

    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)

    # Bind to Tailscale interface if available
    ts_ip = get_tailscale_ip()
    if ts_ip != "0.0.0.0":
        config["server"]["host"] = ts_ip
        logger.info(f"Binding to Tailscale interface: {ts_ip}")

    # Initialize store
    from store.factory import StorageFactory
    store = StorageFactory.get_repository("config.yaml")
    await store.init()

    # Initialize tracing (optional)
    obs_config = config.get("observability", {}).get("tracing", {})
    if obs_config.get("enabled"):
        sentry_dsn = None
        sentry_cfg = config.get("observability", {}).get("sentry", {})
        dsn_env = sentry_cfg.get("dsn_env")
        if dsn_env:
            sentry_dsn = os.environ.get(dsn_env)
        TraceManager.initialize(
            service_name=obs_config.get("service_name", "llmproxy"),
            otlp_endpoint=obs_config.get("otlp_endpoint"),
            sentry_dsn=sentry_dsn,
        )

    # Start metrics server (optional)
    metrics_cfg = config.get("server", {}).get("metrics", {})
    if metrics_cfg.get("enabled"):
        start_metrics_server(port=metrics_cfg.get("port", 9091))

    # Launch the security gateway
    rotator = RotatorAgent(store)
    port = config.get("server", {}).get("port", 8090)
    logger.info(f"LLMProxy Security Gateway starting on port {port}")

    await rotator.run(port=port)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
