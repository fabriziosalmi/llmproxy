import { describe, expect, it } from 'vitest';
import { buildKpiData, extractMetric } from './parseMetrics';

const SAMPLE = `
# HELP llm_proxy_requests_total Total requests
# TYPE llm_proxy_requests_total counter
llm_proxy_requests_total{endpoint="openai"} 100
llm_proxy_requests_total{endpoint="anthropic"} 40
llm_proxy_injection_blocked_total 5
llm_proxy_auth_failures_total 3
llm_proxy_request_errors_total 2
llm_proxy_token_usage_total 1500
`.trim();

describe('extractMetric()', () => {
    it('sums every labeled instance', () => {
        expect(extractMetric(SAMPLE, 'llm_proxy_requests_total')).toBe(140);
    });

    it('returns 0 for an unknown counter', () => {
        expect(extractMetric(SAMPLE, 'llm_proxy_nope')).toBe(0);
    });

    it('skips comment lines', () => {
        expect(extractMetric('# HELP foo\nfoo 7\n', 'foo')).toBe(7);
    });
});

describe('buildKpiData()', () => {
    it('aggregates Prometheus + health into the canonical KPI shape', () => {
        const data = buildKpiData(SAMPLE, { uptime_seconds: 7200, pool_size: 4, pool_healthy: 3 });
        expect(data.requests).toBe(140);
        expect(data.blocked).toBe(8); // 5 injection + 3 auth
        expect(data.piiMasked).toBe(5);
        expect(data.errors).toBe(2);
        expect(data.tokens).toBe(1500);
        expect(data.passRatePct).toBeCloseTo((1 - 8 / 140) * 100, 5);
        expect(data.uptimeSeconds).toBe(7200);
        expect(data.pool).toEqual({ healthy: 3, total: 4 });
    });

    it('falls back to 100% pass rate and null pool when there is no traffic / health', () => {
        const data = buildKpiData('', null);
        expect(data.requests).toBe(0);
        expect(data.passRatePct).toBe(100);
        expect(data.uptimeSeconds).toBeNull();
        expect(data.pool).toBeNull();
    });

    it('clamps pass rate above zero when blocked > requests (impossible but defensive)', () => {
        const odd = 'llm_proxy_requests_total 1\nllm_proxy_injection_blocked_total 5\n';
        const data = buildKpiData(odd, null);
        expect(data.passRatePct).toBe(0);
    });
});
