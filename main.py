import asyncio
import logging
from store.store import EndpointStore
from agents.scanner import ScannerAgent
from agents.crawler import CrawlerAgent
from agents.sota_interface_agent import SOTAInterfaceAgent
from agents.validator import ValidatorAgent
from agents.advanced_validator import AdvancedValidatorAgent
from proxy.rotator import RotatorAgent
from agents.admin_agent import AdminAgent
from agents.self_healer import SelfHealerAgent
from agents.distiller import DistillerAgent
from core.metrics import start_metrics_server
from core.local_assistant import LocalAssistant
from core.supervisor import AgentSupervisor
from core.discovery_utils import get_tailscale_ip
from core.tracing import TraceManager
from repl.interface import start_repl
from dotenv import load_dotenv
import yaml
import os

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("main")

async def main():
    # Load config
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)

    # Tailscale Discovery
    ts_ip = get_tailscale_ip()
    if ts_ip != "0.0.0.0":
        config["server"]["host"] = ts_ip
        logger.info(f"Binding to Tailscale interface: {ts_ip}")

    # Initialize store via Factory (Architect's Refinement: Modular Adapters)
    from store.factory import StorageFactory
    store = StorageFactory.get_repository("config.yaml")
    await store.init()

    # Initialize Tracing
    obs_config = config.get("observability", {}).get("tracing", {})
    if obs_config.get("enabled"):
        TraceManager.initialize(
            service_name=obs_config.get("service_name", "llmproxy"),
            otlp_endpoint=obs_config.get("otlp_endpoint")
        )
    
    # Initialize supervisor
    supervisor = AgentSupervisor()
    
    # Initialize local AI assistant
    local_llm = LocalAssistant(
        host=config["local_llm"]["host"],
        default_model=config["local_llm"]["model"]
    )
    
    # Initialize agents
    scanner = ScannerAgent(store)
    crawler = CrawlerAgent()
    interface = SOTAInterfaceAgent(store, local_llm)
    validator = ValidatorAgent(store)
    adv_validator = AdvancedValidatorAgent(store)
    rotator = RotatorAgent(store, assistant=local_llm)
    admin_port = config.get("server", {}).get("admin", {}).get("port", 8081)
    admin = AdminAgent(store, port=admin_port)
    healer = SelfHealerAgent(store, local_llm)
    distiller = DistillerAgent(store)
    
    # Start background agents
    asyncio.create_task(healer.start())
    asyncio.create_task(distiller.start())
    
    # Start Metrics
    if config["server"]["metrics"]["enabled"]:
        logger.info(f"Starting metrics server on port {config['server']['metrics']['port']}...")
        start_metrics_server(port=config["server"]["metrics"]["port"])

    # Register agents with supervisor
    supervisor.add_agent(scanner)
    supervisor.add_agent(crawler)
    supervisor.add_agent(interface)
    supervisor.add_agent(validator)
    supervisor.add_agent(adv_validator)
    supervisor.add_agent(rotator)
    supervisor.add_agent(admin)

    # Load Dynamic Plugins
    supervisor.load_plugins(store=store)

    # Start REPL
    start_repl(store, supervisor.agents, local_llm)

    logger.info("Starting Agentic LLM Proxy System (Hardened)...")
    
    try:
        await supervisor.start()
    except asyncio.CancelledError:
        await supervisor.stop()
        logger.info("System shutting down...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
