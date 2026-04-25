import { test } from '@playwright/test';

// TODO(phase-C): implement once we know the palette keybinding contract.
// This spec covers Sprint 2's command palette:
//   1. Press Cmd/Ctrl+K
//   2. Type a tab name
//   3. Press Enter
//   4. Assert the active tab matches and the palette closes
test.skip('command palette opens with Cmd+K and jumps to a tab', async () => {
    // Deferred to Phase C.
});
