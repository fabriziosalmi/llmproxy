# AI Dependency Guard

**Plugin:** `AI Dependency Guard` (`installed.ai_dependency_guard:AiDependencyGuard`) · **Hook:** `post_flight` · **Default:** disabled

Looks at the packages a model's response tells the user to install, and flags the
ones that the package registry says do not exist or that are on a list you
supply. Derived from
[ai-dependency-guard](https://github.com/fabriziosalmi/ai-dependency-guard), which
checks dependency manifests in CI; this plugin checks model output instead.

## The problem

A model can recommend a package that does not exist. Someone who registers that
name on PyPI or npm can then deliver code to whoever follows the recommendation.
This is called slopsquatting.

## What is checked

Only non-streaming responses, and only the text of the first choice
(`choices[0].message.content`). A response served from the cache is skipped.
Streamed responses are not checked.

Package names are taken from two forms only:

- **install commands:** `pip install X`, `pip3 install X`, `python -m pip install X`,
  `uv add X`, `uv pip install X`, `poetry add X`, `pipenv install X`, `pdm add X`,
  `npm install X`, `npm i X`, `npm add X`, `yarn add X`, `pnpm add X`
- **pinned requirement lines:** a name at the start of a line followed by `==`,
  `>=`, `<=`, `~=` or `!=` and a version, such as `name==1.2.3`

`conda` and `mamba` commands are not read. Flags and the arguments they take are
dropped (`-r requirements.txt`, `-e ./local`, `--index-url …`), as are URLs,
`git+…` references and paths containing a `/`. A bare archive file name such as
`foo-1.0-py3-none-any.whl` is not recognised as a file: it is looked up as a
package name and, not being one, flagged. Names are lower-cased and stripped of extras
and version specifiers; PyPI names are normalised as in
[PEP 503](https://peps.python.org/pep-0503/) for the lookup. npm scoped packages
(`@scope/name`) are kept. At most `max_packages_per_response` names are checked.

## Two checks

| Tier | Check | Flags |
|------|-------|-------|
| **1** | The name is in the configured `blocklist`. No network request. | Names you list, including ones that exist on the registry |
| **2** | One `GET` per remaining name to `https://pypi.org/pypi/<name>/json` or `https://registry.npmjs.org/<name>` | Names the registry answers `404` or `410` for |

Tier 2 fails open. A `200` means the package exists. Any other status (`429`,
`5xx`), a timeout or a connection error is inconclusive: the name is not flagged
and the result is not cached. Each request has its own time limit of
`registry_timeout_ms` (1500 ms by default). `200`, `404` and `410` results are
cached in the process for `cache_ttl_s`, for up to 4,096 names.

A package that exists is not flagged by tier 2, whoever published it. Tier 2 does
not detect a malicious package registered under a name the model made up; it
detects the name while it is still unregistered.

The plugin's own time limit is `timeout_ms: 2500` and its `fail_policy` is `open`:
if it fails or runs out of time the response is returned unchecked.

## Action

By default the plugin does not change or refuse the response. When it flags
something it writes a warning to the process log and sets two keys on the request
context:

```
ctx.metadata["_depguard_flagged"]  = True
ctx.metadata["_depguard_packages"] = [
    {"name": "totally-not-real-pkg", "ecosystem": "pypi", "reason": "not_found"},
    {"name": "evil-pkg",             "ecosystem": "pypi", "reason": "blocklist"},
]
```

Nothing shipped with the proxy reads those keys; they are there for a plugin of
your own that runs after this one. The caller sees no difference.

With `block_on_hallucination: true` a flagged response is refused: the caller
gets `403` with a message naming the packages, and the refusal is a row in the
audit chain. The provider has already been called and billed at that point.

## Configuration

The plugin is an entry in `plugins/manifest.yaml` with `enabled: false`:

```yaml
- name: "AI Dependency Guard"
  enabled: true                      # shipped as false
  config:
    registry_check: true             # tier 2 on or off
    block_on_hallucination: false    # true: refuse a flagged response with 403
    max_packages_per_response: 20    # limit on names checked per response
    registry_timeout_ms: 1500        # time limit of each registry request
    cache_ttl_s: 3600                # how long registry answers are cached
    ecosystems: ["pypi", "npm"]
    blocklist: []                    # names to flag without a lookup (tier 1)
```

Enable it by editing the manifest, or with
`POST /api/v1/plugins/toggle` and `{"name": "AI Dependency Guard", "enabled": true}`
(see [Plugins API](/api/plugins#toggle-plugin)).

The blocklist is empty as shipped. With tier 2 on, the proxy makes outbound
requests to `pypi.org` and `registry.npmjs.org` carrying the package names found
in responses.

## Tests

`tests/test_ai_dependency_guard.py`: extraction (install commands, flags, URLs,
scoped npm names, pinned requirement lines, prose that must not match), the
blocklist, the three registry outcomes, the cache limit, blocking with `403`, and
the cases where the plugin does nothing. The tests use a seeded cache and a fake
HTTP session in place of the registries.
