import asyncio
import logging
from core.adapter_engine import NetworkSniffer, AdapterSynthesis
from core.schema_mapper import SchemaMapper

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sota_demo")

async def demo_sota_sniffing(target_url: str):
    logger.info(f"DEMO: Starting SOTA Sniffing for {target_url}...")

    # 1. Initialize SOTA Components
    sniffer = NetworkSniffer(headless=True)
    mapper = SchemaMapper()

    # 2. Sniff Network Traffic (Intercepting XHR/Fetch)
    # This simulates navigating to a chatbot page and looking for API calls
    captured = await sniffer.sniff(target_url, duration=5)

    if not captured:
        logger.warning("No potential LLM APIs intercepted.")
        return

    logger.info(f"Captured {len(captured)} potential API signature(s).")

    for i, call in enumerate(captured):
        logger.info(f"--- MATCH {i+1} ---")
        logger.info(f"URL: {call['url']}")
        logger.info(f"Headers Sample: {list(call['headers'].keys())[:5]}")
        logger.info(f"Payload Sample: {json.dumps(call['payload'], indent=2)}")

        # 3. Synthesize Adapter and Model
        template = AdapterSynthesis.create_template(call)
        model = mapper.generate_model(f"SOTA_Model_{i}", call["payload"])

        logger.info(f"Synthesized Template URL: {template['endpoint']}")
        logger.info(f"Generated Pydantic Schema Fields: {list(model.__fields__.keys())}")

if __name__ == "__main__":
    # Example target (ideally a chatbot or interface site)
    # NOTE: This requires playwrite to be installed and browsers downloaded
    # pip install playwright && playwright install
    asyncio.run(demo_sota_sniffing("https://huggingface.co/spaces/HuggingFaceH4/zephyr-7b-beta"))
