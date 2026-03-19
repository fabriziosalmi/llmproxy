from core.base_agent import BaseAgent
from core.adapter_engine import NetworkSniffer, AdapterSynthesis
from core.schema_mapper import SchemaMapper
from models import LLMEndpoint, EndpointStatus
from store.store import EndpointStore
import asyncio

class SOTAInterfaceAgent(BaseAgent):
    """
    Advanced agent that autonomous navigates websites, sniffs network traffic,
    and synthesizes API adapters without relying on brittle DOM parsing.
    """
    
    def __init__(self, store: EndpointStore):
        super().__init__("sota_interface", initial_state="IDLE")
        self.store = store
        self.sniffer = NetworkSniffer(headless=True)
        self.mapper = SchemaMapper()

    async def run(self):
        while True:
            self.logger.info("SOTA Interfacing cycle started...")
            # Pick endpoints that were FOUND but not yet analyzed
            targets = self.store.get_by_status(EndpointStatus.FOUND)
            
            for target in targets:
                await self.execute_task(self.analyze_and_map, target)
            
            await asyncio.sleep(600)

    async def analyze_and_map(self, endpoint: LLMEndpoint):
        self.logger.info(f"SOTA Sniffing endpoint: {endpoint.url}")
        
        # 1. Use Playwright to find the actual API call
        captured = await self.sniffer.sniff(str(endpoint.url))
        
        if captured:
            self.logger.info(f"Successfully captured {len(captured)} potential API calls.")
            best_capture = captured[0] # Pick the most promising one
            
            # 2. Synthesize Adapter Template
            template = AdapterSynthesis.create_template(best_capture)
            
            # 3. Create Validation Schema
            model = self.mapper.generate_model(f"API_{endpoint.id}", best_capture["payload"])
            
            # 4. Update Endpoint Metadata
            endpoint.metadata["adapter_template"] = template
            endpoint.metadata["schema"] = model.schema()
            # 4. Local LLM Enrichment (Optional)
            self.logger.info("Consulting Local LLM for adapter enrichment...")
            enrichment_prompt = f"Analyze this intercepted API payload and suggest the correct OpenAI-compatible mapping: {json.dumps(best_capture['payload'])}"
            advice = await self.local_llm.consult(enrichment_prompt, task_id=endpoint.id)
            if advice:
                self.logger.info(f"Local LLM Advice: {advice}")
                endpoint.metadata["adapter_advice"] = advice

            endpoint.status = EndpointStatus.DISCOVERED
            
            self.logger.info(f"SOTA mapping complete for {endpoint.url}")
        else:
            self.logger.warning(f"No API calls captured for {endpoint.url}. Marking as ignored.")
            endpoint.status = EndpointStatus.IGNORED
            
        self.store.add_endpoint(endpoint)
