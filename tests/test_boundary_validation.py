"""Input was validated by whatever eventually used it.

No handler that accepts caller input declared a request model: chat,
completions and embeddings each did `await request.json()` and reached into the
dict with .get(). So `{"model": {"a": 1}}` was accepted at the edge, written
into the audit and spend rows, used as a dict key in the cost estimator and
passed to the tokenizer — surfacing as a TypeError from a module the caller had
never heard of rather than a 422 naming the field. And `{"messages": "hello"}`
passed the max_messages check, because len() of a string is a number, before
being iterated character by character.

Also here: two containment checks that normalised paths without resolving
symlinks, and a signature that joined caller-influenced fields with a bare
separator.
"""

import httpx
import pytest

from conftest import InMemoryRepository, minimal_config
from test_e2e import LightweightAgent


def _open_agent():
    from proxy.app_factory import create_app

    config = minimal_config()
    config["server"]["auth"]["enabled"] = False
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)
    return agent


def _client(agent):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    )


# ── the boundary rejects what used to travel inward ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"model": {"a": 1}, "messages": [{"role": "user", "content": "hi"}]},
        {"model": "gpt-4o", "messages": "hello"},
        {"messages": [{"role": "user", "content": "hi"}]},  # no model
        {"model": "gpt-4o"},  # no messages
        {"model": "gpt-4o", "messages": [{"content": "no role"}]},
    ],
)
async def test_a_malformed_chat_body_is_422_not_a_deep_typeerror(body):
    agent = _open_agent()
    async with _client(agent) as c:
        resp = await c.post("/v1/chat/completions", json=body)

    assert resp.status_code == 422, f"{body} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_the_error_names_the_field():
    """A 422 that does not say which field is barely better than a 500."""
    agent = _open_agent()
    async with _client(agent) as c:
        resp = await c.post(
            "/v1/chat/completions", json={"messages": [{"role": "u", "content": "x"}]}
        )

    assert "model" in resp.text


@pytest.mark.asyncio
async def test_a_valid_request_is_unaffected():
    agent = _open_agent()
    async with _client(agent) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code != 422


@pytest.mark.asyncio
async def test_unknown_parameters_still_pass_through():
    """These are OpenAI-compatible endpoints; callers send parameters this
    proxy does not model, and rejecting them would break every real client."""
    agent = _open_agent()
    async with _client(agent) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.7,
                "tool_choice": "auto",
                "some_provider_extension": {"nested": True},
            },
        )

    assert resp.status_code != 422


def test_only_what_the_caller_sent_is_forwarded():
    """exclude_unset keeps this identical to forwarding the raw parsed body —
    validation adds a gate without changing what reaches the upstream."""
    from proxy.schemas import ChatCompletionRequest

    body = ChatCompletionRequest(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    ).to_body()

    assert "stream" not in body, "a default the caller never sent would be forwarded"
    assert body["model"] == "gpt-4o"


def test_multimodal_content_is_accepted():
    """The OpenAI schema allows a list of content parts, and the adapters
    translate it — narrowing to str would reject requests that work."""
    from proxy.schemas import ChatCompletionRequest

    req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this"},
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                ],
            }
        ],
    )

    assert isinstance(req.to_body()["messages"][0]["content"], list)


@pytest.mark.asyncio
async def test_completions_and_embeddings_are_validated_too():
    agent = _open_agent()
    async with _client(agent) as c:
        completions = await c.post("/v1/completions", json={"prompt": "hi"})
        embeddings = await c.post("/v1/embeddings", json={"input": "hi"})

    assert completions.status_code == 422
    assert embeddings.status_code == 422


def test_the_openapi_document_now_carries_request_shapes():
    """It described no request body at all, so it documented nothing a client
    could validate against."""
    agent = _open_agent()
    schema = agent.app.openapi()

    body = schema["paths"]["/v1/chat/completions"]["post"].get("requestBody")
    assert body is not None
    ref = body["content"]["application/json"]["schema"]["$ref"]
    assert "ChatCompletionRequest" in ref


# ── containment resolves symlinks ───────────────────────────────────────────


def test_path_containment_uses_realpath(tmp_path):
    """abspath normalises ".." but follows symlinks, so a link planted inside
    the root pointed anywhere and every check passed."""
    import os

    root = tmp_path / "exports"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("not yours")
    (root / "innocent.txt").symlink_to(outside)

    export_dir = os.path.realpath(str(root))
    requested = os.path.realpath(os.path.join(export_dir, "innocent.txt"))

    assert os.path.commonpath([export_dir, requested]) != export_dir, (
        "the symlink escaped and containment did not notice"
    )


def test_the_call_sites_use_realpath():
    import inspect

    import core.plugin_engine as plugin_engine
    import proxy.routes.admin as admin

    assert "os.path.realpath(self.plugins_dir)" in inspect.getsource(plugin_engine)
    download = inspect.getsource(admin)
    start = download.index("async def download_export_file")
    assert "os.path.realpath" in download[start : start + 800]


# ── the signature commits to its field split ────────────────────────────────


def test_a_separator_in_a_field_no_longer_collides():
    """model "a|b" + provider "c" signed the same message as model "a" +
    provider "b|c", so the signature did not uniquely commit to the split it
    advertises in X-LLMProxy-Signed-Fields."""
    from core.response_signer import _canonical_message

    left = _canonical_message("a|b", "c", "t", "r")
    right = _canonical_message("a", "b|c", "t", "r")

    assert left != right


def test_the_body_is_still_fully_covered():
    from core.response_signer import ResponseSigner

    signer = ResponseSigner("secret")
    headers = signer.sign_response(b"hello", "gpt-4o", "openai", "req-1")

    assert ResponseSigner.verify(
        "secret",
        b"hello",
        "gpt-4o",
        "openai",
        headers["X-LLMProxy-Signed-At"],
        "req-1",
        headers["X-LLMProxy-Signature"],
    )
    assert not ResponseSigner.verify(
        "secret",
        b"HELLO",
        "gpt-4o",
        "openai",
        headers["X-LLMProxy-Signed-At"],
        "req-1",
        headers["X-LLMProxy-Signature"],
    )


# ── the coverage floor tracks the measurement ───────────────────────────────


def test_the_coverage_floor_is_not_far_below_reality():
    """It sat at 68 against a measured 70.1, so ~220 statements could drain
    away unnoticed — and the modules with room to fall are the consequential
    ones."""
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".github/workflows/ci.yml")) as f:
        ci = f.read()

    floor = int(re.search(r"--cov-fail-under=(\d+)", ci).group(1))
    assert floor >= 71, f"floor is {floor}; raise it when coverage rises"


# ── session ids are derived, not hashed ─────────────────────────────────────


def test_a_session_id_is_not_a_bare_hash_of_the_token():
    """CodeQL's py/weak-sensitive-data-hashing, and it is right in principle.

    A session_id is not private — it is written into every audit row and shows
    up in log lines — so a bare hash of a credential lets anyone who sees one
    test candidate tokens offline. With 128-bit random keys that is infeasible,
    but the safety rested on an assumption about key entropy that the code
    cannot enforce, and operators choose their own keys.
    """
    import hashlib

    from core.session_id import from_token

    token = "sk-proxy-0123456789abcdef"
    assert from_token(token) != hashlib.sha256(token.encode()).hexdigest()[:16]


def test_the_same_token_gives_the_same_id():
    """It doubles as the semantic cache's tenant key, so it must be stable."""
    from core.session_id import from_token

    assert from_token("sk-a") == from_token("sk-a")
    assert from_token("sk-a") != from_token("sk-b")


def test_the_identity_secret_makes_it_stable_across_processes(monkeypatch):
    from core.infisical import clear_cache
    from core.session_id import from_token, reset_for_tests

    monkeypatch.setenv("LLM_PROXY_IDENTITYSECRET_UNUSED", "x")
    monkeypatch.setenv("LLM_PROXY_IDENTITY_SECRET", "a-stable-server-side-secret")
    clear_cache()
    reset_for_tests()
    first = from_token("sk-a")

    reset_for_tests()  # simulate a restart
    clear_cache()
    assert from_token("sk-a") == first


def test_without_the_identity_secret_it_still_works(monkeypatch):
    """Unguessable either way; the cost is a cold cache after a restart."""
    from core.infisical import clear_cache
    from core.session_id import from_token, reset_for_tests

    monkeypatch.delenv("LLM_PROXY_IDENTITY_SECRET", raising=False)
    clear_cache()
    reset_for_tests()

    assert len(from_token("sk-a")) == 16


def test_the_fingerprint_branch_separates_callers_behind_one_nat():
    """Collapsing them would make the multi-turn detector score unrelated
    callers together."""
    from core.session_id import from_fingerprint

    a = from_fingerprint("10.0.0.1", "curl/8", "en")
    b = from_fingerprint("10.0.0.1", "python-httpx/0.27", "en")

    assert a != b


def test_no_route_derives_a_session_id_by_hand():
    """The same block was transcribed in three modules."""
    import inspect

    from proxy.routes import chat, completions, embeddings

    for module in (chat, completions, embeddings):
        source = inspect.getsource(module)
        assert "sha256(token" not in source, f"{module.__name__} still hashes directly"
        assert "session_id_from_token" in source
