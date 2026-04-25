import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { parseEndpointBreakdown, renderEndpointBreakdown } from './EndpointBreakdown';

const SAMPLE = `
# HELP llm_proxy_requests_total Total requests by endpoint
# TYPE llm_proxy_requests_total counter
llm_proxy_requests_total{endpoint="openai",method="POST"} 100
llm_proxy_requests_total{endpoint="anthropic"} 250
llm_proxy_requests_total{endpoint="dead"} 10
llm_proxy_request_errors_total{endpoint="dead"} 8
llm_proxy_request_errors_total{endpoint="anthropic"} 1
`.trim();

let host: HTMLElement;

beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
});

afterEach(() => {
    host.remove();
});

describe('parseEndpointBreakdown', () => {
    it('aggregates requests + errors per endpoint', () => {
        const map = parseEndpointBreakdown(SAMPLE);
        expect(map.openai).toEqual({ requests: 100, errors: 0 });
        expect(map.anthropic).toEqual({ requests: 250, errors: 1 });
        expect(map.dead).toEqual({ requests: 10, errors: 8 });
    });

    it('returns an empty object on empty input', () => {
        expect(parseEndpointBreakdown('')).toEqual({});
    });

    it('skips comment lines', () => {
        const out = parseEndpointBreakdown('# HELP\n# TYPE\nllm_proxy_requests_total{endpoint="x"} 7\n');
        expect(out.x).toEqual({ requests: 7, errors: 0 });
    });
});

describe('renderEndpointBreakdown', () => {
    it('renders one row per endpoint with err% color tier', () => {
        renderEndpointBreakdown(host, SAMPLE);
        // Three rows.
        const rows = host.querySelectorAll('div.flex.items-center.justify-between');
        expect(rows).toHaveLength(3);
        // openai: 0% err → emerald.
        expect(host.textContent).toContain('openai');
        expect(host.textContent).toContain('100 req');
        expect(host.textContent).toContain('0.0% err');
        // anthropic: 0.4% err → amber tier.
        expect(host.textContent).toContain('250 req');
        // dead: 80% err → rose tier.
        expect(host.textContent).toContain('80.0% err');
    });

    it('shows the empty state when no per-endpoint counters are exposed', () => {
        renderEndpointBreakdown(host, '# HELP nothing\n');
        expect(host.textContent).toContain('No per-endpoint data yet');
    });
});
