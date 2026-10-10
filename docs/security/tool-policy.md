# Tool Policy

Which tools a model's response may call, and when. Off by default.

Detection asks whether a text looks like an attack. On text it was not written
against it stops one attack in five, and none of the instructions planted in a
tool result (see the [benchmark](/security/benchmark)): such an instruction is an
ordinary sentence in the wrong place. The tool policy does not read it. It
refuses the call the instruction asks for.

## The two rules

```yaml
security:
  tool_policy:
    enabled: true
    mode: enforce            # log_only: record what would be refused, refuse nothing
    allow: ["*"]             # tools the model may call at all
    deny: []                 # never, whatever allow says
    after_tool_result:       # tools that may be called in a turn that follows a tool result
      - "*Get*"
      - "*Read*"
      - "*Search*"
      - "*View*"
      - "*List*"
```

**`allow` / `deny`** decide which tools may be called at all.

**`after_tool_result`** is the one that stops an indirect injection. A tool
result is data from somewhere else: a web page, an inbox, a file. When the model
answers one by calling a tool, nobody asked for that call except, possibly, the
data. With this list set, a call made in a turn that follows a tool result is
refused unless the tool is on the list. List the tools that only read. Leave the
key out and a tool result restricts nothing; set it to `[]` and nothing may be
called after one.

A call the user really wants is not lost. If the agent asks ("the page says to
email the report to X, shall I?") and the user answers, the turn follows a user
message and the call goes through. The user's reply is the confirmation step.

Names are matched as shell-style patterns, **case-sensitively**: a tool name is
an identifier, and without case `*get*` matches `ManageTrafficLightState`
(mana-GE-T-raffic). Exact names are safer than patterns.

## What happens on a refusal

- **Non-streaming:** `403`, `Tool call '<name>' refused by policy: <reason>`.
- **Streaming:** the stream ends with
  `data: {"error":"tool_refused","message":"Tool call refused by policy"}`. The
  chunk that would have completed the call's name is not sent, so the client
  holds an unfinished call it cannot run. Text sent before the call stays sent.
- Either way the request is a row in the audit chain with `blocked = 1` and the
  tool's name, and `llm_proxy_tool_policy_total{decision="refused"}` counts it.
  In `log_only` mode nothing is refused and the counter's label is
  `would_refuse`.

## What it does not do

- It does not help when the untrusted text is inside the **user's own message**
  (a pasted page, retrieved context placed in the user turn). The proxy cannot
  tell those words from the user's.
- It stops a multi-step task that legitimately needs a state-changing tool
  after reading something ("look up the IBAN, then pay it") until the user
  confirms. That is the trade it makes.
- It judges the tool's **name and position**, not its arguments: an allowed
  read-only tool can still be asked to read something the user did not mean.
- The policy is global. Per-key policies are not implemented.
- It sees `tool_calls` (and the legacy `function_call`) in OpenAI-format
  responses, which is what every adapter returns.
