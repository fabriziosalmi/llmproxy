#!/usr/bin/env python3
"""Bootstrap Validator — Round 2: Corrected URLs + New Discoveries."""
import asyncio
import aiohttp
import json
import time

RESULTS = []

async def test(name, url, method, headers, payload, parser=None, timeout=30):
    print(f"\n{'='*60}\n {name}\n   {url}")
    start = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            kw = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=timeout)}
            if method == "POST":
                kw["json"] = payload
            async with s.request(method, url, **kw) as r:
                dt = round(time.time() - start, 2)
                if 'json' in (r.content_type or ''):
                    data = await r.json()
                elif 'text/event-stream' in (r.content_type or ''):
                    raw = await r.text()
                    data = {"raw_sse": raw[:1000]}
                else:
                    data = {"raw": (await r.text())[:500]}
                
                ans = ""
                if parser:
                    try: ans = parser(data)
                    except: ans = str(data)[:200]
                else:
                    try: ans = data["choices"][0]["message"]["content"][:200]
                    except: ans = str(data)[:200]
                
                ok = r.status == 200 and len(ans) > 3
                icon = "" if ok else ""
                print(f"   {icon} {r.status} | {dt}s | {ans[:120]}")
                RESULTS.append({"name": name, "status": r.status, "latency": dt, 
                               "success": ok, "preview": ans[:150],
                               "format": "openai" if "choices" in str(data) else "custom",
                               "url": url})
    except asyncio.TimeoutError:
        print(f"   ️ TIMEOUT")
        RESULTS.append({"name": name, "status": "TIMEOUT", "success": False})
    except Exception as e:
        print(f"    {e}")
        RESULTS.append({"name": name, "status": "ERROR", "success": False, "error": str(e)[:200]})

async def main():
    print(" Bootstrap Validator — Round 2 (Corrected)")
    print("="*60)
    
    OPENAI_BODY = {"model": "openai", "messages": [{"role": "user", "content": "Say hello in one sentence."}], "max_tokens": 60}
    H = {"Content-Type": "application/json"}

    # ─── 1. Pollinations.ai — OpenAI-compat endpoint ───
    await test("Pollinations.ai (OpenAI)", "https://text.pollinations.ai/openai",
               "POST", H, OPENAI_BODY)

    # ─── 2. Pollinations.ai — Simple text endpoint ───
    await test("Pollinations.ai (Simple)", "https://text.pollinations.ai/Say%20hello%20in%20one%20sentence",
               "GET", {}, None, parser=lambda d: d.get("raw", str(d)[:200]))

    # ─── 3. DuckDuckGo AI ─── 
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://duckduckgo.com/duckchat/v1/status",
                headers={"x-vqd-accept": "1", 
                         "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                         "Accept": "*/*",
                         "Referer": "https://duckduckgo.com/"}) as r:
                vqd = r.headers.get("x-vqd-4", "")
                print(f"\n   DuckDuckGo VQD: {' got token' if vqd else ' no token'} (status {r.status})")
                if vqd:
                    await test("DuckDuckGo AI Chat", "https://duckduckgo.com/duckchat/v1/chat",
                        "POST",
                        {"x-vqd-4": vqd, "Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                         "Accept": "text/event-stream",
                         "Referer": "https://duckduckgo.com/"},
                        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Say hi"}]},
                        parser=lambda d: d.get("raw_sse", str(d)[:300]))
                else:
                    RESULTS.append({"name": "DuckDuckGo AI", "status": "NO_VQD", "success": False})
    except Exception as e:
        RESULTS.append({"name": "DuckDuckGo AI", "status": "ERROR", "success": False, "error": str(e)[:200]})

    # ─── 4. HuggingFace Inference (free, serverless) ───
    await test("HuggingFace Inference (Free)", 
               "https://router.huggingface.co/hf-inference/models/microsoft/DialoGPT-medium/v1/chat/completions",
               "POST", H, 
               {"model": "microsoft/DialoGPT-medium", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 50})

    # ─── 5. HuggingFace (meta-llama) ───
    await test("HuggingFace Llama",
               "https://router.huggingface.co/hf-inference/models/meta-llama/Llama-3.2-1B-Instruct/v1/chat/completions",
               "POST", H,
               {"messages": [{"role": "user", "content": "Say hello"}], "max_tokens": 50})

    # ─── 6. Puter.js (unlimited free GPT) ───
    await test("Puter.js AI",
               "https://api.puter.com/drivers/call",
               "POST", H,
               {"interface": "puter-chat-completion", "driver": "openai-completion", 
                "method": "complete",
                "args": {"messages": [{"role": "user", "content": "Say hello in one sentence."}]}})
    
    # ─── 7. Blackbox AI (free, no login) ───
    await test("Blackbox AI",
               "https://www.blackbox.ai/api/chat",
               "POST", {**H, "User-Agent": "Mozilla/5.0"},
               {"messages": [{"role": "user", "content": "Say hello in one sentence."}],
                "previewToken": None, "codeModelMode": True, "agentMode": {},
                "trendingAgentMode": {}, "isMicMode": False},
               parser=lambda d: d.get("raw", str(d)[:300]))

    # ─── 8. DeepSeek (free tier) ───
    await test("DeepSeek Chat (Free)",
               "https://chat.deepseek.com/api/v0/chat/completions",
               "POST", {**H, "User-Agent": "Mozilla/5.0"},
               {"model": "deepseek_chat", "messages": [{"role": "user", "content": "Say hello"}],
                "stream": False})

    # ══════ SUMMARY ══════
    print("\n\n" + "="*60)
    print(" ROUND 2 SUMMARY")
    print("="*60)
    
    passed = [r for r in RESULTS if r.get("success")]
    failed = [r for r in RESULTS if not r.get("success")]
    
    print(f"\n WORKING ({len(passed)}):")
    for r in passed:
        print(f"    {r['name']} — {r.get('latency','?')}s — Format: {r.get('format','?')}")
        print(f"      URL: {r.get('url','')}")
    
    print(f"\n FAILED ({len(failed)}):")
    for r in failed:
        print(f"    {r['name']} — {r.get('status','?')}")
    
    with open("bootstrap_results_r2.json", "w") as f:
        json.dump(RESULTS, f, indent=2, default=str)
    print(f"\n Saved to bootstrap_results_r2.json")

if __name__ == "__main__":
    asyncio.run(main())
