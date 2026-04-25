import { test } from '@playwright/test';

// TODO(phase-C): implement once we have a deterministic auth fixture.
// This spec covers the "add endpoint" flow:
//   1. Authenticate with a proxy API key from env (LLMPROXY_E2E_KEY)
//   2. Navigate to the Endpoints tab
//   3. Open the add-endpoint dialog
//   4. Submit a fake OpenAI-compatible URL
//   5. Assert the new endpoint appears in the registry list
test.skip('user can add an OpenAI-compatible endpoint and see it in the registry', async () => {
    // Implementation deferred to Phase C alongside the auth fixture work.
});
