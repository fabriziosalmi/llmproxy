# Plugins Panel

The Plugins view shows the ring-based plugin pipeline with management controls.

![Plugins Panel](/screenshots/soc-plugins.png)

## Ring Pipeline View

Plugins are displayed grouped by their ring assignment:

1. **Ingress** — Auth, Zero-Trust
2. **Pre-Flight** — Budget, Loop Breaker, PII, Cache
3. **Routing** — Model Selection, A/B Router
4. **Post-Flight** — Sanitization, Quality Gate, SLA Guard
5. **Background** — Telemetry, Token Counter

Each plugin card shows:
- Name, version, author
- Ring assignment and priority
- Enabled/disabled state
- Invocation count, error count, average latency
- Configuration form (auto-generated from `ui_schema`)

## Actions

- **Toggle**: Enable/disable individual plugins
- **Hot-Swap**: Reload all plugins with zero downtime
- **Rollback**: Revert to previous plugin state
- **Install/Uninstall**: Add or remove marketplace plugins

## Configuration Forms

Marketplace plugins with `ui_schema` get auto-generated configuration forms in the SOC UI. Supported field types:

- Text inputs
- Number inputs with min/max constraints
- Boolean toggles
- Dropdown selects
- Textarea for multi-line content
- Array/tag inputs

Changes are applied via hot-swap.
