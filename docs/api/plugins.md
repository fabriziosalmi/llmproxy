# API: Plugins

Plugin management endpoints. `GET` routes need permission `registry:read`; every
other route needs `plugins:manage`, which the `admin` and `operator` roles hold.

Plugins are declared in two manifests: `plugins/manifest.yaml`, shipped with the
proxy, and `plugins/installed/manifest.yaml`, written by the install route. An
entry in the second replaces an entry of the same name in the first. A plugin is
identified by its manifest `name`, for example `"Smart Budget Guard"`.

These routes change manifest entries and reload plugins. They do not upload code:
a plugin's file must already be present under `plugins/`.

## List Plugins

```
GET /api/v1/plugins
```

Returns the plugins the engine has loaded:

```json
{
  "plugins": [
    {
      "name": "Smart Budget Guard",
      "hook": "pre_flight",
      "type": "python",
      "version": "1.0.0",
      "author": "llmproxy",
      "description": "Pre-flight budget enforcement with cost estimation",
      "ui_schema": [{"key": "session_budget_usd", "type": "number"}],
      "enabled": true,
      "timeout_ms": 5,
      "fail_policy": "closed"
    }
  ]
}
```

(`ui_schema` is shortened here.) A plugin whose manifest entry has
`enabled: false` is not loaded and is not in the list. If no plugin is loaded at all, the entries of `plugins/manifest.yaml` are
returned as written. Per-plugin counters are at `GET /api/v1/plugins/stats` (see
[Admin API](/api/admin#dashboards-and-analytics)).

## Install Plugin

```
POST /api/v1/plugins/install
```

Adds an entry to `plugins/installed/manifest.yaml` and reloads the plugins.
`name`, `hook` and `entrypoint` are required (`400` if one is missing). The other
manifest fields (`type`, `priority`, `enabled`, `fail_policy`, `timeout_ms`,
`config`, `version`, ...) are optional and stored as given.

```json
{
  "name": "My Plugin",
  "hook": "pre_flight",
  "entrypoint": "installed.my_plugin:MyPlugin",
  "allow_inprocess": true
}
```

`hook` is one of `ingress`, `pre_flight`, `routing`, `post_flight`, `background`.
For a Python plugin (`type: python`, the default) `entrypoint` is
`<module path under plugins/>:<class or async function>`; the example loads
`plugins/installed/my_plugin.py`.

A Python plugin runs inside the proxy's process with the proxy's privileges. An
entry in the installed manifest is refused unless it sets
`"allow_inprocess": true`. The source is checked for imports outside an allow-list
and for calls such as `exec` and `eval`; that check catches mistakes and is not a
sandbox. The SHA-256 of the source is recorded in the manifest at install, and a
file that no longer matches it is refused at later loads.

Returns `{"status": "installed", "name": "My Plugin"}`, or `422` with the reason
when the plugin cannot be loaded (its file is missing or cannot be imported, the
hook is not one of the five names, the loader refuses it). On a `422` the entry is
not kept in `plugins/installed/manifest.yaml` and the running plugins are unchanged.

## Uninstall Plugin

```
DELETE /api/v1/plugins/{name}
```

Removes the entry of that name from `plugins/installed/manifest.yaml` and reloads
the plugins. Returns `{"status": "uninstalled", "name": "..."}`. `404` when the
installed manifest has no such entry; a plugin declared only in
`plugins/manifest.yaml` cannot be uninstalled, only disabled. The plugin's file is
not deleted.

## Toggle Plugin

```
POST /api/v1/plugins/toggle
```

Sets `enabled` on the entry of that name in `plugins/manifest.yaml` and reloads
the plugins.

```json
{
  "name": "Smart Budget Guard",
  "enabled": true
}
```

Returns `{"name": "Smart Budget Guard", "enabled": true}`.

- `name` must match the manifest `name` exactly; `404` when no entry has that
  name. `enabled` must be `true` or `false` (`400` otherwise).
- Only `plugins/manifest.yaml` is edited. An entry in the installed manifest is
  not affected by this route.
- The file is rewritten from its parsed form, so comments in it are lost.
- If the reload that follows fails, the route answers `422` with the reason and
  the manifest is put back as it was.

## Hot-Swap

```
POST /api/v1/plugins/hot-swap
```

Reloads all plugins from the two manifests without restarting:

1. The new set of plugins is built separately; requests keep using the current
   set. `on_load()` runs on each new class plugin.
2. If any enabled plugin fails to load, the reload is refused: `422` with the
   reason, and the current set stays as it is.
3. Otherwise the current set is replaced by the new one in a single step,
   `on_unload()` runs on the replaced plugins, and the previous set is kept as the
   target of the next rollback.

Returns `{"status": "success", "message": "Plugin set reloaded"}`.

## Rollback

```
POST /api/v1/plugins/rollback
```

Puts back the rings as they were before the last successful hot-swap, toggle,
install or uninstall. Always returns `{"status": "rolled_back"}`, also when there
is nothing to roll back to.

It restores the rings only. The manifests are not changed, so the next reload or
restart loads what they say, and `GET /api/v1/plugins` keeps listing the set from
after the swap. The restored plugins have already had `on_unload()` called.
