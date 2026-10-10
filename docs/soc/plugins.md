# Plugins

The Plugins screen shows one card for each loaded plugin and has controls to reload, roll back, install and uninstall. It refreshes every 30 seconds.

## What is listed

The cards come from `GET /api/v1/plugins`, which returns the plugins the engine has loaded. A plugin that is disabled in the manifest is not loaded and has no card, so it cannot be enabled from this screen. With the shipped manifest the screen shows 11 plugins.

The cards are shown in one grid. They are not grouped by ring.

## Plugin card

Each card shows:

- Name, and version when the manifest gives one
- Ring, timeout and fail policy
- A dot for the enabled state
- Description
- Calls, blocks, error rate and average latency, from `GET /api/v1/plugins/stats`
- P50, P95 and P99 latency, once there are samples
- The fields of the plugin's `ui_schema` with their `default` values, marked read-only. These are the defaults declared in the schema, not the values in use

The timeout on the card is the manifest's `timeout_ms`. For a class plugin the engine enforces the class attribute instead, which can differ.

The screen has no form to change a plugin's configuration, and the API has no route for it. A plugin's settings are the `config` block of its manifest entry.

## Actions

| Control | Call | Notes |
|---------|------|-------|
| **Inspect** | none | Opens the plugin's detail panel |
| **Disable / Enable** | `POST /api/v1/plugins/toggle` | Writes `enabled` in `plugins/manifest.yaml`, then hot-swaps |
| **Uninstall** | `DELETE /api/v1/plugins/{name}` | Removes an entry of `plugins/installed/manifest.yaml`. For a plugin of the bundled manifest the API answers 404 |
| **Reload** | `POST /api/v1/plugins/hot-swap` | Reloads the manifest |
| **Rollback** | `POST /api/v1/plugins/rollback` | Restores the rings that were live before the last successful hot-swap |
| **+ Install** | `POST /api/v1/plugins/install` | Opens a form: name, ring, entrypoint, timeout, fail policy, description |

## Limits

- **A reload is all or nothing.** If a plugin cannot be loaded the API answers `422` and nothing changes, neither the running plugins nor the manifest.
- **The install form cannot install a Python plugin.** It sends `type: python` without `allow_inprocess`, and the engine refuses Python plugins from the installed manifest unless that flag is set. The API answers `422` and the entry is not kept.
