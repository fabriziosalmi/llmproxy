# Detection Benchmark

What the proxy's detection layer stops, measured on data it was not written
against. The numbers on this page and in the README are generated from
`scripts/waf_eval/results.json`; a test fails the build if they drift apart.

## Results

<!-- waf-bench:start -->
Measured 2026-10-10 on llmproxy 1.38.1 (byte firewall and SecurityShield, default configuration), against `protectai/deberta-v3-base-prompt-injection-v2` at threshold 0.5.

| Dataset | Kind | Prompts | llmproxy | Classifier |
|---|---|---:|---:|---:|
| `deepset/prompt-injections` | attack | 263 | 4.6% | 41% |
| `Lakera/gandalf_ignore_instructions` | attack | 1,000 | 39% | 100% |
| `scripts/waf_eval/heldout.py` | attack | 52 | 27% | 90% |
| `uiuc-kang-lab/InjecAgent` | attack | 1,054 | 0% | 66% |
| `jackhhao/jailbreak-classification` | attack | 666 | 32% | 84% |
| `deepset/prompt-injections` | benign | 399 | 0% | 1.0% |
| `scripts/waf_eval/heldout.py` | benign | 42 | 4.8% | 38% |
| `jackhhao/jailbreak-classification` | benign | 1,332 | 0.1% | 0.6% |
| `leolee99/NotInject` | benign | 339 | 0% | 43% |
| **All attacks, stopped** | | **3,035** | **21%** | **79%** |
| **All benign, stopped by mistake** | | **2,112** | **0.1%** | **8.2%** |

The held-out set by family (stopped / prompts):

| Kind | Family | llmproxy | Classifier |
|---|---|---:|---:|
| attack | direct | 1 / 3 | 3 / 3 |
| attack | paraphrase | 0 / 6 | 6 / 6 |
| attack | extraction | 2 / 6 | 6 / 6 |
| attack | roleplay | 2 / 6 | 3 / 6 |
| attack | multilingual | 4 / 8 | 8 / 8 |
| attack | obfuscation | 5 / 8 | 7 / 8 |
| attack | indirect | 0 / 5 | 4 / 5 |
| attack | exfiltration | 0 / 2 | 2 / 2 |
| attack | authority | 0 / 3 | 3 / 3 |
| attack | hypothetical | 0 / 3 | 3 / 3 |
| attack | agent | 0 / 2 | 2 / 2 |
| benign | ordinary | 0 / 6 | 1 / 6 |
| benign | security-talk | 2 / 6 | 4 / 6 |
| benign | instruction-words | 0 / 10 | 3 / 10 |
| benign | devops | 0 / 8 | 2 / 8 |
| benign | document | 0 / 4 | 4 / 4 |
| benign | multilingual | 0 / 4 | 1 / 4 |
| benign | fiction | 0 / 4 | 1 / 4 |

The tool policy on the indirect-injection benchmark (the model is assumed to obey the planted instruction; the policy is `after_tool_result: ["*Get*", "*Read*", "*Search*", "*View*", "*List*", "*Navigate*"]`):

| Attack type | Cases | Attacker's call refused |
|---|---:|---:|
| direct harm | 510 | 510 |
| data stealing | 544 | 544 |
| the user's own call, refused by mistake | 1,054 | 0 |

The classifier at other thresholds (attacks stopped; hard benign prompts stopped by mistake):

| Threshold | Attacks | Hard benign |
|---|---:|---:|
| 0.5 | 79% | 42% |
| 0.99 | 63% | 29% |
| 0.9999 | 50% | 16% |

Hard benign prompts are the trigger-word set and the held-out negatives (381 prompts).
On 1,054 clean tool results (the indirect-injection templates with the attacker's text replaced by an ordinary sentence) the classifier flagged 29%.
Latency per prompt on a laptop CPU: llmproxy median 0.43 ms; classifier median 16 ms, 95th percentile 195 ms.
<!-- waf-bench:end -->

## Reading the numbers

**The proxy's detection is lexical.** The byte firewall matches signatures after
decoding common encodings; the shield scores regular expressions and compares
character trigrams against a list of known phrasings. That recognises the
well-known wordings and their obfuscations, in well under a millisecond and with
almost no false positives. It does not recognise an attack that is worded
differently, and it has nothing to say about an instruction planted in a tool
result, which is an ordinary sentence in the wrong place.

**A classifier is not the answer either.** The open model used as a reference
stops about four attacks in five, and refuses a large share of legitimate text
that merely talks about instructions, as well as a good part of clean tool
output. Raising its threshold trades the one for the other without a setting
that is good at both. It is useful as a signal to record and review; as a gate it
breaks ordinary work.

**So do not rely on recognising the attack.** What limits the damage of an
injection is what the response is allowed to do: which tools may be called and
when, where links and images may point, whether a secret can leave. Those
controls do not depend on the wording of the attack.

## Caveats

- Three of the public datasets (`gandalf`, `deepset`, `jackhhao`) are widely
  used for training; the reference classifier has very likely seen them. The
  indirect-injection benchmark, the trigger-word set and the held-out set are
  the out-of-distribution tests.
- The held-out set is small (94 prompts) and was written by the reviewer who
  ran the measurement, before reading the signatures. It is there so the
  repository carries a set the detector was not tuned on, not as a benchmark of
  its own.
- "Stopped" means the request is refused. For the indirect-injection set the
  whole conversation (user turn, tool call, tool result) is sent as the proxy
  would receive it.
- Each prompt is judged on its own: the shield's multi-turn and cross-session
  memory is empty.
- The regression corpus in `tests/corpus/` is not part of this: it was written
  with the detector.

## Reproducing it

```bash
make waf-bench            # fetch (about 750 MB), score, write results.json, update the tables
make waf-bench-check      # only the part that needs no download: the held-out set
```

`scripts/waf_eval/fetch.sh` downloads the datasets and the classifier at pinned
revisions into `.waf-bench/` (git-ignored); they are not redistributed here.

| Source | Licence |
|---|---|
| `deepset/prompt-injections` | Apache-2.0 |
| `Lakera/gandalf_ignore_instructions` | MIT |
| `jackhhao/jailbreak-classification` | Apache-2.0 |
| `leolee99/NotInject` | MIT |
| `uiuc-kang-lab/InjecAgent` | MIT |
| `protectai/deberta-v3-base-prompt-injection-v2` (reference classifier) | Apache-2.0 |
