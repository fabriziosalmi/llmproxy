from core.base_agent import BaseAgent
from core.fsm import State
from models import LLMEndpoint, EndpointStatus
from store.store import EndpointStore
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import uuid

class ScannerAgent(BaseAgent):
    def __init__(self, store: EndpointStore):
        super().__init__("scanner", initial_state="IDLE")
        self.store = store

    def _setup_fsm(self):
        super()._setup_fsm()
        self.fsm.add_state(State("SCANNING"))
        self.fsm.add_state(State("PARSING"))
        self.fsm.add_transition("IDLE", "scan", "SCANNING")
        self.fsm.add_transition("SCANNING", "parse", "PARSING")
        self.fsm.add_transition("PARSING", "finish", "IDLE")

    async def run(self):
        """Main loop for scanning."""
        while True:
            await self.fsm.trigger("scan")
            self.logger.info("Scanning for new endpoints...")
            targets = ["https://github.com/working-with-llms", "https://huggingface.co/spaces"]
            
            for target in targets:
                await self.execute_task(self.scan_target, target)
            
            await self.fsm.trigger("finish")
            await asyncio.sleep(3600)

    async def scan_target(self, url: str):
        self.logger.info(f"Scanning target: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    html = await response.text()
                    await self.parse_html(html, url)

    async def parse_html(self, html: str, source_url: str):
        soup = BeautifulSoup(html, 'html.parser')
        # Logic to find potential API endpoints in HTML (links, scripts, etc.)
        # This is a placeholder for actual extraction logic
        links = soup.find_all('a', href=True)
        for link in links:
            href = link['href']
            if "api" in href or "llm" in href:
                endpoint = LLMEndpoint(
                    id=str(uuid.uuid4()),
                    url=href if href.startswith("http") else f"{source_url}{href}",
                    status=EndpointStatus.FOUND,
                    metadata={"found_at": source_url}
                )
                self.store.add_endpoint(endpoint)
                self.logger.info(f"Discovered potential endpoint: {endpoint.url}")
