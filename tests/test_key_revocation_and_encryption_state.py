"""Taking a key away, and the encryption that protects nothing.

Two findings from the audit that share a subject.

**Revocation required a restart.** Every authentication decision resolves the
key bag through core.infisical.get_secret, which memoised each value
permanently: no expiry, no refresh, and the only function that cleared it —
whose docstring says it is "useful for rotation" — was called from tests and
nowhere else. So deleting a key from the environment did nothing, rotating it
in Infisical did nothing, and the config hot-reload did nothing either, because
the reload re-reads the same cached name. For a gateway whose keys are handed
to application teams, a leaked key stayed live until someone could take an
outage.

**The encryption subsystem has no callers.** SecretManager.encrypt and .decrypt
are called nowhere, so the Fernet key, the PBKDF2 salt and
LLM_PROXY_MASTER_KEY protect no stored data — and no salt file is created on a
fresh install, because nothing asks for a Fernet. That is fine as a design
(credentials are referenced by environment-variable NAME and never written to
disk, which is stronger than encrypting them) and was not fine as
documentation: .env.example listed the master key under "REQUIRED — Proxy
won't start without these", the deployment guide tabulated it, and the rotation
script printed a runbook about "provider API keys stored in SQLite".
"""

import time

import pytest


@pytest.fixture(autouse=True)
def _isolate_secret_cache():
    from core.infisical import clear_cache

    clear_cache()
    yield
    clear_cache()


# ── a key can be taken away without a restart ───────────────────────────────


def test_a_resolved_secret_is_reused_within_the_ttl(monkeypatch):
    """The cache still exists — the point is that it expires."""
    from core.infisical import get_secret

    monkeypatch.setenv("LLM_PROXY_TEST_ROTATE", "first")
    assert get_secret("LLM_PROXY_TEST_ROTATE") == "first"

    monkeypatch.setenv("LLM_PROXY_TEST_ROTATE", "second")
    assert get_secret("LLM_PROXY_TEST_ROTATE") == "first", "cache stopped working"


def test_a_rotated_secret_is_picked_up_after_the_ttl(monkeypatch):
    """The finding: this returned the old value for the life of the process."""
    from core.infisical import get_secret

    monkeypatch.setenv("LLM_PROXY_SECRET_CACHE_TTL", "0.05")
    monkeypatch.setenv("LLM_PROXY_TEST_ROTATE", "first")
    assert get_secret("LLM_PROXY_TEST_ROTATE") == "first"

    monkeypatch.setenv("LLM_PROXY_TEST_ROTATE", "second")
    time.sleep(0.08)

    assert get_secret("LLM_PROXY_TEST_ROTATE") == "second"


def test_a_revoked_key_stops_working_after_the_ttl(monkeypatch):
    """The case that matters: not rotation, removal."""
    from proxy.auth_helpers import resolve_api_keys

    monkeypatch.setenv("LLM_PROXY_SECRET_CACHE_TTL", "0.05")
    monkeypatch.setenv("LLM_PROXY_TEST_BAG", "sk-leaked,sk-kept")
    config = {"server": {"auth": {"api_keys_env": "LLM_PROXY_TEST_BAG"}}}
    assert "sk-leaked" in resolve_api_keys(config)

    monkeypatch.setenv("LLM_PROXY_TEST_BAG", "sk-kept")
    time.sleep(0.08)

    keys = resolve_api_keys(config)
    assert "sk-leaked" not in keys
    assert "sk-kept" in keys


def test_the_ttl_is_configurable(monkeypatch):
    from core.infisical import DEFAULT_CACHE_TTL_S, _cache_ttl

    monkeypatch.delenv("LLM_PROXY_SECRET_CACHE_TTL", raising=False)
    assert _cache_ttl() == DEFAULT_CACHE_TTL_S

    monkeypatch.setenv("LLM_PROXY_SECRET_CACHE_TTL", "5")
    assert _cache_ttl() == 5.0


def test_a_nonsense_ttl_falls_back_rather_than_crashing(monkeypatch, caplog):
    from core.infisical import DEFAULT_CACHE_TTL_S, _cache_ttl

    monkeypatch.setenv("LLM_PROXY_SECRET_CACHE_TTL", "soon")
    with caplog.at_level("WARNING"):
        assert _cache_ttl() == DEFAULT_CACHE_TTL_S


def test_caching_can_be_disabled(monkeypatch):
    from core.infisical import get_secret

    monkeypatch.setenv("LLM_PROXY_SECRET_CACHE_TTL", "0")
    monkeypatch.setenv("LLM_PROXY_TEST_ROTATE", "first")
    assert get_secret("LLM_PROXY_TEST_ROTATE") == "first"

    monkeypatch.setenv("LLM_PROXY_TEST_ROTATE", "second")
    assert get_secret("LLM_PROXY_TEST_ROTATE") == "second"


def test_the_config_reload_clears_the_secret_cache():
    """A reload that changes api_keys_env, or a just-rotated key, should take
    effect now rather than at the next restart."""
    import inspect

    import proxy.background as background

    source = inspect.getsource(background.config_watch_loop)

    assert "clear_cache" in source


# ── the encryption subsystem, described honestly ────────────────────────────


def test_nothing_calls_encrypt_or_decrypt():
    """Recording the current state so wiring it up is a deliberate change.

    If this fails because a caller was added, that is good — update the
    documentation this test guards at the same time.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    callers = []
    for directory in ("core", "proxy", "store", "plugins"):
        for path in (root / directory).rglob("*.py"):
            if path.name == "secrets.py":
                continue
            text = path.read_text()
            if "SecretManager.encrypt" in text or "SecretManager.decrypt" in text:
                callers.append(str(path.relative_to(root)))

    assert not callers, (
        "SecretManager.encrypt/decrypt now has callers: "
        f"{callers}. The docs say the master key is unused — update them."
    )


def test_the_docs_do_not_call_the_master_key_required():
    """It sat under "REQUIRED — Proxy won't start without these" while being
    read by nothing at run time."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    env_example = (root / ".env.example").read_text()

    block = env_example[env_example.index("LLM_PROXY_MASTER_KEY=") - 900 :]
    block = block[: block.index("LLM_PROXY_MASTER_KEY=")]

    assert "REQUIRED before" not in block
    assert "unused" in block.lower() or "optional" in block.lower()


def test_no_provider_credential_is_stored_at_rest():
    """The property that makes the missing encryption acceptable: the schema
    has no column for a credential, so there is nothing to encrypt."""
    from store.schema import iter_create_statements

    statements = " ".join(iter_create_statements("sqlite")).lower()

    assert "api_key" not in statements.replace("api_key_env", "")


# ── decryption fails loudly instead of returning its input ──────────────────


def test_decrypt_raises_on_a_value_it_cannot_read(monkeypatch, tmp_path):
    """It used to return the CIPHERTEXT, logged at ERROR.

    Were the subsystem wired, a wrong master key would have handed Fernet
    tokens to callers as credentials and the symptom would be 401s from every
    provider with nothing naming decryption as the cause.
    """
    from core.secrets import SecretManager

    monkeypatch.setenv("LLM_PROXY_MASTER_KEY", "a-master-key-for-tests")
    monkeypatch.setenv("LLM_PROXY_SALT_PATH", str(tmp_path / "salt"))
    SecretManager._fernet = None

    with pytest.raises(ValueError, match="could not be decrypted|not a valid"):
        SecretManager.decrypt("this-is-not-a-fernet-token")

    SecretManager._fernet = None


def test_decrypt_round_trips_a_value_it_wrote(monkeypatch, tmp_path):
    """Failing loudly must not mean failing on valid input."""
    from core.secrets import SecretManager

    monkeypatch.setenv("LLM_PROXY_MASTER_KEY", "a-master-key-for-tests")
    monkeypatch.setenv("LLM_PROXY_SALT_PATH", str(tmp_path / "salt"))
    SecretManager._fernet = None

    token = SecretManager.encrypt("sk-provider-secret")
    assert SecretManager.decrypt(token) == "sk-provider-secret"

    SecretManager._fernet = None


def test_decrypt_still_passes_through_the_empty_string(monkeypatch, tmp_path):
    from core.secrets import SecretManager

    assert SecretManager.decrypt("") == ""
