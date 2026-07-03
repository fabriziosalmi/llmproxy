import { describe, it, expect } from 'vitest';
import { getScalar, setScalar, hasPath, parseScalarToken, formatValue } from './yamlEdit';

const SAMPLE = `# top comment
server:
  host: 0.0.0.0
  port: 8090          # canonical UI/health port
  auth:
    enabled: true
    api_keys_env: LLM_PROXY_API_KEYS

security:
  enabled: true
  max_payload_size_kb: 512
  link_sanitization:
    enabled: true
    blocked_domains: ["malicious-site.com", "phishing.net"]
    risk_scoring:
      enabled: false
      block_threshold: 0.7   # 0..1; lower = stricter
      log_only: false
    homograph_protection:
      enabled: true
      log_only: true
      brands: ["paypal.com", "google.com"]

logging:
  level: INFO
`;

describe('getScalar', () => {
    it('reads booleans', () => {
        expect(getScalar(SAMPLE, 'security.enabled')).toBe(true);
        expect(getScalar(SAMPLE, 'security.link_sanitization.risk_scoring.enabled')).toBe(false);
        expect(getScalar(SAMPLE, 'security.link_sanitization.homograph_protection.log_only')).toBe(true);
    });
    it('reads numbers', () => {
        expect(getScalar(SAMPLE, 'server.port')).toBe(8090);
        expect(getScalar(SAMPLE, 'security.max_payload_size_kb')).toBe(512);
        expect(getScalar(SAMPLE, 'security.link_sanitization.risk_scoring.block_threshold')).toBe(0.7);
    });
    it('reads bare and env-ref strings', () => {
        expect(getScalar(SAMPLE, 'server.host')).toBe('0.0.0.0');
        expect(getScalar(SAMPLE, 'server.auth.api_keys_env')).toBe('LLM_PROXY_API_KEYS');
        expect(getScalar(SAMPLE, 'logging.level')).toBe('INFO');
    });
    it('reads inline string lists', () => {
        expect(getScalar(SAMPLE, 'security.link_sanitization.blocked_domains')).toEqual([
            'malicious-site.com',
            'phishing.net',
        ]);
        expect(getScalar(SAMPLE, 'security.link_sanitization.homograph_protection.brands')).toEqual([
            'paypal.com',
            'google.com',
        ]);
    });
    it('returns undefined for a missing path', () => {
        expect(getScalar(SAMPLE, 'security.threat_ledger.threshold')).toBeUndefined();
        expect(getScalar(SAMPLE, 'nope.at.all')).toBeUndefined();
    });
    it('does not confuse a nested key with the wrong parent', () => {
        // risk_scoring.log_only=false vs homograph_protection.log_only=true
        expect(getScalar(SAMPLE, 'security.link_sanitization.risk_scoring.log_only')).toBe(false);
    });
});

describe('hasPath', () => {
    it('is true for existing and false for missing', () => {
        expect(hasPath(SAMPLE, 'server.port')).toBe(true);
        expect(hasPath(SAMPLE, 'server.timeout')).toBe(false);
    });
});

describe('setScalar — in-place replace preserves everything else', () => {
    it('flips a boolean and preserves the trailing comment', () => {
        const out = setScalar(SAMPLE, 'security.link_sanitization.homograph_protection.log_only', false);
        expect(getScalar(out, 'security.link_sanitization.homograph_protection.log_only')).toBe(false);
        // Only that one line changed; comment on block_threshold still there.
        expect(out).toContain('block_threshold: 0.7   # 0..1; lower = stricter');
        expect(out).toContain('port: 8090          # canonical UI/health port');
    });
    it('changes a number while keeping its comment', () => {
        const out = setScalar(SAMPLE, 'security.link_sanitization.risk_scoring.block_threshold', 0.5);
        expect(out).toContain('block_threshold: 0.5   # 0..1; lower = stricter');
        expect(getScalar(out, 'security.link_sanitization.risk_scoring.block_threshold')).toBe(0.5);
    });
    it('touches exactly one line (diff of 1)', () => {
        const before = SAMPLE.split('\n');
        const after = setScalar(SAMPLE, 'security.enabled', false).split('\n');
        expect(after.length).toBe(before.length);
        const changed = before.filter((l, i) => l !== after[i]);
        expect(changed).toEqual(['  enabled: true']);
    });
    it('replaces an inline string list', () => {
        const out = setScalar(SAMPLE, 'security.link_sanitization.homograph_protection.brands', [
            'paypal.com',
            'google.com',
            'apple.com',
        ]);
        expect(getScalar(out, 'security.link_sanitization.homograph_protection.brands')).toEqual([
            'paypal.com',
            'google.com',
            'apple.com',
        ]);
    });
    it('quotes list items that need quoting, leaves safe ones bare-ish', () => {
        const out = setScalar(SAMPLE, 'security.link_sanitization.blocked_domains', ['evil.com', 'a b.com']);
        // safe domain stays bare; the one with a space is quoted (valid YAML flow list)
        expect(out).toContain('[evil.com, "a b.com"]');
    });
});

describe('setScalar — insertion of missing keys', () => {
    it('inserts a missing leaf under an existing parent block', () => {
        const out = setScalar(SAMPLE, 'server.auth.enabled', true); // exists → replace
        const out2 = setScalar(out, 'server.timeout', 30); // missing leaf under server
        expect(getScalar(out2, 'server.timeout')).toBe(30);
        // inserted at 2-space indent under server
        expect(out2).toMatch(/\n {2}timeout: 30/);
    });
    it('inserts a missing nested chain under an existing grandparent', () => {
        const out = setScalar(SAMPLE, 'security.threat_ledger.threshold', 3);
        expect(getScalar(out, 'security.threat_ledger.threshold')).toBe(3);
        expect(out).toMatch(/\n {2}threat_ledger:\n {4}threshold: 3/);
    });
    it('inserts a missing top-level section at root', () => {
        const out = setScalar(SAMPLE, 'budget.daily_limit', 100);
        expect(getScalar(out, 'budget.daily_limit')).toBe(100);
        expect(out).toMatch(/\nbudget:\n {2}daily_limit: 100/);
    });
    it('round-trips after insertion', () => {
        let out = setScalar(SAMPLE, 'security.zero_trust.enabled', true);
        out = setScalar(out, 'security.zero_trust.enabled', false);
        expect(getScalar(out, 'security.zero_trust.enabled')).toBe(false);
    });
});

describe('setScalar — block-list to inline', () => {
    it('converts a block-style list to inline and drops the item lines', () => {
        const blockCfg = `security:
  link_sanitization:
    blocked_domains:
      - one.com
      - two.com
    enabled: true
`;
        const out = setScalar(blockCfg, 'security.link_sanitization.blocked_domains', ['three.com']);
        expect(getScalar(out, 'security.link_sanitization.blocked_domains')).toEqual(['three.com']);
        // sibling key preserved, item lines gone
        expect(getScalar(out, 'security.link_sanitization.enabled')).toBe(true);
        expect(out).not.toContain('- one.com');
    });
});

describe('comment/quote edge cases', () => {
    it('does not treat a # inside a URL value as a comment', () => {
        const cfg = `webhooks:\n  url: https://example.com/path#anchor\n`;
        expect(getScalar(cfg, 'webhooks.url')).toBe('https://example.com/path#anchor');
        const out = setScalar(cfg, 'webhooks.url', 'https://new.example.com/x#y');
        expect(getScalar(out, 'webhooks.url')).toBe('https://new.example.com/x#y');
    });
    it('handles a # inside a quoted string', () => {
        const cfg = `a:\n  b: "has # hash"\n`;
        expect(getScalar(cfg, 'a.b')).toBe('has # hash');
    });
});

describe('parseScalarToken / formatValue units', () => {
    it('parses primitives', () => {
        expect(parseScalarToken('true')).toBe(true);
        expect(parseScalarToken('42')).toBe(42);
        expect(parseScalarToken('-1.5')).toBe(-1.5);
        expect(parseScalarToken('null')).toBeNull();
        expect(parseScalarToken('hello')).toBe('hello');
        expect(parseScalarToken('"quoted"')).toBe('quoted');
        expect(parseScalarToken('[a, "b c"]')).toEqual(['a', 'b c']);
    });
    it('formats primitives', () => {
        expect(formatValue(true)).toBe('true');
        expect(formatValue(0.7)).toBe('0.7');
        expect(formatValue('LLM_PROXY_KEYS')).toBe('LLM_PROXY_KEYS');
        expect(formatValue('has space')).toBe('"has space"');
        expect(formatValue(['a.com', 'b.com'])).toBe('[a.com, b.com]');
    });
});
