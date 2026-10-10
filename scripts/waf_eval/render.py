"""Write the benchmark tables into README.md and docs/security/benchmark.md.

    python scripts/waf_eval/render.py            # rewrite the marked blocks
    python scripts/waf_eval/render.py --check    # exit 1 if they are out of date

The numbers in the documentation come from results.json and from nowhere else:
tests/test_waf_benchmark.py runs the --check, so a number edited by hand, or a
results file updated without the documents, fails the build.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESULTS = pathlib.Path(__file__).parent / "results.json"
TARGETS = {"README.md": "summary", "docs/security/benchmark.md": "full"}


def _pct(part: int, whole: int) -> str:
    value = 100 * part / whole if whole else 0.0
    return f"{value:.1f}%" if 0 < value < 10 else f"{value:.0f}%"


def summary(r: dict) -> str:
    a, b = r["attacks"], r["benign"]
    by = {(d["name"], d["kind"]): d for d in r["datasets"]}
    indirect = by[("injecagent", "attack")]
    lines = [
        f"Measured {r['measured']} on llmproxy {r['llmproxy_version']}, default configuration, "
        f"{a['n'] + b['n']:,} prompts from four public datasets, one indirect-injection benchmark and a held-out set:",
        "",
        "| | Stopped |",
        "|---|---|",
        f"| Attack prompts ({a['n']:,}) | **{_pct(a['waf'], a['n'])}** |",
        f"| of which: an instruction hidden in a tool result ({indirect['n']:,}) | **{_pct(indirect['waf'], indirect['n'])}** |",
        f"| Benign prompts ({b['n']:,}), stopped by mistake | **{_pct(b['waf'], b['n'])}** |",
    ]
    return "\n".join(lines)


def full(r: dict) -> str:
    scored = r["classifier"]["scored"]
    lines = [
        f"Measured {r['measured']} on llmproxy {r['llmproxy_version']} (byte firewall and SecurityShield, default "
        f"configuration)" + (f", against `{r['classifier']['id']}` at threshold {r['classifier']['threshold']}." if scored else "."),
        "",
        "| Dataset | Kind | Prompts | llmproxy | Classifier |",
        "|---|---|---:|---:|---:|",
    ]
    for d in r["datasets"]:
        lines.append(
            f"| `{d['source']}` | {d['kind']} | {d['n']:,} | {_pct(d['waf'], d['n'])} | "
            f"{_pct(d['classifier'], d['n']) if scored else 'n/a'} |"
        )
    a, b = r["attacks"], r["benign"]
    lines += [
        f"| **All attacks, stopped** | | **{a['n']:,}** | **{_pct(a['waf'], a['n'])}** | "
        f"**{_pct(a['classifier'], a['n']) if scored else 'n/a'}** |",
        f"| **All benign, stopped by mistake** | | **{b['n']:,}** | **{_pct(b['waf'], b['n'])}** | "
        f"**{_pct(b['classifier'], b['n']) if scored else 'n/a'}** |",
        "",
        "The held-out set by family (stopped / prompts):",
        "",
        "| Kind | Family | llmproxy | Classifier |",
        "|---|---|---:|---:|",
    ]
    for f in r["heldout_families"]:
        lines.append(f"| {f['kind']} | {f['family']} | {f['waf']} / {f['n']} | {f['classifier']} / {f['n']} |")
    if scored:
        hard, clean = r["benign_hard"], r.get("classifier_clean_tool_results")
        lines += ["", "The classifier at other thresholds (attacks stopped; hard benign prompts stopped by mistake):", "",
                  "| Threshold | Attacks | Hard benign |", "|---|---:|---:|"]
        for t in r["classifier_thresholds"]:
            lines.append(f"| {t['threshold']} | {_pct(t['attacks_stopped'], t['attacks'])} | "
                         f"{_pct(t['hard_benign_stopped'], t['hard_benign'])} |")
        lines += ["", f"Hard benign prompts are the trigger-word set and the held-out negatives ({hard['n']} prompts)."]
        if clean:
            lines.append(
                f"On {clean['n']:,} clean tool results (the indirect-injection templates with the attacker's text "
                f"replaced by an ordinary sentence) the classifier flagged {_pct(clean['flagged'], clean['n'])}."
            )
        lat, wl = r["classifier_latency_ms"], r["waf_latency_ms"]
        lines.append(f"Latency per prompt on a laptop CPU: llmproxy median {wl['median']} ms; classifier median "
                     f"{lat['median']} ms, 95th percentile {lat['p95']} ms.")
    return "\n".join(lines)


def render(text: str, block: str) -> str:
    pattern = re.compile(r"(<!-- waf-bench:start -->\n)(?:.*?\n)?(<!-- waf-bench:end -->)", re.S)
    if not pattern.search(text):
        raise SystemExit("no <!-- waf-bench:start --> ... <!-- waf-bench:end --> block found")
    return pattern.sub(lambda m: m.group(1) + block + "\n" + m.group(2), text)


def main() -> int:
    results = json.loads(RESULTS.read_text())
    stale = []
    for rel, kind in TARGETS.items():
        path = ROOT / rel
        current = path.read_text()
        wanted = render(current, summary(results) if kind == "summary" else full(results))
        if wanted != current:
            stale.append(rel)
            if "--check" not in sys.argv:
                path.write_text(wanted)
    if "--check" in sys.argv and stale:
        print("out of date:", ", ".join(stale), "- run scripts/waf_eval/render.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
