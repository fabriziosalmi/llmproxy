"""The encryption salt must live where the ciphertext it decrypts lives.

The salt is one half of the key for every stored credential; the database
holding that ciphertext is the other half. They were placed in different
durability classes: docker-compose mounts a volume at /app/data while the salt
defaulted to /app/.llmproxy_salt, which is image content. A rebuild therefore
preserved the encrypted values and discarded the key — and because decrypt()
returns the input on InvalidToken, the proxy then carried on holding Fernet
tokens where credentials should be, so the symptom was 401s from every provider
rather than an error naming the cause.

The upgrade path is the delicate part: switching the default without honouring
an existing legacy file would itself generate a fresh salt and orphan
everything encrypted under the old one.
"""

import os

import pytest

from core.secrets import SecretManager


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_PROXY_SALT_PATH", raising=False)
    yield


def test_the_default_is_inside_the_data_directory():
    """data/ is what the deployment guide calls unreconstructable state."""
    assert SecretManager._resolve_salt_path() == "data/.llmproxy_salt"


def test_an_existing_legacy_salt_still_wins(tmp_path):
    """An upgraded deployment must keep decrypting what it already has."""
    legacy = tmp_path / ".llmproxy_salt"
    legacy.write_bytes(b"\x01" * 32)

    assert SecretManager._resolve_salt_path() == ".llmproxy_salt"


def test_the_new_location_wins_once_the_salt_has_been_moved(tmp_path):
    """After migration the legacy file is ignored, not preferred forever."""
    (tmp_path / ".llmproxy_salt").write_bytes(b"\x01" * 32)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".llmproxy_salt").write_bytes(b"\x02" * 32)

    assert SecretManager._resolve_salt_path() == "data/.llmproxy_salt"


def test_an_explicit_path_overrides_everything(tmp_path, monkeypatch):
    (tmp_path / ".llmproxy_salt").write_bytes(b"\x01" * 32)
    monkeypatch.setenv("LLM_PROXY_SALT_PATH", "/somewhere/else/salt")

    assert SecretManager._resolve_salt_path() == "/somewhere/else/salt"


def test_a_new_salt_is_written_into_a_directory_that_does_not_exist_yet(
    tmp_path, monkeypatch
):
    """First boot has no data/ — generating the salt must create it."""
    monkeypatch.setenv("LLM_PROXY_MASTER_KEY", "a-master-key-for-tests")
    SecretManager._fernet = None

    SecretManager._get_fernet()

    salt_file = tmp_path / "data" / ".llmproxy_salt"
    assert salt_file.exists()
    assert len(salt_file.read_bytes()) == 32
    assert oct(salt_file.stat().st_mode)[-3:] == "600"
    SecretManager._fernet = None


def test_a_truncated_salt_refuses_to_start_rather_than_deriving_a_wrong_key(
    tmp_path, monkeypatch
):
    """A zero-length salt used to be read and used, silently.

    That is the unrecoverable case: a different salt derives a different key,
    every stored value fails to decrypt, and the failure surfaces far from its
    cause. Failing to boot is the correct outcome — the original salt is the
    only thing that can read the data, so the operator must restore it.
    """
    monkeypatch.setenv("LLM_PROXY_MASTER_KEY", "a-master-key-for-tests")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".llmproxy_salt").write_bytes(b"")
    SecretManager._fernet = None

    with pytest.raises(ValueError, match="truncated or corrupt"):
        SecretManager._get_fernet()

    SecretManager._fernet = None


def test_a_full_length_salt_round_trips(tmp_path, monkeypatch):
    """The guard must not reject a healthy salt."""
    monkeypatch.setenv("LLM_PROXY_MASTER_KEY", "a-master-key-for-tests")
    SecretManager._fernet = None

    ciphertext = SecretManager.encrypt("sk-provider-secret")
    SecretManager._fernet = None  # force a re-derive from the persisted salt
    assert SecretManager.decrypt(ciphertext) == "sk-provider-secret"

    SecretManager._fernet = None


def test_the_compose_stack_does_not_pin_the_salt_path():
    """Pinning it would skip the legacy fallback and orphan an upgrade."""
    import pathlib

    compose = pathlib.Path(
        os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")
    ).read_text()
    for line in compose.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "LLM_PROXY_SALT_PATH=" not in stripped, (
            "docker-compose.yml pins LLM_PROXY_SALT_PATH; that bypasses the "
            "legacy-salt fallback and regenerates the key on upgrade"
        )
