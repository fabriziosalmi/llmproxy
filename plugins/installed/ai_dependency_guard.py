"""
AI Dependency Guard — slopsquatting defense for LLM responses.

A post_flight port of ai-dependency-guard (github.com/fabriziosalmi/ai-dependency-guard,
MIT), adapted from CI manifest scanning to live LLM-output inspection.

The attack it defends against ("slopsquatting"): an LLM confidently recommends
`pip install <package>` or `npm install <package>` for a package that does not
exist. Attackers watch model output for these hallucinated names, register them
on PyPI/npm, and ship malware — so a user who copy-pastes the model's suggestion
installs the attacker's package. A 2024 study found ~20% of LLM-suggested
packages across popular models were hallucinated; the same fake name recurs
across generations, which is exactly what makes pre-registration profitable.

Two-tier detection (mirrors the upstream tool):
  Tier 1 — blocklist: names in a configured denylist are flagged offline, before
           any network call. This catches squatted/hijacked names that DO resolve
           to HTTP 200 (Tier 2 would wave them through). Empty by default —
           populate it per-deployment, exactly like the upstream `--blocklist`.
  Tier 2 — registry existence: remaining names are checked against the official
           registry (PyPI JSON API / npm registry). A strict HTTP 404 → the
           package does not exist → hallucination. HTTP 200, any 5xx, a timeout,
           or any transport error → assume it exists (FAIL-OPEN), exactly as
           upstream: availability over strict validation.

Where package names come from (an LLM response is free text, not a manifest, so
we extract only high-signal references to keep false positives near zero):
  - install commands:  pip install X,  pip3 install X,  python -m pip install X,
                       npm install/i/add X,  yarn add X,  pnpm add X
  - pinned requirement lines:  name==1.2.3 / name>=1.0 (a version specifier is
                       required, so bare prose words are never treated as packages)

Deterministic and fail-open: any parsing or network error leaves the response
intact. The plugin never mutates the LLM output — it enriches metadata (and
optionally BLOCKS the response when block_on_hallucination is set).
"""
import asyncio
import json
import re
import time
from typing import Any, Optional

from core.plugin_sdk import BasePlugin, PluginHook, PluginResponse
from core.plugin_engine import PluginContext

# ── Package reference extraction ─────────────────────────────────────────────
# pip / python -m pip install <args...>  (args captured up to a shell break)
_PIP_RE = re.compile(
    r"(?:\bpip3?|\bpython3?\s+-m\s+pip)\s+install\s+([^\n`;|&#]+)", re.IGNORECASE
)
# npm install|i|add / yarn add / pnpm add <args...>
_NPM_RE = re.compile(
    r"\b(?:npm\s+(?:install|i|add)|yarn\s+add|pnpm\s+add)\s+([^\n`;|&#]+)",
    re.IGNORECASE,
)
# Pinned requirement line: name[extras] <specifier> version. The specifier is
# mandatory, which is what keeps ordinary prose from matching.
_REQ_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9._-]{0,213})\s*(?:\[[^\]]*\])?\s*(?:==|>=|<=|~=|!=)\s*[\w.*+!-]",
    re.MULTILINE,
)

# pip flags that consume the FOLLOWING token (a path/URL/filename, never a
# package) — used to skip that token during extraction.
_ARG_TAKING_FLAGS = frozenset(
    {"-r", "--requirement", "-c", "--constraint", "-e", "--editable",
     "-f", "--find-links", "-i", "--index-url", "--extra-index-url", "-t",
     "--target", "--prefix", "--registry"}
)

_PYPI_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,213}[a-z0-9])?$")
_NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$")

# Cheap pre-check: skip the whole plugin unless the response plausibly names a
# package to install. Substring match against a lowered copy of the content.
_TRIGGER_SUBSTRINGS = ("install", "yarn add", "pnpm add", "npm i", "==", ">=")


def _pep503(name: str) -> str:
    """PyPI canonical name: runs of -, _, . collapse to a single -."""
    return re.sub(r"[-_.]+", "-", name)


def _split_args(arg_str: str) -> list:
    """Tokenise an install command's argument string into package candidates,
    dropping flags and the path arguments that certain flags consume."""
    out: list = []
    skip_next = False
    for tok in arg_str.split():
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            if tok in _ARG_TAKING_FLAGS:
                skip_next = True
            # `--flag=value` and bare flags both carry no package
            continue
        out.append(tok)
    return out


def _norm_pypi(tok: str) -> Optional[str]:
    tok = tok.strip().strip("\"'").lower()
    # strip version specifier / extras / markers: keep the leading name only
    tok = re.split(r"[=<>~!;\[@ ]", tok, 1)[0].strip()
    if not tok or "/" in tok or ":" in tok or tok.startswith("."):
        return None  # paths, URLs, git+…, ./local — not a registry package
    return tok if _PYPI_NAME_RE.match(tok) else None


def _norm_npm(tok: str) -> Optional[str]:
    tok = tok.strip().strip("\"'").lower()
    if tok.startswith("@"):  # scoped: @scope/name[@version]
        parts = tok.split("@")  # ["", "scope/name", "version"?]
        if len(parts) < 2 or "/" not in parts[1]:
            return None
        name = "@" + parts[1]
    else:
        if "/" in tok or ":" in tok or tok.startswith("."):
            return None  # tarball path, git URL, local dir
        name = tok.split("@", 1)[0]
    return name if _NPM_NAME_RE.match(name) else None


def _extract_packages(text: str) -> list:
    """Return a de-duplicated list of (ecosystem, name) candidates, preserving
    first-seen order. Ecosystem is 'pypi' or 'npm'."""
    seen: set = set()
    found: list = []

    def add(eco: str, name: Optional[str]):
        if name and (eco, name) not in seen:
            seen.add((eco, name))
            found.append((eco, name))

    for m in _PIP_RE.finditer(text):
        for tok in _split_args(m.group(1)):
            add("pypi", _norm_pypi(tok))
    for m in _NPM_RE.finditer(text):
        for tok in _split_args(m.group(1)):
            add("npm", _norm_npm(tok))
    for m in _REQ_RE.finditer(text):
        add("pypi", _norm_pypi(m.group(1)))
    return found


# ── Tier 2 registry existence check (async, cached, fail-open) ───────────────
# Module-level cache shared across invocations: (ecosystem, name) -> (exists, expiry).
_CACHE: "dict[tuple[str, str], tuple[bool, float]]" = {}


def _cache_get(key) -> Optional[bool]:
    hit = _CACHE.get(key)
    if hit is None:
        return None
    exists, expiry = hit
    if expiry < time.monotonic():
        _CACHE.pop(key, None)
        return None
    return exists


def _cache_put(key, exists: bool, ttl_s: float) -> None:
    _CACHE[key] = (exists, time.monotonic() + ttl_s)


def _registry_url(ecosystem: str, name: str) -> str:
    if ecosystem == "pypi":
        return f"https://pypi.org/pypi/{_pep503(name)}/json"
    enc = name.replace("/", "%2F") if name.startswith("@") else name
    return f"https://registry.npmjs.org/{enc}"


class AiDependencyGuard(BasePlugin):
    name = "ai_dependency_guard"
    hook = PluginHook.POST_FLIGHT
    version = "1.0.0"
    author = "Fabrizio Salmi (ai-dependency-guard port)"
    description = (
        "Slopsquatting defense: flags LLM-recommended pip/npm packages that don't "
        "exist (registry 404) or are on a denylist. Port of ai-dependency-guard."
    )
    timeout_ms = 2500  # Tier-2 network budget + margin; fail-open on overrun

    def __init__(self, config: Any = None):
        super().__init__(config)
        self._registry_check = bool(self.config.get("registry_check", True))
        self._block = bool(self.config.get("block_on_hallucination", False))
        self._max_packages = int(self.config.get("max_packages_per_response", 20))
        self._budget_s = int(self.config.get("registry_timeout_ms", 1500)) / 1000.0
        self._cache_ttl_s = float(self.config.get("cache_ttl_s", 3600))
        ecos = self.config.get("ecosystems", ["pypi", "npm"]) or ["pypi", "npm"]
        self._ecosystems = {str(e).lower() for e in ecos}
        self._blocklist = {
            str(p).strip().lower()
            for p in (self.config.get("blocklist") or [])
            if str(p).strip()
        }
        self._total_checked = 0
        self._flagged = 0

    def get_stats(self) -> dict:
        return {
            "total_checked": self._total_checked,
            "responses_flagged": self._flagged,
            "cache_size": len(_CACHE),
        }

    def _extract_response_content(self, ctx: PluginContext) -> str:
        if not ctx.response or not hasattr(ctx.response, "body"):
            return ""
        try:
            data = json.loads(ctx.response.body.decode())
            choices = data.get("choices", [])
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "") or ""
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError, TypeError, KeyError) as e:
            self.logger.debug("ai_dependency_guard response parse skipped: %s", e)
            return ""

    async def _registry_exists(self, session, ecosystem: str, name: str) -> bool:
        """True = exists or assume-exists (fail-open); False = strict 404."""
        try:
            async with session.get(_registry_url(ecosystem, name)) as resp:
                exists = bool(resp.status != 404)
        except Exception as e:  # timeout, DNS, connreset → fail-open
            self.logger.debug("ai_dependency_guard registry error for %s: %s", name, e)
            return True
        _cache_put((ecosystem, name), exists, self._cache_ttl_s)
        return exists

    async def _tier2_missing(self, candidates: list) -> list:
        """Return the subset of candidates that the registry reports as 404.
        Cached results are used directly; the network sweep is bounded by the
        total time budget and fails open (returns no extra flags) on overrun."""
        to_check = []
        missing = []
        for eco, name in candidates:
            cached = _cache_get((eco, name))
            if cached is False:
                missing.append((eco, name))
            elif cached is None:
                to_check.append((eco, name))
        if not to_check:
            return missing
        try:
            import aiohttp
        except ImportError:
            return missing  # aiohttp unavailable → Tier-2 disabled, fail-open

        timeout = aiohttp.ClientTimeout(total=self._budget_s)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *(self._registry_exists(session, e, n) for e, n in to_check),
                        return_exceptions=True,
                    ),
                    timeout=self._budget_s,
                )
        except Exception as e:  # includes TimeoutError → fail-open
            self.logger.debug("ai_dependency_guard Tier-2 sweep skipped: %s", e)
            return missing  # fail-open: whatever we couldn't confirm, we allow
        for (eco, name), exists in zip(to_check, results):
            if exists is False:  # exceptions (fail-open) are truthy/non-False
                missing.append((eco, name))
        return missing

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        self._total_checked += 1
        if ctx.metadata.get("_cache_status") == "HIT":
            return PluginResponse.passthrough()

        content = self._extract_response_content(ctx)
        if not content:
            return PluginResponse.passthrough()
        low = content.lower()
        if not any(s in low for s in _TRIGGER_SUBSTRINGS):
            return PluginResponse.passthrough()

        candidates = [
            (eco, name)
            for eco, name in _extract_packages(content)
            if eco in self._ecosystems
        ][: self._max_packages]
        if not candidates:
            return PluginResponse.passthrough()

        flagged: dict = {}  # (eco,name) -> reason
        # Tier 1: offline blocklist
        remaining = []
        for eco, name in candidates:
            if name in self._blocklist:
                flagged[(eco, name)] = "blocklist"
            else:
                remaining.append((eco, name))
        # Tier 2: registry existence
        if self._registry_check and remaining:
            for eco, name in await self._tier2_missing(remaining):
                flagged[(eco, name)] = "not_found"

        if not flagged:
            return PluginResponse.passthrough()

        self._flagged += 1
        packages = [
            {"name": name, "ecosystem": eco, "reason": reason}
            for (eco, name), reason in flagged.items()
        ]
        ctx.metadata["_depguard_flagged"] = True
        ctx.metadata["_depguard_packages"] = packages
        names = ", ".join(p["name"] for p in packages)
        self.logger.warning(
            "ai_dependency_guard: %d suspicious package(s) in LLM response: %s",
            len(packages), names,
        )

        if self._block:
            return PluginResponse.block(
                status_code=403,
                error_type="hallucinated_dependency",
                message=(
                    "Response blocked: it recommends package(s) that appear "
                    f"hallucinated or denylisted ({names}). Verify before installing."
                ),
            )
        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            "AiDependencyGuard loaded: registry_check=%s block=%s ecosystems=%s "
            "blocklist=%d entries",
            self._registry_check, self._block, sorted(self._ecosystems),
            len(self._blocklist),
        )
