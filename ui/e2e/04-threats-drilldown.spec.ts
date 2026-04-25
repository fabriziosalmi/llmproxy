import { test } from '@playwright/test';

// TODO(phase-C): implement once authenticated fixtures are available.
// This spec covers the threat investigation flow shipped in Sprint 2:
//   1. Land on the Threats tab
//   2. Click any threat row → drilldown drawer opens
//   3. Click "Explain" → explain pane renders with the rule that fired
//   4. Drilldown shows the time range from the global picker
test.skip('threat row → drilldown → explain pane', async () => {
    // Deferred to Phase C.
});
