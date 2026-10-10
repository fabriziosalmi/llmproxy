"""The firewall's signatures survive a volume mounted over ``data/``.

The signatures, the trigram corpus and the pricing table ship in ``data/``, the
directory the data volume is mounted on. A bind mount or an empty PVC hides
them: the production deployment ran its firewall on the 28 built-in signatures
instead of the 178 in the file, and said so only at DEBUG. The image now keeps a
second copy in ``/app/defaults`` and the loader falls back to it.
"""

import logging
import pathlib
import shutil

from core import signature_loader
from core.signature_loader import SignatureStore

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = ("signatures.yaml", "injection_corpus.yaml", "pricing.yaml")


def _store(directory):
    return SignatureStore(
        signatures_path=str(directory / "signatures.yaml"),
        corpus_path=str(directory / "injection_corpus.yaml"),
        pricing_path=str(directory / "pricing.yaml"),
    )


def test_with_data_hidden_the_shipped_copy_is_loaded(tmp_path, monkeypatch, caplog):
    shipped = tmp_path / "defaults"
    shipped.mkdir()
    for name in FILES:
        shutil.copy(ROOT / "data" / name, shipped / name)
    monkeypatch.setattr(signature_loader, "SHIPPED_DIR", shipped)
    hidden = tmp_path / "data"  # an empty volume: none of the files are there
    hidden.mkdir()

    with caplog.at_level(logging.WARNING, logger="llmproxy.signatures"):
        store = _store(hidden)

    assert store.load() is True
    assert len(store.banned_signatures) > 100 and len(store.corpus) > 100 and store.pricing
    assert "using the copy shipped with this release" in caplog.text


def test_a_file_in_data_is_still_the_one_used(tmp_path, monkeypatch):
    shipped = tmp_path / "defaults"
    shipped.mkdir()
    for name in FILES:
        shutil.copy(ROOT / "data" / name, shipped / name)
    monkeypatch.setattr(signature_loader, "SHIPPED_DIR", shipped)
    data = tmp_path / "data"
    data.mkdir()
    (data / "signatures.yaml").write_text("version: 1\nbanned_signatures: ['operator phrase one']\nrot13_signatures: []\n")

    store = _store(data)
    store.load()

    assert store.banned_signatures == [b"operator phrase one"]


def test_with_neither_copy_the_fallback_is_announced_at_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(signature_loader, "SHIPPED_DIR", tmp_path / "nowhere")

    with caplog.at_level(logging.WARNING, logger="llmproxy.signatures"):
        loaded = _store(tmp_path).load()

    assert loaded is False
    assert "built-in fallback list" in caplog.text


def test_the_image_keeps_the_second_copy_outside_the_data_directory():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "/app/defaults" in dockerfile
    for name in FILES:
        assert f"/app/data/{name}" in dockerfile
