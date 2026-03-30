# Contributing to LLMProxy

Thanks for your interest in contributing! This guide will help you get started.

## Quick Start

```bash
git clone https://github.com/fabriziosalmi/llmproxy.git
cd llmproxy
make setup          # Creates venv, installs deps, copies .env.example
# Edit .env with at least one provider key
make test           # Run the test suite
```

## Development Workflow

1. **Fork** the repository and create a feature branch from `main`
2. **Write code** following the conventions below
3. **Add tests** for any new functionality
4. **Run checks**: `make test && make lint && make syntax`
5. **Submit a PR** with a clear description of what and why

## Code Conventions

### Python
- **Version**: Python 3.12+
- **Linter**: `ruff` (config in `pyproject.toml`)
- **Type hints**: Required on all public function signatures
- **Logging**: Use `logging.getLogger(__name__)`, never `print()`
- **Async**: All I/O operations must be async. Protect shared state with `asyncio.Lock()`
- **Exceptions**: Never use bare `except:` or `except Exception: pass` in new code. Log or re-raise.

### Architecture
- **5-Ring Plugin System**: Ingress → Pre-Flight → Routing → Post-Flight → Background
- **Adapters**: One per LLM provider in `proxy/adapters/`. Must implement `BaseModelAdapter`.
- **Plugins**: Marketplace plugins in `plugins/marketplace/`. Must extend `BasePlugin`.
- **Config**: All secrets via environment variables, never hardcoded.

### Commit Messages
- Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- Keep the first line under 72 characters
- Reference issues: `fix: resolve race condition in budget guard (#123)`

## Testing

```bash
make test           # Core test suite (fast, ~5s)
make test-all       # All tests including optional deps
make bench          # Performance benchmarks
```

- Tests live in `tests/` and mirror the source structure
- Use `pytest` with `@pytest.mark.asyncio` for async tests
- Aim for: unit tests on business logic, integration tests on pipelines
- Security tests: add adversarial inputs to `tests/test_security.py`

## Plugin Development

See [Plugin SDK docs](docs/plugins/sdk.md) for the full guide. Quick version:

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook

class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT

    async def execute(self, ctx):
        # Your logic here
        return PluginResponse.passthrough()
```

## Pull Request Checklist

- [ ] Tests pass (`make test`)
- [ ] Linter passes (`make lint`)
- [ ] New public functions have type hints and docstrings
- [ ] No hardcoded secrets or API keys
- [ ] No bare `except:` blocks
- [ ] CHANGELOG.md updated (for user-facing changes)
- [ ] Docs updated if API surface changed

## Reporting Bugs

Open a GitHub issue with:
- LLMProxy version (`cat VERSION`)
- Python version (`python --version`)
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (redact any API keys)

## Security Vulnerabilities

See [SECURITY.md](SECURITY.md) — please do NOT open public issues for security bugs.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
