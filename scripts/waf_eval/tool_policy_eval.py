"""What the tool policy does on the indirect-injection benchmark.

    python scripts/waf_eval/tool_policy_eval.py <work-dir>     # repository root, its venv

For each InjecAgent case the conversation is built as the proxy receives it
(the user's request, the agent's tool call, the tool result carrying the
planted instruction) and the model is assumed to OBEY the instruction: its
response calls the attacker's tool. That is the worst case, and it is the one
the detection layer does nothing about. The policy is then asked whether that
call may be made. The user's own call, the one the task needs, is checked too.

No model is run and no text is read: the result depends on tool names and on
where in the conversation the call comes. It is merged into results.json.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.tool_policy import ToolPolicy, follows_tool_result  # noqa: E402

#: The example policy from docs/security/tool-policy.md.
READ_ONLY = ["*Get*", "*Read*", "*Search*", "*View*", "*List*", "*Navigate*"]
POLICY = ToolPolicy.from_config({"security": {"tool_policy": {"enabled": True, "after_tool_result": READ_ONLY}}})


def _call(name: str, call_id: str) -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def main(work: str) -> None:
    out = {"policy": {"after_tool_result": READ_ONLY}, "cases": 0, "attacks_stopped": 0,
           "user_calls": 0, "user_calls_refused": 0, "by_type": {}}
    for kind, label in (("dh", "direct harm"), ("ds", "data stealing")):
        with open(f"{work}/data/injecagent/test_cases_{kind}_base.json") as f:
            cases = json.load(f)
        stopped = 0
        for r in cases:
            user = [{"role": "user", "content": r["User Instruction"]}]
            out["user_calls"] += 1
            out["user_calls_refused"] += bool(POLICY.refusal(r["User Tool"], follows_tool_result(user)))

            tool_result = r["Tool Response"] if isinstance(r["Tool Response"], str) else json.dumps(r["Tool Response"])
            messages = user + [_call(r["User Tool"], "c0"), {"role": "tool", "tool_call_id": "c0", "content": tool_result}]
            refused = False
            for step, attacker_tool in enumerate(r["Attacker Tools"], start=1):
                if POLICY.refusal(attacker_tool, follows_tool_result(messages)):
                    refused = True
                    break
                # The model obeyed and the step went through: its result is the next thing it sees.
                messages = messages + [_call(attacker_tool, f"c{step}"),
                                       {"role": "tool", "tool_call_id": f"c{step}", "content": "{}"}]
            stopped += refused
        out["by_type"][label] = {"cases": len(cases), "stopped": stopped}
        out["cases"] += len(cases)
        out["attacks_stopped"] += stopped

    results_path = pathlib.Path(__file__).parent / "results.json"
    results = json.loads(results_path.read_text())
    results["tool_policy"] = out
    results_path.write_text(json.dumps(results, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
