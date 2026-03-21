#!/usr/bin/env python3
"""Bootstrap Endpoint Validator — Tests Tier-1 providers one by one."""
import asyncio
import aiohttp
import json
import time

RESULTS = []

async def test_endpoint(name: str, url: str, method: str, headers: dict, payload: dict,
                         response_parser=None, timeout: int = 30):
    """Tests a single endpoint and records the result."""
    print(f"\n{'='*60}")
    print(f" Testing: {name}")
    print(f"   URL: {url}")

    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                duration = round(time.time() - start, 2)
                status = resp.status

                if resp.content_type and 'json' in resp.content_type:
                    data = await resp.json()
                else:
                    raw = await resp.text()
                    data = {"raw": raw[:500]}

                # Try to extract the actual response text
                answer = ""
                if response_parser:
                    try:
                        answer = response_parser(data)
                    except:
                        answer = str(data)[:200]
                else:
                    # Default: OpenAI-compat format
                    try:
                        answer = data["choices"][0]["message"]["content"][:200]
                    except:
                        answer = str(data)[:200]

                success = status == 200 and len(answer) > 5
                result = {
                    "name": name, "status": status, "latency": duration,
                    "success": success, "answer_preview": answer[:150],
                    "format": "openai-compat" if "choices" in str(data) else "custom"
                }

                icon = "" if success else ""
                print(f"   {icon} Status: {status} | Latency: {duration}s")
                print(f"   Response: {answer[:100]}...")
                RESULTS.append(result)
                return result

    except asyncio.TimeoutError:
        duration = round(time.time() - start, 2)
        print(f"   ️ TIMEOUT after {duration}s")
        RESULTS.append({"name": name, "status": "TIMEOUT", "latency": duration, "success": False})
    except Exception as e:
        duration = round(time.time() - start, 2)
        print(f"    ERROR: {e}")
        RESULTS.append({"name": name, "status": "ERROR", "latency": duration, "success": False, "error": str(e)})


async def main():
    print(" Bootstrap Endpoint Validator — Tier 1 Blitz")
    print("="*60)

    # ── 1. Pollinations.ai (Free, No Login, Has API) ──
    await test_endpoint(
        name="Pollinations.ai (Text)",
        url="https://text.pollinations.ai/",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "model": "openai",
            "seed": 42
        }
    )

    # ── 2. DuckDuckGo AI (Free, No Login, Needs SOTA) ──
    # Step 1: Get VQD token
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://duckduckgo.com/duckchat/v1/status",
                                    headers={"x-vqd-accept": "1",
                                             "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}) as resp:
                vqd = resp.headers.get("x-vqd-4", "")
                if vqd:
                    await test_endpoint(
                        name="DuckDuckGo AI Chat",
                        url="https://duckduckgo.com/duckchat/v1/chat",
                        method="POST",
                        headers={
                            "x-vqd-4": vqd,
                            "Content-Type": "application/json",
                            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                        },
                        payload={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": "Say hello in one sentence."}]
                        },
                        response_parser=lambda d: d.get("message", str(d)[:200])
                    )
                else:
                    print("\n DuckDuckGo: Could not get VQD token")
                    RESULTS.append({"name": "DuckDuckGo AI Chat", "status": "NO_VQD", "success": False})
    except Exception as e:
        print(f"\n DuckDuckGo: {e}")
        RESULTS.append({"name": "DuckDuckGo AI Chat", "status": "ERROR", "success": False, "error": str(e)})

    # ── 3. DeepAI (Free, No Login, Has API) ──
    await test_endpoint(
        name="DeepAI Text Generation",
        url="https://api.deepai.org/api/text-generator",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={"text": "Say hello in one sentence."},
        response_parser=lambda d: d.get("output", str(d)[:200])
    )

    # ── 4. TextSynth (Free tier, No Login) ──
    await test_endpoint(
        name="TextSynth",
        url="https://api.textsynth.com/v1/engines/llama2_7B/completions",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={"prompt": "Say hello:", "max_tokens": 50},
        response_parser=lambda d: d.get("text", str(d)[:200])
    )

    # ── 5. Brave Leo (via search, needs sniff) ──
    # Skipping for now - browser-embedded only

    # ── 6. Glhf.chat (OpenAI-compat, free) ──
    await test_endpoint(
        name="Glhf.chat",
        url="https://glhf.chat/api/openai/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={
            "model": "hf:meta-llama/Llama-3.3-70B-Instruct",
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "max_tokens": 50
        }
    )

    # ── 7. Chutes.ai (OpenAI-compat, free tier) ──
    await test_endpoint(
        name="Chutes.ai",
        url="https://chutes.ai/app/api/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={
            "model": "deepseek-ai/DeepSeek-V3-0324",
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "max_tokens": 50
        }
    )

    # ── 8. You.com (Research API, free) ──
    await test_endpoint(
        name="You.com Smart",
        url="https://api.ydc-index.io/search?query=hello",
        method="GET",
        headers={"Content-Type": "application/json"},
        payload={},
        response_parser=lambda d: str(d.get("hits", d.get("results", [])))[:200]
    )

    # ── 9. Prodia (T2I, free, no login) ──
    await test_endpoint(
        name="Prodia (T2I)",
        url="https://api.prodia.com/v1/sd/generate",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={"prompt": "a cute cat", "model": "v1-5-pruned-emaonly.safetensors"},
        response_parser=lambda d: d.get("job", d.get("imageUrl", str(d)[:200]))
    )

    # ── 10. Dezgo (T2I, free, no login) ──
    await test_endpoint(
        name="Dezgo (T2I)",
        url="https://api.dezgo.com/text2image",
        method="POST",
        headers={"Content-Type": "application/json"},
        payload={"prompt": "a cute cat", "model": "sdxl"},
        response_parser=lambda d: "image_received" if d else "no_response"
    )

    # ══════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════
    print("\n\n" + "="*60)
    print(" VALIDATION SUMMARY")
    print("="*60)

    passed = [r for r in RESULTS if r.get("success")]
    failed = [r for r in RESULTS if not r.get("success")]

    print(f"\n PASSED: {len(passed)}/{len(RESULTS)}")
    for r in passed:
        print(f"    {r['name']} — {r.get('latency', '?')}s — {r.get('format', 'unknown')}")

    print(f"\n FAILED: {len(failed)}/{len(RESULTS)}")
    for r in failed:
        print(f"    {r['name']} — {r.get('status', '?')} — {r.get('error', '')[:80]}")

    # Save results
    with open("bootstrap_results.json", "w") as f:
        json.dump(RESULTS, f, indent=2, default=str)
    print("\n Results saved to bootstrap_results.json")

if __name__ == "__main__":
    asyncio.run(main())
