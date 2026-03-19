import asyncio
import json
import logging
from typing import Dict, List, Optional, Any
from playwright.async_api import async_playwright, Request, Response

logger = logging.getLogger(__name__)

class NetworkSniffer:
    """Uses Playwright to intercept and analyze network traffic for LLM API patterns."""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.intercepted_requests: List[Dict[str, Any]] = []

    async def sniff(self, url: str, duration: int = 15) -> List[Dict[str, Any]]:
        self.intercepted_requests = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()

            # Listen for all requests
            page.on("request", self._handle_request)
            
            logger.info(f"Navigating to {url} and sniffing for {duration}s...")
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                # Wait for potential user interaction or async loads
                await asyncio.sleep(duration)
            except Exception as e:
                logger.error(f"Sniffing navigation failed: {e}")
            finally:
                await browser.close()
        
        return self.intercepted_requests

    def _handle_request(self, request: Request):
        # We look for POST/PUT requests with JSON bodies
        if request.method in ["POST", "PUT"] and ("/api" in request.url or "llm" in request.url or "chat" in request.url):
            try:
                payload = request.post_data_json
                if payload and self._is_likely_llm_payload(payload):
                    self.intercepted_requests.append({
                        "url": request.url,
                        "method": request.method,
                        "headers": request.headers,
                        "payload": payload,
                        "is_xhr": request.resource_type in ["xhr", "fetch"]
                    })
                    logger.info(f"Intercepted potential LLM API: {request.url}")
            except Exception:
                pass

    def _is_likely_llm_payload(self, payload: Any) -> bool:
        """Heuristic to check if a JSON payload looks like an LLM request."""
        if not isinstance(payload, dict):
            return False
        
        # Look for common LLM API keys
        keys = str(payload.keys()).lower()
        indicators = ["prompt", "message", "model", "input", "query", "text"]
        return any(ind in keys for ind in indicators)

class AdapterSynthesis:
    """Synthesizes a reusable adapter from intercepted network traffic."""
    
    @staticmethod
    def create_template(intercepted: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a template for replaying the request."""
        # Strip session-specific headers but keep essential ones
        essential_headers = {
            k: v for k, v in intercepted["headers"].items()
            if k.lower() in ["content-type", "authorization", "x-api-key"]
        }
        
        return {
            "endpoint": intercepted["url"],
            "method": intercepted["method"],
            "base_headers": essential_headers,
            "payload_template": intercepted["payload"] # Needs mapping logic
        }
