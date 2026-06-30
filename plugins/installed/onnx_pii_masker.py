"""
ONNX PII Masker — opt-in alternative to the default Presidio masker.

Contributed by Francesco Stimola (github.com/francesco-stimola/llmproxy-extended)
and integrated here as a disabled-by-default, opt-in plugin: enable it in
plugins/manifest.yaml to swap Presidio for ONNX NER. With grateful thanks. <3

PRE_FLIGHT plugin: replaces the default Presidio masker with the
OpenAI Privacy Filter NER model (fine-tuned transformer, ONNX backend).

Detects 8 PII categories:
  PRIVATE_PERSON, PRIVATE_EMAIL, PRIVATE_PHONE, PRIVATE_ADDRESS,
  PRIVATE_URL, PRIVATE_DATE, ACCOUNT_NUMBER, SECRET

Stores placeholder→value in rotator.security.pii_vault so Ring 4
(shield_sanitizer / demask_pii) restores originals automatically.

Placeholder format: [GROUP_N]  e.g. [PRIVATE_PERSON_1], [PRIVATE_EMAIL_2]
Consistency: within a single request, the same value always gets the same
placeholder (reverse_index). Across requests the vault handles re-mapping.

Requirements: onnxruntime>=1.18.0  transformers>=4.40.0  huggingface-hub>=0.20.0
Model must be pre-downloaded locally: huggingface-cli download openai/privacy-filter
"""
from typing import Any

from core.plugin_sdk import BasePlugin, PluginHook, PluginResponse
from core.plugin_engine import PluginContext

MODEL_ID = "openai/privacy-filter"

VARIANTS: dict[str, str] = {
    "fp32":  "onnx/model.onnx",
    "fp16":  "onnx/model_fp16.onnx",
    "int8":  "onnx/model_quantized.onnx",
    "q4":    "onnx/model_q4.onnx",
    "q4f16": "onnx/model_q4f16.onnx",
}

# Only int8 produces output identical to CPU when running on DirectML.
# Other variants may miss PII due to GPU/CPU round-trip divergence.
DML_SAFE_VARIANTS: frozenset[str] = frozenset({"int8"})

# Characters that the model sometimes absorbs into a span boundary but are
# never a legitimate edge of a PII value (e.g. "customer=Mario" → strips "=").
_STRIP_PUNCT = frozenset("=<>:;,\"'`()[]{}|!?*")


def _strippable(ch: str) -> bool:
    return ch.isspace() or ch in _STRIP_PUNCT


class _OnnxClassifier:
    """Minimal token-classification runner over a raw ONNX Runtime session.

    We cannot use transformers' pipeline + optimum here: the model needs
    transformers>=5 for its custom architecture, while optimum's ONNX bridge
    pins transformers<4.58 — mutually exclusive. We tokenize with the fast
    tokenizer, run the ONNX graph directly, and return one entity dict per
    non-O token with char offsets. _merge_consecutive then stitches adjacent
    tokens exactly like the torch aggregation_strategy="simple" path does.
    """

    def __init__(self, session: Any, tokenizer: Any, id2label: dict[int, str]) -> None:
        self.session = session
        self.tok = tokenizer
        self.id2label = id2label

    def __call__(self, text: str) -> list[dict]:
        import numpy as np

        enc = self.tok(
            text,
            return_offsets_mapping=True,
            return_tensors="np",
            truncation=True,
        )
        offsets = enc["offset_mapping"][0]
        feeds = {
            "input_ids": enc["input_ids"].astype("int64"),
            "attention_mask": enc["attention_mask"].astype("int64"),
        }
        logits = self.session.run(["logits"], feeds)[0][0]  # (seq_len, num_labels)
        ids = logits.argmax(-1)
        m = logits.max(-1, keepdims=True)
        ex = np.exp(logits - m)
        probs = ex / ex.sum(-1, keepdims=True)

        ents = []
        for i, lab_id in enumerate(ids):
            label = self.id2label[int(lab_id)]
            if label == "O":
                continue
            start, end = int(offsets[i][0]), int(offsets[i][1])
            if start == end:  # special token (CLS / SEP / pad)
                continue
            group = label.split("-", 1)[-1]  # strip B-/I-/E-/S- prefix
            ents.append({
                "entity_group": group,
                "start": start,
                "end": end,
                "score": float(probs[i, int(lab_id)]),
                "word": text[start:end],
            })
        return ents


def _merge_consecutive(entities: list[dict], max_gap: int = 1) -> list[dict]:
    """Stitch adjacent tokens of the same group whose boundaries touch."""
    if not entities:
        return []
    sorted_ents = sorted(entities, key=lambda e: e["start"])
    merged: list[dict] = [dict(sorted_ents[0])]
    for ent in sorted_ents[1:]:
        last = merged[-1]
        same_group = ent.get("entity_group") == last.get("entity_group")
        if same_group and ent["start"] - last["end"] <= max_gap:
            last["end"] = ent["end"]
            last["score"] = max(last.get("score", 0), ent.get("score", 0))
        else:
            merged.append(dict(ent))
    return merged


def _mask_text(
    text: str,
    raw_entities: list[dict],
    vault: Any,
    reverse_index: dict,
    counters: dict,
) -> str:
    """
    Replace detected entity spans with placeholders.

    vault:         rotator.security.pii_vault — TTLCache(token → original)
    reverse_index: (group, value_lower) → placeholder, shared across all
                   messages in the same request for consistency.
    counters:      group → current highest N, also shared per request.
    """
    entities = _merge_consecutive(raw_entities, max_gap=1)
    entities = sorted(entities, key=lambda e: e["start"], reverse=True)

    masked = text
    for ent in entities:
        group = ent["entity_group"].upper()
        start, end = ent["start"], ent["end"]

        while start < end and _strippable(text[start]):
            start += 1
        while end > start and _strippable(text[end - 1]):
            end -= 1
        if start >= end:
            continue

        original = text[start:end]
        key = (group, original.strip().lower())
        placeholder = reverse_index.get(key)
        if placeholder is None:
            counters[group] = counters.get(group, 0) + 1
            placeholder = f"[{group}_{counters[group]}]"
            reverse_index[key] = placeholder
            vault[placeholder] = original

        masked = masked[:start] + placeholder + masked[end:]

    return masked


class OnnxPiiMasker(BasePlugin):
    name = "onnx_pii_masker"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "Francesco Stimola (llmproxy-extended)"
    description = (
        "PII masking via OpenAI Privacy Filter (ONNX NER). Detects 8 categories: "
        "PRIVATE_PERSON, PRIVATE_EMAIL, PRIVATE_PHONE, PRIVATE_ADDRESS, PRIVATE_URL, "
        "PRIVATE_DATE, ACCOUNT_NUMBER, SECRET. Replaces Presidio default masker."
    )
    # NER inference: 50–300 ms per message on CPU; allow generous headroom.
    timeout_ms = 2000

    def __init__(self, config=None):
        super().__init__(config)
        self._classifier: _OnnxClassifier | None = None
        self._backend: str = "not-loaded"

    async def on_load(self) -> None:
        try:
            self._classifier, self._backend = self._build_classifier()
            self.logger.info(f"ONNX PII masker ready — backend={self._backend}")
        except Exception as exc:
            self.logger.error(
                f"Failed to load ONNX model ({exc}). "
                "Ensure the model is downloaded: "
                "huggingface-cli download openai/privacy-filter. "
                "Plugin will passthrough until model is available."
            )

    def _build_classifier(self) -> tuple[_OnnxClassifier, str]:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from transformers import AutoConfig, AutoTokenizer

        model_id: str = self.config.get("model_id", MODEL_ID)
        variant: str = self.config.get("variant", "int8")
        backend_pref: str = self.config.get("backend", "auto")
        force_cpu: bool = bool(self.config.get("force_cpu", False))

        if variant not in VARIANTS:
            self.logger.warning(f"Unknown variant '{variant}', falling back to int8")
            variant = "int8"

        onnx_file = VARIANTS[variant]
        providers = ["CPUExecutionProvider"]
        backend = f"onnx-cpu({variant})"

        want_gpu = not force_cpu and backend_pref in ("auto", "directml")
        if want_gpu and "DmlExecutionProvider" in ort.get_available_providers():
            if variant in DML_SAFE_VARIANTS:
                providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
                backend = f"onnx-directml({variant})"
            else:
                self.logger.warning(
                    f"DirectML disabled for variant '{variant}': GPU output diverges "
                    f"from CPU and can miss PII. Only int8 is DML-safe. Using CPU."
                )

        self.logger.info(f"Loading ONNX model {model_id} [{variant}] via {backend}")
        local_path = hf_hub_download(model_id, onnx_file, local_files_only=True)
        tok = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
        conf = AutoConfig.from_pretrained(model_id, local_files_only=True)
        sess = ort.InferenceSession(local_path, providers=providers)
        return _OnnxClassifier(sess, tok, conf.id2label), backend

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        if self._classifier is None:
            return PluginResponse.passthrough()

        rotator = ctx.metadata.get("rotator")
        if rotator is None:
            return PluginResponse.passthrough()

        body = ctx.body
        messages = body.get("messages")
        if not messages:
            return PluginResponse.passthrough()

        vault = rotator.security.pii_vault
        reverse_index: dict = {}  # (group, value_lower) → placeholder
        counters: dict = {}       # group → current max N

        any_masked = False
        for msg in messages:
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            try:
                raw_entities = self._classifier(content)
            except Exception as exc:
                self.logger.warning(f"Inference failed on message: {exc}")
                continue
            if not raw_entities:
                continue
            masked = _mask_text(content, raw_entities, vault, reverse_index, counters)
            if masked != content:
                msg["content"] = masked
                any_masked = True

        if any_masked:
            ctx.metadata["pii_masked"] = True
            categories = ", ".join(sorted(counters.keys()))
            self.logger.info(f"PII masked: [{categories}] — {len(counters)} category(ies)")
            await rotator._add_log(
                f"ONNX PII Masker: masked [{categories}]", level="SYSTEM"
            )
            return PluginResponse.modify(body=body)

        self.logger.debug("No PII detected in messages")
        return PluginResponse.passthrough()
