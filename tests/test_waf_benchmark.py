"""The detection numbers the project publishes are the ones it measured.

The README used to state 100% coverage of prompt injection, measured on a
corpus written alongside the detector. On prompts the detector was not written
against it stops about one attack in five. The published figures now come from
``scripts/waf_eval/results.json`` and these tests keep three things true:

* the tables in the README and the docs are the ones generated from that file;
* the part of the benchmark that needs no download, the held-out set, still
  gives the figures the file records, so a change to the detector cannot leave
  stale numbers behind;
* the old claim does not come back.
"""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL = ROOT / "scripts" / "waf_eval"
sys.path.insert(0, str(EVAL))

import heldout  # noqa: E402
import predict_waf  # noqa: E402

RESULTS = json.loads((EVAL / "results.json").read_text())


def test_the_published_tables_are_generated_from_the_results_file():
    done = subprocess.run(
        [sys.executable, str(EVAL / "render.py"), "--check"], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture(scope="module")
async def heldout_verdicts():
    firewall = predict_waf.make_firewall()

    async def judge(samples):
        out = []
        for n, (family, text) in enumerate(samples):
            v = await predict_waf.verdict(firewall, [{"role": "user", "content": text}], f"h{n:05d}-abcdefghij")
            out.append((family, v["fw"] or v["shield"]))
        return out

    return await judge(heldout.ATTACKS), await judge(heldout.BENIGN)


async def test_the_held_out_set_still_gives_the_recorded_figures(heldout_verdicts):
    """If this fails the detector changed: run `make waf-bench` and commit the
    new results, do not edit the numbers."""
    attacks, benign = heldout_verdicts
    recorded = {(d["name"], d["kind"]): d for d in RESULTS["datasets"]}

    assert (len(attacks), sum(hit for _, hit in attacks)) == (
        recorded[("heldout", "attack")]["n"],
        recorded[("heldout", "attack")]["waf"],
    )
    assert (len(benign), sum(hit for _, hit in benign)) == (
        recorded[("heldout", "benign")]["n"],
        recorded[("heldout", "benign")]["waf"],
    )


async def test_the_held_out_families_match_too(heldout_verdicts):
    attacks, _ = heldout_verdicts
    measured: dict[str, int] = {}
    for family, hit in attacks:
        measured[family] = measured.get(family, 0) + hit
    recorded = {f["family"]: f["waf"] for f in RESULTS["heldout_families"] if f["kind"] == "attack"}

    assert measured == recorded


def test_the_readme_states_the_measured_rate_and_not_full_coverage():
    readme = (ROOT / "README.md").read_text()
    a = RESULTS["attacks"]

    assert f"**{round(100 * a['waf'] / a['n'])}%**" in readme
    assert "All 27 corpus variants caught" not in readme
    assert "LLM01 — Prompt Injection      | **100 %**" not in readme


def test_the_results_file_says_what_it_measured():
    assert RESULTS["attacks"]["n"] == sum(d["n"] for d in RESULTS["datasets"] if d["kind"] == "attack")
    assert {d["licence"] for d in RESULTS["datasets"]} <= {"Apache-2.0", "MIT", "this repository"}
    assert RESULTS["llmproxy_version"] and RESULTS["measured"]
