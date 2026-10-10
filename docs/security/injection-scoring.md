# Injection Scoring & Trajectory Detection

The shield (`SecurityShield`, `core/security.py`) inspects each request for prompt
injection before it is forwarded. It is on by default (`security.enabled`).

The detection is lexical: regular expressions and a character-trigram comparison
against a list of known phrasings. On the [benchmark](/security/benchmark), the
firewall and the shield together stop 21% of 3,035 attack prompts and none of
1,054 instructions planted in a tool result, and refuse 0.1% of 2,112 benign
prompts. A reworded attack is not recognised.

## What is inspected

All text in the request that reaches the model: the content of every message
(strings and the text parts of multi-part content), tool-call names and arguments,
the top-level `prompt`, `system`, `instructions` and `input` fields, and the name
and description of each tool definition. The pieces are joined and scored as one
text.

## Checks, in order

The first check that fails refuses the request.

1. **Size.** The body is larger than `security.max_payload_size_kb` (512), has
   more than `security.max_messages` (50) messages, or is longer than 2,000
   characters with fewer than one distinct word in five.
2. **Session trajectory.** See below.
3. **Threat ledger.** The same client address, or the same credential, has sent at
   least 3 requests with a non-zero pattern score in the last 600 seconds and
   their scores add up to 3.0 or more. On by default
   (`security.threat_ledger.enabled`, `threshold`, `min_events`,
   `window_seconds`).
4. **Pattern score.** 30 regular expressions, each with a weight between 0.6 and
   0.95; the score is the sum of the weights of those that match. They are applied
   to the lower-cased text and to a normalised form (Unicode NFKC, zero-width
   characters removed, look-alike Cyrillic and Greek letters and common leetspeak
   digits mapped to Latin letters). Seven of the patterns are instruction-override
   phrases in Italian, German, French, Spanish, Portuguese, Chinese and Russian.
   **A score of 0.85 or more refuses the request.**
5. **Trigram comparison.** The text is compared with 157 known phrasings
   (`data/injection_corpus.yaml`) by Jaccard similarity of character trigrams,
   over a sliding window. A similarity of `security.semantic_analysis.threshold`
   (0.35) or more counts as a match. This compares characters, not meaning. On by
   default (`security.semantic_analysis.enabled`).
6. **Composite.** `0.4 × min(pattern score / 2, 1) + 0.35 × trigram similarity +
   0.25 × min(trajectory sum / 3, 1)`.
   - 0.7 or more: refused.
   - 0.3 or less: passed, unless the pattern score is at least
     `security.confidence.regex_escalate_floor` (0.6), in which case it is
     treated as the middle band.
   - In the middle band the request is refused when the composite is at least
     `security.confidence.gray_zone_fallback` (0.5), and passed otherwise.
7. **Links.** URLs in the text are checked against
   `security.link_sanitization.blocked_domains`, and, when configured, against
   the homograph and [risk-scoring](/security/fqdn-risk-scoring) checks.

With the default weights, a trigram match with no pattern match does not refuse a
request: its largest contribution to the composite is 0.35. It decides the outcome
only together with a pattern match or a trajectory score. A single pattern below
0.85, such as `system prompt` (0.8), does not refuse a request on its own either.

The code can hand the middle band to a language model for a verdict instead of
the 0.5 rule. No model is wired to the shield in the shipped proxy, so that path
does not run.

## The refusal

`403`, in the OpenAI error envelope:

```json
{
  "error": {
    "message": "Request blocked by content security policy",
    "type": "permission_error",
    "param": null,
    "code": "forbidden"
  },
  "detail": "Request blocked by content security policy"
}
```

The score is not returned to the caller. The score and the matched patterns are
written to the process log at warning level. The trajectory, ledger, size and link
checks use their own messages (for example
`Conversation trajectory indicates security risk (Multi-turn violation)`).

A refused request is a row in the audit chain with `blocked = 1` and the message
as the reason.

The refused request's model and messages are also remembered for
`caching.negative_cache.ttl` seconds (300). A request with the same model and
messages sent again in that time is refused with the same message, without being
scored. This memory is shared by all callers, and it also holds requests refused
by the trajectory and ledger checks: after a session is refused for its
trajectory, the same request from another caller is refused for those 300
seconds, whatever its own content scores.

## Multi-Turn Trajectory Detection

The shield keeps the pattern score of each request per session. A session is the
credential (a keyed hash of it), or, with authentication off, the client address
together with the `User-Agent` and `Accept-Language` headers.

A request is refused when the session has at least three scores from the last 5
minutes and the last three add up to more than 1.5. The current request's score is
included. Three requests that each match one pattern of weight 0.7 are enough:

```
Request 1: score 0.7 → passed
Request 2: score 0.7 → passed
Request 3: score 0.7 → sum 2.1 > 1.5 → refused
```

The rule is a sum over the last three scores. It does not look at whether the
scores are rising.

The memory is per process. It is lost on restart and on a configuration reload,
and `POST /api/v1/security/reset` clears it. It holds up to 10,000 sessions; a
session idle for an hour is dropped.

Scores of refused requests count too. After two requests refused for their
pattern score (0.85 or more each) within 5 minutes, the session's next request is
refused for its trajectory even if it matches no pattern.

## Pipeline Position

```
firewall → route authentication → negative cache → shield → plugin INGRESS ring → ...
```

The shield runs in `process_proxy_request` (`proxy/request_pipeline.py`), for
`/v1/chat/completions` and `/v1/completions`, after the caller has authenticated
and before the first plugin ring. It scores the request as sent: PII masking runs
later, in the pre-flight ring. `/v1/embeddings` calls the shield directly on its
input.

## Responses

The checks above apply to requests. Responses are checked separately, by a
different and shorter pattern list: non-streaming responses in the post-flight
ring, and streams by a guard that scans the text as it arrives and can cut the
stream after the text has already been sent. See the
[security overview](/security/overview#response-sanitisation).

## Complement: Topic Blocklist

To refuse requests by subject rather than by injection wording, use the
[Topic Blocklist](/plugins/marketplace#topic-blocklist) plugin (off by default),
which matches keywords, whole words or regular expressions.
