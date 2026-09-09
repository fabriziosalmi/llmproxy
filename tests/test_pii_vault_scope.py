"""PII placeholders must not be resolvable outside the request that minted them.

The vault used to live on the SecurityShield instance — one TTLCache of 10,000
entries shared by every concurrent caller — and demask_pii walked all of it
against every response. Two consequences, both fixed here:

  * one caller's placeholder was resolvable inside another caller's response,
    with 32 bits of truncated uuid4 as the only thing making it unlikely;
  * the cost of demasking one response grew with how much PII every *other*
    caller had sent in the last hour.

The optional ONNX masker was worse than unlikely: its placeholders are
[GROUP_N] with N restarting at 1 per request, so a shared vault collided by
construction rather than by chance.
"""

import time

import pytest

from core.security import SecurityShield


def _shield() -> SecurityShield:
    return SecurityShield({"security": {"enabled": True}})


def test_a_placeholder_from_another_request_is_not_restored():
    """The leak, stated directly: B must not receive A's email."""
    shield = _shield()

    vault_a: dict = {}
    masked_a = shield.mask_pii("contact me at alice@example.com", vault=vault_a)
    assert "alice@example.com" not in masked_a
    token = next(iter(vault_a))

    # B never masked anything, but its response happens to carry A's token.
    vault_b: dict = {}
    restored_b = shield.demask_pii(f"the address is {token}", vault=vault_b)

    assert "alice@example.com" not in restored_b, (
        "a token minted by another request was resolved into this response"
    )
    assert token in restored_b, "an unknown token should be left untouched"


def test_the_minting_request_still_gets_its_own_value_back():
    """Scoping must not break the feature it is scoping."""
    shield = _shield()
    vault: dict = {}

    masked = shield.mask_pii("write to bob@example.com please", vault=vault)
    assert "bob@example.com" not in masked

    assert shield.demask_pii(masked, vault=vault) == "write to bob@example.com please"


def test_placeholders_carry_the_full_uuid():
    """Truncating to 8 hex characters gave ~1.2% collision odds at capacity."""
    shield = _shield()
    vault: dict = {}
    shield.mask_pii("mail carol@example.com", vault=vault)

    token = next(iter(vault))
    # [PII_<LABEL>_<hex>] — the hex run is the entropy that separates callers.
    hex_part = token.rstrip("]").rsplit("_", 1)[1]
    assert len(hex_part) == 32, f"expected a full uuid4 hex, got {len(hex_part)} chars"


def test_two_requests_masking_the_same_value_get_independent_vaults():
    """Nothing accumulates in a shared store as a side effect of masking."""
    shield = _shield()

    vault_a: dict = {}
    vault_b: dict = {}
    shield.mask_pii("dave@example.com", vault=vault_a)
    shield.mask_pii("dave@example.com", vault=vault_b)

    assert len(vault_a) == 1 and len(vault_b) == 1
    assert set(vault_a) != set(vault_b), "distinct requests must mint distinct tokens"
    # The process-wide vault is untouched by the request path.
    assert len(shield.pii_vault) == 0


def test_demasking_cost_does_not_grow_with_other_callers_traffic():
    """The 15 ms-per-response figure was the shared vault being walked.

    Not a benchmark — an order-of-magnitude guard. A per-request vault holds a
    handful of entries whatever the rest of the process is doing, so demasking
    a response with an empty request vault must not degrade as the shield's
    own store fills up.
    """
    shield = _shield()
    response = "the quick brown fox jumps over the lazy dog. " * 220  # ~10 KB

    for i in range(10_000):
        shield.pii_vault[f"[PII_EMAIL_{i:032x}]"] = "someone@example.com"

    started = time.perf_counter()
    for _ in range(20):
        shield.demask_pii(response, vault={})
    elapsed_ms = (time.perf_counter() - started) / 20 * 1000

    assert elapsed_ms < 1.0, (
        f"demasking took {elapsed_ms:.2f} ms with an empty request vault — "
        "the process-wide store is being walked again"
    )


@pytest.mark.asyncio
async def test_the_masker_plugin_keeps_its_vault_on_the_context():
    """pii_masker and shield_sanitizer must meet on the same per-request dict."""
    from core.plugin_engine import PluginContext
    from plugins.default.pii_masker import mask

    class _Rotator:
        def __init__(self, shield):
            self.security = shield

        async def _add_log(self, *_a, **_kw):
            return None

    shield = _shield()
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "ping erin@example.com"}]},
        metadata={"rotator": _Rotator(shield)},
    )

    await mask(ctx)

    vault = ctx.metadata.get("_pii_vault")
    assert vault, "the masker must record its placeholders on the context"
    assert "erin@example.com" not in ctx.body["messages"][0]["content"]
    assert "erin@example.com" in vault.values()
    # And nothing leaked into the shared store on the way through.
    assert len(shield.pii_vault) == 0
