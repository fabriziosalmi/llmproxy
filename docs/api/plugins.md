# API: Plugins

Plugin lifecycle management endpoints.

## List Plugins

```
GET /api/v1/plugins
```

Returns all plugins with marketplace metadata (version, author, config, ui_schema, stats).

## Install Plugin

```
POST /api/v1/plugins/install
```

Install a plugin. The plugin file is **AST-scanned** for security before loading.

```json
{
  "name": "my_plugin"
}
```

## Uninstall Plugin

```
DELETE /api/v1/plugins/{name}
```

Remove a plugin from the pipeline.

## Toggle Plugin

```
POST /api/v1/plugins/toggle
```

Enable or disable a plugin without uninstalling it.

```json
{
  "name": "smart_budget_guard",
  "enabled": true
}
```

## Hot-Swap

```
POST /api/v1/plugins/hot-swap
```

Zero-downtime reload of all plugins using RCU (Read-Copy-Update):

1. Calls `on_unload()` on existing plugins
2. Snapshots current state (rollback target)
3. Loads new configuration
4. Calls `on_load()` on new plugins
5. Runs health check
6. Atomic swap
7. Auto-rollback on failure

## Rollback

```
POST /api/v1/plugins/rollback
```

Revert to the previous plugin state (before the last hot-swap).
