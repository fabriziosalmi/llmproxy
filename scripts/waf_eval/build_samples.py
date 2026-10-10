"""Turn the fetched datasets and the held-out set into one samples.jsonl.

    python build_samples.py <work-dir>

One line per sample: {id, dataset, label, family, text, messages}. label 1 is an
attack (should be stopped), 0 is benign (should pass). ``messages`` is the chat
request the sample arrives as; ``text`` is the part a detector is asked about
(for an indirect injection, the tool result that carries it).

Needs pyarrow (see requirements.txt); nothing else from this repository.
"""
import collections
import csv
import json
import pathlib
import sys

import pyarrow.parquet as pq

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import heldout  # noqa: E402

W = sys.argv[1]
out: list[dict] = []
counts: collections.Counter = collections.Counter()


def add(dataset, label, text, messages=None, family=""):
    text = (text or "").strip()
    if not text:
        return
    out.append(
        {
            "id": f"{dataset}-{counts[dataset]:05d}",
            "dataset": dataset,
            "label": int(label),
            "family": family,
            "text": text,
            "messages": messages or [{"role": "user", "content": text}],
        }
    )
    counts[dataset] += 1


for split in ("train", "test"):
    for r in pq.read_table(f"{W}/data/deepset/{split}.parquet").to_pylist():
        add("deepset", r["label"], r["text"])
for split in ("train", "validation", "test"):
    for r in pq.read_table(f"{W}/data/gandalf/{split}.parquet").to_pylist():
        add("gandalf", 1, r["text"])
for split in ("one", "two", "three"):
    for r in pq.read_table(f"{W}/data/notinject/{split}.parquet").to_pylist():
        add("notinject", 0, r["prompt"], family=r.get("category") or "")
csv.field_size_limit(10**9)
with open(f"{W}/data/jackhhao/full.csv", newline="") as f:
    for r in csv.DictReader(f):
        add("jackhhao", 1 if r["type"] == "jailbreak" else 0, r["prompt"])
for name in ("dh", "ds"):
    with open(f"{W}/data/injecagent/test_cases_{name}_base.json") as f:
        cases = json.load(f)
    for r in cases:
        tool = r["Tool Response"] if isinstance(r["Tool Response"], str) else json.dumps(r["Tool Response"])
        params = r["Tool Parameters"] if isinstance(r["Tool Parameters"], str) else json.dumps(r["Tool Parameters"])
        add(
            "injecagent",
            1,
            tool,
            [
                {"role": "user", "content": r["User Instruction"]},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": r["User Tool"], "arguments": params}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": tool},
            ],
            family=r.get("Attack Type") or name,
        )
for family, text in heldout.ATTACKS:
    add("heldout", 1, text, family=family)
for family, text in heldout.BENIGN:
    add("heldout", 0, text, family=family)

with open(f"{W}/samples.jsonl", "w") as f:
    for o in out:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
by = collections.Counter((o["dataset"], o["label"]) for o in out)
for (dataset, label), n in sorted(by.items()):
    print(f"{dataset:<12} {'attack' if label else 'benign'} n={n}")
print("total", len(out))
