# PII Detection & Masking

LLMProxy includes dual-mode PII detection that prevents sensitive personal information from reaching LLM providers.

## Detection Modes

### Presidio NLP (opt-in)

When `presidio-analyzer` is installed, LLMProxy uses Microsoft Presidio for NLP-powered entity recognition. Supports 18 entity types including names, addresses, and context-dependent patterns.

```bash
pip install presidio-analyzer presidio-anonymizer
```

### Regex Fallback (always available)

Built-in regex patterns detect common PII without external dependencies:

| Pattern | Example |
|---------|---------|
| Email | `user@example.com` |
| Phone | `+1-555-0123` |
| SSN | `123-45-6789` |
| Credit Card | `4111-1111-1111-1111` |
| IBAN | `DE89370400440532013000` |

## Vault Tokenization

PII is replaced with vault tokens, not deleted:

```
Input:  "Contact john@example.com for details"
Output: "Contact [PII_EMAIL_a7b3c] for details"
```

The original values are stored in an in-memory vault keyed by token. Responses can be **demasked** to restore original PII before returning to the client.

## How It Works

```
mask_pii(text) → tokenized text + vault entries
                          ↓
              Request sent to LLM provider
                          ↓
demask_pii(response) → original PII restored
```

## Pipeline Position

PII masking runs as the **PII Neural Masker** default plugin in the PRE_FLIGHT ring (priority 20), after auth but before cache lookup and routing.

## Configuration

PII masking is always active when the security module is enabled. The audit trail can optionally mask PII in logs:

```yaml
logging:
  audit_trail:
    enabled: true
    mask_pii: true
```
