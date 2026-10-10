# PII Detection & Masking

Before a chat request is forwarded, personal data that the proxy recognises in the
messages is replaced by placeholders. In a non-streaming response the placeholders
are replaced by the original values again.

Masking is on by default. It is done by the plugin named `PII Masker` in
`plugins/manifest.yaml`. The name is historical: detection is by regular
expressions, or by Presidio when that is installed.

## What is masked, and what is not

Masked:

- the `content` of every message in `/v1/chat/completions`, when it is a string;
- the `text` of each part, when `content` is a list of parts;
- the prompt of `/v1/completions`, which becomes a message.

Not masked:

- the input of `/v1/embeddings`;
- tool definitions, and tool-call names and arguments;
- top-level fields other than `messages`.

What is not recognised by the detection in use is forwarded as it is.

## Detection

### Regular expressions (default)

Used when Presidio is not installed, which is the case for the shipped
`requirements.txt` and image.

| Label | What matches | Example |
|-------|--------------|---------|
| `EMAIL` | Email address | `user@example.com` |
| `IBAN` | Two letters, two digits, then 12 to 30 characters in groups | `DE89370400440532013000` |
| `PHONE_US` | Ten digits as 3-3-4, optionally separated by `-` or `.` | `555-123-4567` |
| `SSN` | US social security number as 3-2-4 with hyphens | `123-45-6789` |
| `CREDIT_CARD` | 15 digits starting 34 or 37, or 16 digits, that pass the Luhn check | `4111-1111-1111-1111` |
| `PHONE_INTL` | `+`, a country code, then groups of digits | `+1-555-0123` |
| `IP_ADDRESS` | IPv4 address | `10.0.0.1` |
| `API_KEY` | `sk`, `key`, `token`, `bearer` or `api_key`, then `-` or `_`, then 20 or more characters | `sk-abcdefghijklmnopqrstuvwxyz` |

Names, postal addresses and dates are not detected by the regular expressions.

### Presidio (optional)

When `presidio-analyzer` and `presidio-anonymizer` can be imported, Presidio is
used **instead of** the regular expressions:

```bash
pip install presidio-analyzer presidio-anonymizer
```

It is asked for 11 entity types, in English, at a score of 0.7 or more:
`EMAIL_ADDRESS`, `PHONE_NUMBER`, `US_SSN`, `CREDIT_CARD`, `PERSON`, `LOCATION`,
`IBAN_CODE`, `IP_ADDRESS`, `US_DRIVER_LICENSE`, `US_PASSPORT`, `DATE_TIME`.

The `API_KEY` pattern belongs to the regular expressions and is not applied when
Presidio is in use.

## Placeholders

Each match is replaced by a placeholder with a random identifier:

```
Input:  "Contact john@example.com for details"
Output: "Contact [PII_EMAIL_3f2a9c0e5b7d4a1f8e6c2b9d0a7f4e31] for details"
```

The mapping from placeholder to original value is kept in memory for the
duration of the request and belongs to that request only. It is not written to
disk.

```
request  → mask → provider sees placeholders
response ← restore ← provider's answer
```

Restoring happens in the `Post-Flight Sanitizer` plugin, on `message.content` of
each choice of a **non-streaming** response. In a streamed response the
placeholders are not restored: if the model repeats one, the caller receives the
placeholder.

## Pipeline Position

The masker runs in the pre-flight ring at priority 20, after authentication and
after the shield has scored the request, and before the cache lookup and routing.
Its `fail_policy` is `closed`: if the masker fails, the request is refused, not
forwarded unmasked.

## Configuration

There is no `config.yaml` key for masking. It is active when the plugin is enabled
(the default) and `security.enabled` is true (the default). To turn it off, disable
the plugin:

```bash
curl -X POST http://localhost:8090/api/v1/plugins/toggle \
  -H "Authorization: Bearer your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "PII Masker", "enabled": false}'
```

`plugins/manifest.yaml` also contains `ONNX PII Masker`, an alternative masker that
uses a local ONNX model. It is disabled by default and needs additional packages
and a model download.

## Audit log

Audit rows hold request metadata, not prompts or responses, so they contain no
message text to mask.
