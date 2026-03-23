// k6 baseline load test for LLMProxy
// Usage: k6 run tests/load/k6_baseline.js
//
// Requires a running LLMProxy instance at http://localhost:8090
// with at least one configured provider.
//
// Thresholds:
//   - P95 latency < 500ms (health endpoint)
//   - P95 latency < 5000ms (chat completions)
//   - Error rate < 1%

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8090';
const API_KEY = __ENV.API_KEY || 'test-key';

const errorRate = new Rate('errors');
const healthLatency = new Trend('health_latency');
const chatLatency = new Trend('chat_latency');

export const options = {
  stages: [
    { duration: '10s', target: 5 },   // ramp up
    { duration: '30s', target: 10 },   // sustained load
    { duration: '10s', target: 0 },    // ramp down
  ],
  thresholds: {
    errors: ['rate<0.01'],                    // <1% error rate
    health_latency: ['p(95)<500'],            // health P95 < 500ms
    chat_latency: ['p(95)<5000'],             // chat P95 < 5s
    http_req_duration: ['p(99)<10000'],       // overall P99 < 10s
  },
};

export default function () {
  // Health check (fast, always available)
  const healthRes = http.get(`${BASE_URL}/health`);
  healthLatency.add(healthRes.timings.duration);
  check(healthRes, {
    'health status 200': (r) => r.status === 200,
    'health has pool_size': (r) => JSON.parse(r.body).pool_size !== undefined,
  });
  errorRate.add(healthRes.status !== 200);

  // Metrics endpoint
  const metricsRes = http.get(`${BASE_URL}/metrics`);
  check(metricsRes, {
    'metrics status 200': (r) => r.status === 200,
  });

  // Chat completions (requires auth + provider)
  const chatRes = http.post(
    `${BASE_URL}/v1/chat/completions`,
    JSON.stringify({
      model: 'fast',
      messages: [{ role: 'user', content: 'Say "ok" and nothing else.' }],
      max_tokens: 5,
    }),
    {
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${API_KEY}`,
      },
      timeout: '10s',
    }
  );
  chatLatency.add(chatRes.timings.duration);
  check(chatRes, {
    'chat status 200 or 401': (r) => r.status === 200 || r.status === 401,
  });
  errorRate.add(chatRes.status >= 500);

  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(
      {
        health_p95_ms: data.metrics.health_latency?.values?.['p(95)'] || 0,
        chat_p95_ms: data.metrics.chat_latency?.values?.['p(95)'] || 0,
        error_rate: data.metrics.errors?.values?.rate || 0,
        total_requests: data.metrics.http_reqs?.values?.count || 0,
      },
      null,
      2
    ),
  };
}
