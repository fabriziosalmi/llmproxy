"""Score every sample with an open prompt-injection classifier (ONNX, CPU).

    python predict_classifier.py <work-dir>

The reference a lexical WAF is compared against: the highest INJECTION
probability over 512-token windows. Also scores the same tool results with the
attacker's text replaced by an ordinary sentence, to see what it does to clean
data. Writes preds_clf.jsonl and preds_clf_clean_tools.json.

Needs onnxruntime, tokenizers and numpy (see requirements.txt).
"""
import json
import sys
import time

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

W = sys.argv[1]
M = f"{W}/model/protectai"
MAX_WINDOWS = 16
CLEAN_SENTENCE = "Arrived on time and works as described. Would buy again."

with open(f"{M}/config.json") as f:
    id2label = {int(k): v for k, v in json.load(f)["id2label"].items()}
INJECTION = next(i for i, v in id2label.items() if v.upper() != "SAFE")
tok = Tokenizer.from_file(f"{M}/tokenizer.json")
tok.enable_truncation(max_length=512, stride=64)
options = ort.SessionOptions()
options.intra_op_num_threads = 4
sess = ort.InferenceSession(f"{M}/model.onnx", options, providers=["CPUExecutionProvider"])
INPUTS = {i.name for i in sess.get_inputs()}


def score(text: str) -> tuple[float, int]:
    enc = tok.encode(text)
    windows = ([enc] + enc.overflowing)[:MAX_WINDOWS]
    best = 0.0
    for w in windows:
        feed = {
            "input_ids": np.array([w.ids], dtype=np.int64),
            "attention_mask": np.array([w.attention_mask], dtype=np.int64),
        }
        if "token_type_ids" in INPUTS:
            feed["token_type_ids"] = np.array([w.type_ids], dtype=np.int64)
        logits = sess.run(None, feed)[0][0]
        p = np.exp(logits - logits.max())
        best = max(best, float(p[INJECTION] / p.sum()))
    return best, len(windows)


with open(f"{W}/samples.jsonl") as f, open(f"{W}/preds_clf.jsonl", "w") as out:
    for line in f:
        s = json.loads(line)
        t0 = time.perf_counter()
        value, windows = score(s["text"])
        out.write(
            json.dumps(
                {"id": s["id"], "score": round(value, 5), "windows": windows,
                 "ms": round((time.perf_counter() - t0) * 1000, 1)}
            )
            + "\n"
        )

clean = []
for name in ("dh", "ds"):
    with open(f"{W}/data/injecagent/test_cases_{name}_base.json") as f:
        for r in json.load(f):
            tpl = r["Tool Response Template"]
            tpl = tpl if isinstance(tpl, str) else json.dumps(tpl)
            if "<Attacker Instruction>" in tpl:
                clean.append(round(score(tpl.replace("<Attacker Instruction>", CLEAN_SENTENCE))[0], 5))
with open(f"{W}/preds_clf_clean_tools.json", "w") as f:
    json.dump(clean, f)
print("done")
