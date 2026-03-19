#!/usr/bin/env python3
"""Round 3: DuckDuckGo via Playwright + more free APIs."""
import asyncio
import aiohttp
import json
import time

RESULTS = []

async def test(name, url, method, headers, payload, parser=None, timeout=30):
    print(f"\n{'='*60}\n🔍 {name}\n   {url}")
    start = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            kw = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=timeout)}
            if method == "POST": kw["json"] = payload
            async with s.request(method, url, **kw) as r:
                dt = round(time.time() - start, 2)
                raw = await r.text()
                try: data = json.loads(raw)
                except: data = {"raw": raw[:1000]}
                ans = parser(data) if parser else str(data)[:200]
                try: ans = data["choices"][0]["message"]["content"][:200]
                except: pass
                ok = r.status == 200 and len(str(ans)) > 3
                icon = "✅" if ok else "❌"
                print(f"   {icon} {r.status} | {dt}s")
                print(f"   {str(ans)[:150]}")
                RESULTS.append({"name": name, "status": r.status, "latency": dt, "success": ok,
                               "preview": str(ans)[:150], "url": url})
    except Exception as e:
        print(f"   💥 {e}")
        RESULTS.append({"name": name, "status": "ERROR", "success": False, "error": str(e)[:200]})

async def test_duckduckgo():
    """Test DuckDuckGo AI via proper session with cookies."""
    print(f"\n{'='*60}\n🔍 DuckDuckGo AI (Full Session)")
    start = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            # Step 1: Visit duck.ai to get session cookies
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }
            async with s.get("https://duckduckgo.com/?q=DuckDuckGo+AI+Chat&ia=chat&duckai=1", headers=headers) as r:
                print(f"   Page: {r.status}")
            
            # Step 2: Get VQD token
            vqd_headers = {
                **headers,
                "x-vqd-accept": "1",
                "Referer": "https://duckduckgo.com/",
                "Origin": "https://duckduckgo.com",
                "Accept": "*/*",
            }
            async with s.get("https://duckduckgo.com/duckchat/v1/status", headers=vqd_headers) as r:
                vqd = r.headers.get("x-vqd-4", "")
                print(f"   VQD: {'✅ ' + vqd[:20] + '...' if vqd else '❌ no token'} (status {r.status})")
            
            if vqd:
                # Step 3: Chat
                chat_headers = {
                    **headers,
                    "x-vqd-4": vqd,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Referer": "https://duckduckgo.com/",
                    "Origin": "https://duckduckgo.com",
                }
                body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Say hello in one sentence."}]}
                async with s.post("https://duckduckgo.com/duckchat/v1/chat", headers=chat_headers, json=body) as r:
                    dt = round(time.time() - start, 2)
                    raw = await r.text()
                    # Parse SSE: collect "message" fields
                    answer_parts = []
                    for line in raw.split("\n"):
                        if line.startswith("data: "):
                            try:
                                chunk = json.loads(line[6:])
                                if "message" in chunk:
                                    answer_parts.append(chunk["message"])
                            except: pass
                    answer = "".join(answer_parts)
                    ok = r.status == 200 and len(answer) > 3
                    icon = "✅" if ok else "❌"
                    print(f"   {icon} {r.status} | {dt}s")
                    print(f"   Response: {answer[:150]}")
                    RESULTS.append({"name": "DuckDuckGo AI Chat", "status": r.status, "latency": dt,
                                   "success": ok, "preview": answer[:150], "url": "duckduckgo.com/duckchat"})
            else:
                RESULTS.append({"name": "DuckDuckGo AI Chat", "status": "NO_VQD", "success": False})
    except Exception as e:
        print(f"   💥 {e}")
        RESULTS.append({"name": "DuckDuckGo AI Chat", "status": "ERROR", "success": False, "error": str(e)[:200]})

async def main():
    print("🚀 Bootstrap Validator — Round 3")
    print("="*60)
    H = {"Content-Type": "application/json"}

    # ─── 1. DuckDuckGo (full session) ───
    await test_duckduckgo()

    # ─── 2. Blackbox AI (corrected) ───
    await test("Blackbox AI", "https://www.blackbox.ai/api/chat",
               "POST", {**H, "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
               {"messages": [{"role": "user", "content": "Say hello in one sentence."}],
                "previewToken": None, "codeModelMode": True, "agentMode": {},
                "trendingAgentMode": {}, "isMicMode": False, "maxTokens": 100},
               parser=lambda d: d.get("raw", str(d)[:200]))

    # ─── 3. Phind (free, no login in browser) ───
    await test("Phind Search", "https://https.api.phind.com/infer/",
               "POST", {**H, "User-Agent": "Mozilla/5.0"},
               {"question": "Say hello in one sentence", "options": {"date": "", "language": "en", "detailed": False, "areaOfInterest": ""},
                "context": []},
               parser=lambda d: d.get("raw", str(d)[:200]))

    # ─── 4. Pollinations models list ───
    await test("Pollinations Models", "https://text.pollinations.ai/models",
               "GET", {}, None,
               parser=lambda d: json.dumps([m.get("name","") for m in d[:5]] if isinstance(d, list) else d)[:200])

    # ─── 5. Pollinations with specific model ───
    await test("Pollinations (Gemini)", "https://text.pollinations.ai/openai",
               "POST", H,
               {"model": "gemini", "messages": [{"role": "user", "content": "Say hello in one sentence."}]})

    # ─── 6. Pollinations (Claude) ───
    await test("Pollinations (Claude)", "https://text.pollinations.ai/openai",
               "POST", H,
               {"model": "claude", "messages": [{"role": "user", "content": "Say hello in one sentence."}]})

    # ─── 7. Pollinations (Llama) ───
    await test("Pollinations (Llama)", "https://text.pollinations.ai/openai",
               "POST", H,
               {"model": "llama", "messages": [{"role": "user", "content": "Say hello in one sentence."}]})

    # ─── 8. Pollinations (Mistral) ───
    await test("Pollinations (Mistral)", "https://text.pollinations.ai/openai",
               "POST", H,
               {"model": "mistral", "messages": [{"role": "user", "content": "Say hello in one sentence."}]})

    # ─── 9. Pollinations (DeepSeek) ───
    await test("Pollinations (DeepSeek)", "https://text.pollinations.ai/openai",
               "POST", H,
               {"model": "deepseek", "messages": [{"role": "user", "content": "Say hello in one sentence."}]})

    # ══════ SUMMARY ══════
    print("\n\n" + "="*60)
    print("📊 ROUND 3 SUMMARY")
    print("="*60)
    passed = [r for r in RESULTS if r.get("success")]
    failed = [r for r in RESULTS if not r.get("success")]
    print(f"\n✅ WORKING ({len(passed)}):")
    for r in passed: print(f"   ✅ {r['name']} — {r.get('latency','?')}s")
    print(f"\n❌ FAILED ({len(failed)}):")
    for r in failed: print(f"   ❌ {r['name']} — {r.get('status','?')}")
    
    with open("bootstrap_results_r3.json", "w") as f:
        json.dump(RESULTS, f, indent=2, default=str)

if __name__ == "__main__":
    asyncio.run(main())
