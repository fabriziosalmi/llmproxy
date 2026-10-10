"""Run every sample through llmproxy's own byte firewall and SecurityShield.

    python scripts/waf_eval/predict_waf.py <work-dir>      # from the repository root, in its venv

Uses the real classes with the default configuration and the shipped
signatures, exactly as the proxy loads them. Writes preds_waf.jsonl.
"""
import asyncio
import json
import logging
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.firewall_asgi import ByteLevelFirewallMiddleware  # noqa: E402
from core.security import SecurityShield  # noqa: E402
from core.signature_loader import SignatureStore  # noqa: E402

SHIELD_CONFIG = {"injection_guard": {"enabled": True}, "language_guard": {"enabled": True}}


async def _app(scope, receive, send):
    return None


def make_firewall() -> ByteLevelFirewallMiddleware:
    store = SignatureStore(signatures_path=str(ROOT / "data" / "signatures.yaml"))
    if not store.load():
        raise SystemExit("data/signatures.yaml did not load")
    return ByteLevelFirewallMiddleware(_app, signature_store=store)


async def verdict(firewall: ByteLevelFirewallMiddleware, messages: list, session: str) -> dict:
    """What the proxy does with this request: the firewall on the body bytes,
    then a shield with no memory of other prompts."""
    body = {"model": "gpt-4o", "messages": messages}
    fw_hit = bool(firewall._inspect(json.dumps(body, ensure_ascii=False).encode())[1][0])
    shield = SecurityShield(SHIELD_CONFIG, assistant=None)
    reason = await shield.inspect(body, session)
    return {"fw": fw_hit, "shield": bool(reason), "reason": (reason or "")[:80]}


async def main(work: str) -> None:
    # Thousands of blocked prompts would each log a line. Only when run as a
    # script: this module is also imported by the test suite.
    logging.disable(logging.CRITICAL)
    firewall = make_firewall()
    n, started = 0, time.time()
    with open(f"{work}/samples.jsonl") as f, open(f"{work}/preds_waf.jsonl", "w") as out:
        for line in f:
            s = json.loads(line)
            t0 = time.perf_counter()
            v = await verdict(firewall, s["messages"], f"s{n:06d}-abcdefghij")
            v.update(id=s["id"], ms=round((time.perf_counter() - t0) * 1000, 2))
            out.write(json.dumps(v, ensure_ascii=False) + "\n")
            n += 1
    print("done", n, round(time.time() - started), "s")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
