import asyncio
import aiohttp
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("integrated_test")

async def test_semantic_cache():
    """Verifies that repeat semantic requests are served from cache."""
    url = "http://localhost:8000/v1/chat/completions"
    headers = {"Authorization": "Bearer sk-test-key", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "What is the capital of France?"}]
    }

    async with aiohttp.ClientSession() as session:
        # First request (will go to upstream)
        logger.info("Test: Performing first semantic request...")
        async with session.post(url, json=payload, headers=headers) as resp:
            data1 = await resp.json()
            logger.info(f"Response 1: {data1.get('choices',[{}])[0].get('message',{}).get('content')[:30]}...")

        # Wait for cache processing
        await asyncio.sleep(2)

        # Second request (should be CACHE HIT)
        logger.info("Test: Performing second semantic request (cache check)...")
        async with session.post(url, json=payload, headers=headers) as resp:
            data2 = await resp.json()
            logger.info(f"Response 2: {data2.get('choices',[{}])[0].get('message',{}).get('content')[:30]}...")

    return data1 == data2

async def test_mcp_tools():
    """Verifies that the proxy can execute local tools via MCP."""
    url = "http://localhost:8000/v1/chat/completions"
    headers = {"Authorization": "Bearer sk-test-key", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "List the files in the current directory using your local tools."}]
    }

    async with aiohttp.ClientSession() as session:
        logger.info("Test: Performing MCP tool request...")
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            content = data.get('choices',[{}])[0].get('message',{}).get('content', "")
            logger.info(f"MCP Response: {content[:100]}...")
            return "core" in content or "main.py" in content

async def main():
    logger.info("Starting Integrated System Verification...")

    # Note: This assumes the proxy is already running on port 8000
    try:
        cache_ok = await test_semantic_cache()
        logger.info(f"Semantic Cache Test: {'PASSED' if cache_ok else 'FAILED'}")

        mcp_ok = await test_mcp_tools()
        logger.info(f"MCP Tools Test: {'PASSED' if mcp_ok else 'FAILED'}")

    except Exception as e:
        logger.error(f"Test Execution Failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
