"""P1-3: RequestForwarder must observe config hot-reload.

Bug: RequestForwarder.__init__ captured `self.config = config` at startup.
The config_watch_loop reloads via `agent.config = agent._load_config()`,
which REBINDS the attribute to a new dict. The forwarder's snapshot ref
still points at the OLD dict, so hot-reloaded endpoints + fallback chains
silently don't apply to the request path.

Fix: forwarder takes a `config_provider` callable and reads config lazily
on each request. The orchestrator passes `lambda: self.config` so the
provider always returns the live agent config.
"""

from proxy.forwarder import RequestForwarder


def test_resolve_endpoint_observes_rebound_config():
    """Demonstrates the stale-config bug + its fix.

    Before P1-3: forwarder constructed with `config=initial_dict` would never
    see new endpoints added via attribute rebind on the agent. The fix is to
    pass `config_provider=lambda: agent.config` and read it lazily.
    """
    initial_config = {"endpoints": {}}

    # Simulated agent: its `config` attribute will be rebound to a NEW dict.
    class StubAgent:
        def __init__(self, cfg):
            self.config = cfg

    agent = StubAgent(initial_config)

    # Pass a provider closure (the supported pattern post-P1-3).
    forwarder = RequestForwarder(config_provider=lambda: agent.config)

    # Initially: no endpoints configured.
    assert forwarder.resolve_endpoint_for_provider("openai") is None

    # Operator edits config.yaml → watcher rebinds agent.config to new dict.
    new_config = {
        "endpoints": {
            "openai-prod": {"provider": "openai", "base_url": "https://api.openai.com"},
        }
    }
    agent.config = new_config

    # Forwarder MUST observe the new endpoint (this fails pre-P1-3 because
    # forwarder.config still points at the original dict).
    resolved = forwarder.resolve_endpoint_for_provider("openai")
    assert resolved is not None, "forwarder did not see hot-reloaded endpoint"
    assert resolved.url == "https://api.openai.com"


def test_fallback_chain_observes_rebound_config():
    """Same staleness for fallback_chains lookups."""
    agent_config = {"endpoints": {}, "fallback_chains": {}}

    class StubAgent:
        def __init__(self, cfg):
            self.config = cfg

    agent = StubAgent(agent_config)
    forwarder = RequestForwarder(config_provider=lambda: agent.config)

    # No fallback initially.
    config_seen = forwarder._live_config()
    assert config_seen.get("fallback_chains", {}).get("gpt-4", []) == []

    # Reload introduces a fallback chain.
    agent.config = {
        "endpoints": {},
        "fallback_chains": {
            "gpt-4": [{"provider": "anthropic", "model": "claude-3-5"}]
        },
    }

    config_seen = forwarder._live_config()
    assert config_seen["fallback_chains"]["gpt-4"][0]["provider"] == "anthropic"


def test_legacy_static_config_still_works():
    """Back-compat: existing callers that pass `config=dict` continue to
    function. They just don't get hot-reload (no provider was wired)."""
    static = {
        "endpoints": {
            "local": {"provider": "ollama", "base_url": "http://localhost:11434"}
        }
    }
    forwarder = RequestForwarder(config=static)
    resolved = forwarder.resolve_endpoint_for_provider("ollama")
    assert resolved is not None
    assert resolved.url == "http://localhost:11434"


def test_provider_takes_precedence_over_static_config():
    """If both `config` and `config_provider` are passed, the provider wins
    — it's the live source of truth."""
    static = {"endpoints": {"stale": {"provider": "x", "base_url": "stale-url"}}}
    live = {"endpoints": {"fresh": {"provider": "x", "base_url": "fresh-url"}}}

    forwarder = RequestForwarder(config=static, config_provider=lambda: live)
    resolved = forwarder.resolve_endpoint_for_provider("x")
    assert resolved is not None
    assert resolved.url == "fresh-url"
