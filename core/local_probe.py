"""
Local OpenAI-compatible endpoint auto-discovery.

Probes well-known loopback/container-host addresses for Ollama, LM Studio,
vLLM, and LiteLLM at boot and registers any responding instance as an
``openai-compatible`` endpoint in the live config. Lets a developer who
already has Ollama or LM Studio running get a 200 OK from the proxy
without editing config.yaml or .env.

Enabled by default; disable with ``discovery.local_scan: false`` in
config.yaml or ``LLM_PROXY_LOCAL_DISCOVERY=0`` in the environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any

import aiohttp

logger = logging.getLogger("llmproxy.local_probe")


# Provider name → (port, probe path, models-json-extractor, adapter provider).
# The extractor takes the parsed JSON body and returns a list of model ids.
_PROBES: list[dict[str, Any]] = [
    {
        "name": "ollama",
        "port": 11434,
        "path": "/api/tags",
        "provider": "ollama",
        "extract": lambda j: [
            m.get("name") for m in (j.get("models") or []) if m.get("name")
        ],
    },
    {
        "name": "lmstudio",
        "port": 1234,
        "path": "/v1/models",
        "provider": "openai-compatible",
        "extract": lambda j: [
            m.get("id") for m in (j.get("data") or []) if m.get("id")
        ],
    },
    {
        "name": "vllm",
        "port": 8000,
        "path": "/v1/models",
        "provider": "openai-compatible",
        "extract": lambda j: [
            m.get("id") for m in (j.get("data") or []) if m.get("id")
        ],
    },
    {
        "name": "litellm",
        "port": 4000,
        "path": "/v1/models",
        "provider": "openai-compatible",
        "extract": lambda j: [
            m.get("id") for m in (j.get("data") or []) if m.get("id")
        ],
    },
]

# Probe hosts — try loopback + the Docker "host-gateway" alias so the proxy
# finds a local provider whether it runs in a container or on bare metal.
#   127.0.0.1             : bare-metal host
#   host.docker.internal  : Docker Desktop (macOS/Windows) + Linux when the
#                           container is launched with --add-host or the
#                           Compose 'host.docker.internal:host-gateway' hint.
_LOCAL_PROBE_HOSTS = ("127.0.0.1", "host.docker.internal")

# Remote peers worth probing come from the environment (comma-separated).
# Values may be either a bare ``host`` (probe all four standard ports) or
# ``host:port`` (probe that exact endpoint across all protocol signatures).
# Accepts IPs, DNS names, or Tailscale addresses.
#   LLM_PROXY_DISCOVERY_PEERS=100.98.112.23,100.118.189.6,nas.lan:8000
_PEERS_ENV = "LLM_PROXY_DISCOVERY_PEERS"

_DEFAULT_TIMEOUT_S = 1.5


def _host_resolves(host: str) -> bool:
    """Cheap DNS check — skip hosts that do not resolve to avoid long waits."""
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


async def _probe_one(
    session: aiohttp.ClientSession,
    host: str,
    probe: dict[str, Any],
    timeout: float,
    port_override: int | None = None,
) -> dict[str, Any] | None:
    port = port_override if port_override is not None else probe["port"]
    url = f"http://{host}:{port}{probe['path']}"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None

    models = []
    try:
        models = [m for m in probe["extract"](data) if m]
    except Exception:  # pragma: no cover - defensive against alien schemas
        return None
    if not models:
        return None

    # OpenAI-compatible inference URL. Ollama needs the /v1 suffix too
    # — its OpenAI-compatible API lives at /v1, not /api.
    base_url = f"http://{host}:{port}/v1"
    return {
        "name": probe["name"],
        "provider": probe["provider"],
        "base_url": base_url,
        "models": models,
        "host": host,
    }


def _is_enabled(config: dict[str, Any]) -> bool:
    env_val = os.environ.get("LLM_PROXY_LOCAL_DISCOVERY")
    if env_val is not None:
        return env_val.strip().lower() not in ("0", "false", "off", "no", "")
    return config.get("discovery", {}).get("local_scan", True)


def _url_already_configured(endpoints: dict[str, Any], base_url: str) -> bool:
    """True if the exact base_url is already registered (any id)."""
    wanted = base_url.rstrip("/")
    for ep in endpoints.values():
        if ep.get("base_url", "").rstrip("/") == wanted:
            return True
    return False


def _unique_id(endpoints: dict[str, Any], base: str) -> str:
    """Return an id that does not collide with existing endpoints.

    If the preferred id is taken (common case: config.yaml ships an
    ``ollama`` entry pointing at localhost that is unreachable from inside a
    container), we register the discovered endpoint as ``<name>-auto`` so
    both entries are visible and the user can decide which one wins.
    """
    if base not in endpoints:
        return base
    candidate = f"{base}-auto"
    i = 2
    while candidate in endpoints:
        candidate = f"{base}-auto{i}"
        i += 1
    return candidate


def _parse_peers(config: dict[str, Any]) -> list[tuple[str, int | None]]:
    """Parse peer targets from env or config.

    Accepts ``host`` (None port → try all standard ports) and ``host:port``
    (probe only that port but against all protocol signatures).
    """
    raw = os.environ.get(_PEERS_ENV, "").strip()
    if not raw:
        raw = ",".join(
            str(p) for p in (config.get("discovery") or {}).get("peers") or []
        )
    if not raw:
        return []

    peers: list[tuple[str, int | None]] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if ":" in token and not token.startswith("["):  # skip IPv6 literals for now
            host, _, port_s = token.rpartition(":")
            try:
                peers.append((host, int(port_s)))
            except ValueError:
                logger.warning("Ignoring malformed peer '%s' (bad port)", token)
        else:
            peers.append((token, None))
    return peers


async def discover_local_endpoints(
    config: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> list[str]:
    """Probe well-known local ports (and explicit remote peers) and inject
    responders into ``config['endpoints']``.

    Returns the list of newly registered endpoint ids. Idempotent: running
    twice on the same config adds nothing the second time.
    """
    if not _is_enabled(config):
        logger.debug("Local endpoint discovery disabled via config/env")
        return []

    endpoints = config.setdefault("endpoints", {})

    # Build the (host, port_override) target list. Local hosts always probe
    # all four standard ports; remote peers honour an explicit port if one
    # was given.
    targets: list[tuple[str, int | None]] = [
        (h, None) for h in _LOCAL_PROBE_HOSTS if _host_resolves(h)
    ]
    for host, port in _parse_peers(config):
        if _host_resolves(host):
            targets.append((host, port))
        else:
            logger.warning("Discovery peer '%s' does not resolve — skipping", host)

    if not targets:
        return []

    connector = aiohttp.TCPConnector(limit=32, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for host, port_override in targets:
            for probe in _PROBES:
                # When the peer pinned a port, only run the protocol signatures
                # against that port — skips the cross-product explosion.
                tasks.append(_probe_one(session, host, probe, timeout, port_override))
        results = await asyncio.gather(*tasks, return_exceptions=False)

    injected: list[str] = []
    seen_urls: set[str] = set()
    for hit in results:
        if not hit:
            continue
        url_key = hit["base_url"].rstrip("/")
        # Skip duplicates across probe hosts (127.0.0.1 vs host.docker.internal
        # typically resolve to the same service). Keep the first responder.
        if url_key in seen_urls or _url_already_configured(endpoints, hit["base_url"]):
            continue
        seen_urls.add(url_key)
        # For remote peers embed a stable host tag in the id so several
        # Tailscale nodes running LM Studio do not collide as
        # lmstudio-auto / lmstudio-auto2 / ... but as lmstudio-100-98-112-23.
        host_tag = hit.get("host", "")
        if host_tag in ("127.0.0.1", "host.docker.internal", ""):
            preferred = hit["name"]
        else:
            preferred = f"{hit['name']}-{host_tag.replace('.', '-').replace(':', '-')}"
        ep_id = _unique_id(endpoints, preferred)
        endpoints[ep_id] = {
            "provider": hit["provider"],
            "base_url": hit["base_url"],
            "models": hit["models"],
            "auth_type": "none",
            "_source": "auto-discovery",
        }
        injected.append(ep_id)
        logger.info(
            "Auto-discovered %s @ %s as '%s' (%d models): %s",
            hit["name"],
            hit["base_url"],
            ep_id,
            len(hit["models"]),
            ", ".join(hit["models"][:3]) + ("..." if len(hit["models"]) > 3 else ""),
        )
    return injected
