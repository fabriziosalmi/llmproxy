from core.base_agent import BaseAgent
from core.adapter_engine import NetworkSniffer, AdapterSynthesis
from core.schema_mapper import SchemaMapper
from core.micro_prompts import MicroPrompts
from core.local_assistant import LocalAssistant
from core.logger import setup_logger
from core.pattern_memory import PatternMemory
from models import LLMEndpoint, EndpointStatus
from store.store import EndpointStore
import asyncio
import json

class SOTAInterfaceAgent(BaseAgent):
    """
    Advanced agent that autonomous navigates websites, sniffs network traffic,
    and synthesizes API adapters without relying on brittle DOM parsing.
    """
    
    def __init__(self, store: EndpointStore, local_llm: LocalAssistant):
        super().__init__("sota_interface", initial_state="IDLE")
        self.logger = setup_logger("sota_interface")
        self.store = store
        self.local_llm = local_llm
        self.sniffer = NetworkSniffer(headless=True)
        self.mapper = SchemaMapper()
        self.memory = PatternMemory()
        self.evasion_stats = {"success": 0, "failure": 0, "curve_complexity": 1.0}

    async def run(self):
        while True:
            self.logger.info("SOTA Interfacing cycle started...")
            # Pick endpoints that were FOUND but not yet analyzed
            targets = await self.store.get_by_status(EndpointStatus.FOUND)
            
            for target in targets:
                await self.execute_task(self.analyze_and_map, target)
            
            # Predictive Swapping: Check for expiring endpoints
            await self._predictive_swapping_tick()
            
            await asyncio.sleep(600)

    async def _predictive_swapping_tick(self):
        """Checks for endpoints whose headers are nearing expiration."""
        self.logger.info("Running Predictive Hot-Swapping check...")
        pool = await self.store.get_all()
        now = asyncio.get_event_loop().time()
        
        for endpoint in pool:
            ts = endpoint.metadata.get("captured_at", 0)
            ttl = 1800 # Assume 30 min TTL for demo
            if ts and (now - ts) > (ttl - 120): # 2 mins before expiry
                self.logger.info(f"Predictive Swapping: Endpoint {endpoint.url} nearing expiry. Triggering refresh.")
                await self.execute_task(self.analyze_and_map, endpoint)

    async def _apply_human_behavior(self, page):
        """Simulates self-evolving human-like behavior."""
        import random
        import math
        
        # 1. Advanced Evasion: Canvas Fingerprint Spoofing (via sniffer already)
        
        # 2. Genetic Path Evolution
        mod = self.evasion_stats["curve_complexity"]
        for i in range(random.randint(3, 6)):
            # Mutating sine-curves for unpredictable mouse movement
            x = 400 + 200 * math.sin(i * mod) + random.uniform(-50, 50)
            y = 300 + 150 * math.cos(i * mod) + random.uniform(-50, 50)
            await page.mouse.move(x, y, steps=random.randint(15, 25))
            await asyncio.sleep(random.uniform(0.1, 0.4))
            
        # 3. Adaptive Micro-Scroll Jitter
        scroll_depth = random.randint(100, 300)
        await page.evaluate(f"window.scrollBy(0, {scroll_depth})")
        await asyncio.sleep(random.uniform(0.2, 0.4))
        await page.evaluate(f"window.scrollBy(0, -{scroll_depth // 2})")

    async def analyze_and_map(self, endpoint: LLMEndpoint):
        self.logger.info(f"SOTA analysis for: {endpoint.url}")
        
        # 0. Predictive Leap: Can we recognize this site?
        # (Simplified: in a real run, we'd pass an HTML snippet)
        predicted = self.memory.predict(str(endpoint.url))
        if predicted:
            self.logger.info(f"10x PATTERN MATCH: Predicted adapter for {endpoint.url} from memory.")
            endpoint.metadata["adapter_template"] = predicted
            endpoint.status = EndpointStatus.DISCOVERED
            self.store.add_endpoint(endpoint)
            return

        # 1. Use Playwright to find the actual API call
        captured = await self.sniffer.sniff(str(endpoint.url))
        
        if captured:
            self.logger.info(f"Successfully captured {len(captured)} potential API calls.")
            best_capture = captured[0] # Pick the most promising one
            
            # 2. Synthesize Adapter Template
            template = AdapterSynthesis.create_template(best_capture)
            
            # 3. Create Validation Schema
            model = self.mapper.generate_model(f"API_{endpoint.id}", best_capture["payload"])
            
            # 3. Micro-Agent Signature Extraction (Autonomous Tier)
            self.evasion_stats["success"] += 1
            self.evasion_stats["curve_complexity"] = max(0.5, self.evasion_stats["curve_complexity"] * 0.9) # Simplify on success
            endpoint.metadata["captured_at"] = asyncio.get_event_loop().time()
            self.logger.info("Using Micro-Agent for autonomous signature extraction...")
            micro_prompt = MicroPrompts.extract_signature(json.dumps(best_capture['payload']))
            signature = await self.local_llm.consult(micro_prompt, task_id=endpoint.id, model="smollm-135m-instruct")
            self.logger.info(f"Micro-Signature: {signature}")
            endpoint.metadata["micro_signature"] = signature

            # 4. Local LLM Enrichment (High-Tier)
            endpoint.metadata["adapter_template"] = template
            endpoint.metadata["schema"] = model.schema()
            # 4. Local LLM Enrichment (Optional)
            self.logger.info("Consulting Local LLM (High-Tier) for adapter enrichment...")
            enrichment_prompt = f"Analyze this intercepted API payload and suggest the correct OpenAI-compatible mapping: {json.dumps(best_capture['payload'])}"
            # Trigger higher-tier model for complex schema discovery
            advice = await self.local_llm.consult(enrichment_prompt, task_id=endpoint.id, model="hermes-qwen4")
            if advice:
                self.logger.info(f"Local LLM Advice: {advice}")
                endpoint.metadata["adapter_advice"] = advice

            endpoint.status = EndpointStatus.DISCOVERED
            
            # Remember this pattern for 10x prediction next time
            self.memory.remember(str(endpoint.url), str(endpoint.url), template)
            
            self.logger.info(f"SOTA mapping complete for {endpoint.url}")
        else:
            self.evasion_stats["failure"] += 1
            self.evasion_stats["curve_complexity"] = min(5.0, self.evasion_stats["curve_complexity"] * 1.5) # Increase complexity on failure
            self.logger.warning(f"No API calls captured for {endpoint.url}. Triggering VISION FALLBACK...")
            # Vision Fallback: Take a screenshot and "see" the UI
            async with self.sniffer as sniffer:
                page = await sniffer.new_page()
                await page.goto(str(endpoint.url))
                screenshot_path = f"/tmp/vision_{endpoint.id}.png"
                await page.screenshot(path=screenshot_path)
                
                vision_prompt = "Find the chat input area in this UI and describe its likely API parameters. What is the placeholder text?"
                vision_advice = await self.local_llm.consult_vision(vision_prompt, screenshot_path, model="qwen3.5-4b-mlx")
                if vision_advice:
                    self.logger.info(f"Vision Discovery Result: {vision_advice}")
                    endpoint.metadata["vision_discovery"] = vision_advice
                    endpoint.status = EndpointStatus.DISCOVERED
                else:
                    endpoint.status = EndpointStatus.IGNORED
                    await self.store.add_endpoint(endpoint)
            
        await self.store.add_endpoint(endpoint)
