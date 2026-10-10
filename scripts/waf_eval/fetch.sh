#!/usr/bin/env bash
# Fetch the public datasets and the classifier used by the WAF benchmark, at pinned revisions,
# into a directory OUTSIDE the repository (they are not redistributed here).
#   deepset/prompt-injections (Apache-2.0)        jackhhao/jailbreak-classification (Apache-2.0)
#   Lakera/gandalf_ignore_instructions (MIT)      leolee99/NotInject (MIT)
#   uiuc-kang-lab/InjecAgent (MIT)                protectai/deberta-v3-base-prompt-injection-v2 (Apache-2.0, 739 MB)
set -euo pipefail
W="${1:?usage: fetch.sh <work-dir>}"; mkdir -p "$W"/{data/deepset,data/jackhhao,data/gandalf,data/notinject,data/injecagent,model/protectai}
dl(){ curl -sSL --fail -o "$2" "$1"; }
H=https://huggingface.co
D=$H/datasets/deepset/prompt-injections/resolve/4f61ecb038e9c3fb77e21034b22511b523772cdd/data
dl $D/train-00000-of-00001-9564e8b05b4757ab.parquet "$W/data/deepset/train.parquet"
dl $D/test-00000-of-00001-701d16158af87368.parquet "$W/data/deepset/test.parquet"
dl $H/datasets/jackhhao/jailbreak-classification/resolve/2f2ceeb39658696fd3f462403562b6eea5306287/default/jailbreak_dataset_full.csv "$W/data/jackhhao/full.csv"
G=$H/datasets/Lakera/gandalf_ignore_instructions/resolve/04737b65e90a6794ec227012e4a255a7def6344b/data
dl $G/test-00000-of-00001-bc92128b9288a6d1.parquet "$W/data/gandalf/test.parquet"
dl $G/train-00000-of-00001-ded53be747ff55cd.parquet "$W/data/gandalf/train.parquet"
dl $G/validation-00000-of-00001-94481a2a09ff2fff.parquet "$W/data/gandalf/validation.parquet"
N=$H/datasets/leolee99/NotInject/resolve/847ae76cf8fea5ed325429e569ae8cfef022d2e0/data
for s in one two three; do dl $N/NotInject_$s-00000-of-00001.parquet "$W/data/notinject/$s.parquet"; done
IA=f19c9f2c79a41046eb13c03c51a24c567a8ffa07
for f in test_cases_dh_base.json test_cases_ds_base.json; do dl https://raw.githubusercontent.com/uiuc-kang-lab/InjecAgent/$IA/data/$f "$W/data/injecagent/$f"; done
M=$H/protectai/deberta-v3-base-prompt-injection-v2/resolve/90c9989b1a342275dd0d1a95aad283c04e075671/onnx
for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json model.onnx; do dl $M/$f "$W/model/protectai/$f"; done
