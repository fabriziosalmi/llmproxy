import asyncio
import logging
from store.store import EndpointStore
from agents.scanner import ScannerAgent
from agents.crawler import CrawlerAgent
from agents.sota_interface_agent import SOTAInterfaceAgent
from agents.validator import ValidatorAgent
from agents.advanced_validator import AdvancedValidatorAgent
from proxy.rotator import RotatorAgent
from core.metrics import start_metrics_server
from core.supervisor import AgentSupervisor
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

    # Initialize store
    store = EndpointStore()
    
    # Initialize supervisor
    supervisor = AgentSupervisor()
    
    # Initialize agents
    scanner = ScannerAgent(store)
    crawler = CrawlerAgent()
    interface = SOTAInterfaceAgent(store)
    validator = ValidatorAgent(store)
    adv_validator = AdvancedValidatorAgent(store)
    rotator = RotatorAgent(store)
    
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

    # Start REPL
    start_repl(store, supervisor.agents)

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
