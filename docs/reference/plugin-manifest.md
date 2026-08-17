# Plugin Manifest Schema Reference

The `PluginManifest` is the **contract** between a plugin and Hecate's `PluginRegistry`. Every plugin must declare its metadata in this format. This document is the canonical reference for plugin authors.

For the conceptual model, see [Plugins concept](../concepts/plugins.md). For implementation details, see [Extension SPI & Plugin Architecture](../design/extension-architecture.md). For the API reference, see [REST API](rest-api.md).

---

## The manifest schema

The `PluginManifest` is a Pydantic-friendly frozen dataclass (from `src/hecate/plugin/manifest.py`):

```python
@dataclass(frozen=True)
class PluginManifest:
    """Immutable metadata describing a plugin."""

    type: str                              # Required
    name: str                              # Required
    version: str                           # Required
    api_version: str = ""                  # Recommended
    min_platform_version: str = ""         # Recommended
    description: str = ""                  # Recommended
    entry: str = ""                        # Required
    permissions: tuple[str, ...] = ()      # Optional
    translations: tuple[str, ...] = ()     # Optional
    config_schema: dict[str, Any] | None = None  # Optional
```

It is **immutable** (frozen) — plugins cannot mutate their metadata after registration.

---

## Field reference

### `type` (required)

The plugin type. Must be one of:

| Value | Loads as | ABC |
|---|---|---|
| `tool` | Callable tool | `ToolPluginABC` |
| `evaluator` | Evaluation metric | `EvaluatorABC` |
| `channel` | External communication channel | `ChannelABC` |
| `auth_provider` | Authentication method | `AuthProviderABC` |
| `notifier` | Notification destination | `NotifierABC` |
| `extension` | Runtime interceptor | `ExtensionPluginABC` |
| `cli` | CLI subcommand | (Typer app) |
| `model` | LLM provider | `ModelPluginABC` |
| `trigger` | Event trigger | `TriggerPluginABC` |

Examples:

```yaml
type: tool
```

```yaml
type: evaluator
```

### `name` (required)

Unique name within the type. Must match `^[a-z][a-z0-9_-]{1,63}$`.

Examples: `my_metric`, `github-mcp`, `slack-notifier`.

Full identifier is `f"{type}:{name}"` — e.g., `tool:web_search`.

### `version` (required)

Semantic version: `MAJOR.MINOR.PATCH` (e.g., `1.2.3`).

Optional pre-release: `1.2.0-alpha.1`, `1.2.0-rc.1`.

### `api_version` (recommended, required from 0.2)

API version this plugin targets. Current value: `1.0`.

Set explicitly even if same as Hecate's API version — gives the registry a clear contract.

### `min_platform_version` (recommended)

Minimum Hecate version required. Examples: `0.1.5`, `0.2.0`.

The registry rejects the plugin if the running Hecate is older.

### `description` (recommended)

Human-readable description (1-2 sentences). Shown in:
- `hecate plugin list` output
- Canvas plugin manager UI
- API responses

Example: `"Domain-specific glossary citation scorer for legal contracts."`

### `entry` (required)

How to load the plugin. Format depends on the type:

| Format | Used for | Example |
|---|---|---|
| `python:module.path:ClassName` | In-process Python | `python:my_plugin:MyEvaluator` |
| `mcp://endpoint` | MCP server | `mcp://https://mcp.github.com/sse` |
| `a2a://endpoint` | A2A agent | `a2a://https://research.example.com/a2a/` |
| `script:/path/to/script` | External script | `script:/opt/plugins/my.sh` |

### `permissions` (optional)

Permissions the plugin requires. Declared upfront; enforced at runtime.

| Permission | Granted |
|---|---|
| `network:https://*` | Outbound HTTPS to any host |
| `network:https://api.example.com/*` | Outbound HTTPS to specific host |
| `network:http://*` | Outbound HTTP (insecure) |
| `db:read:<table>` | SELECT on table |
| `db:write:<table>` | INSERT/UPDATE/DELETE on table |
| `fs:read:<path>` | Read filesystem path |
| `fs:write:<path>` | Write filesystem path |
| `mcp:invoke` | Call any MCP server |
| `a2a:invoke` | Call any A2A agent |
| `tools:invoke` | Call any tool |

Default: empty tuple (no permissions).

Violations are logged and rejected at the boundary.

### `translations` (optional)

i18n message keys the plugin provides translations for. Loaded via the i18n system.

Example: `("plugin.error.network", "plugin.error.auth")`.

### `config_schema` (optional)

JSON Schema for the plugin's configuration. Validated at registration time.

```yaml
config_schema:
  type: object
  required: ["api_key"]
  properties:
    api_key:
      type: string
      description: "API key for the external service"
    timeout_seconds:
      type: integer
      default: 30
      minimum: 1
      maximum: 300
```

Users supply config values when installing the plugin.

---

## Complete examples

### Tool plugin

```yaml
type: tool
name: company-lookup
version: 1.0.0
api_version: "1.0"
min_platform_version: "0.2.0"
description: "Look up company information by domain name"
entry: "python:my_plugin.tools:CompanyLookup"
permissions:
  - "network:https://api.companies.com/*"
config_schema:
  type: object
  required: ["api_key"]
  properties:
    api_key:
      type: string
```

### Evaluator plugin

```yaml
type: evaluator
name: tone-consistency
version: 0.3.0
api_version: "1.0"
min_platform_version: "0.2.0"
description: "Evaluates whether agent responses maintain consistent tone"
entry: "python:my_evals.tone:ToneConsistencyEvaluator"
permissions:
  - "db:read:agents"
  - "db:read:conversations"
```

### Auth provider plugin

```yaml
type: auth_provider
name: custom-saml
version: 1.0.0
api_version: "1.0"
min_platform_version: "0.2.0"
description: "Custom SAML assertion format for our IdP"
entry: "python:my_auth.saml:CustomSAMLProvider"
config_schema:
  type: object
  required: ["idp_metadata_url"]
  properties:
    idp_metadata_url:
      type: string
      format: uri
```

### MCP server (plugin type)

```yaml
type: tool
name: github-mcp
version: 1.0.0
api_version: "1.0"
min_platform_version: "0.1.0"
description: "GitHub MCP server for PR reviews and repo operations"
entry: "mcp://https://mcp.github.com/sse"
permissions:
  - "network:https://api.github.com/*"
  - "network:https://mcp.github.com/*"
```

### A2A agent (plugin type)

```yaml
type: tool
name: external-research-agent
version: 1.0.0
api_version: "1.0"
min_platform_version: "0.1.0"
description: "External research agent at research.example.com"
entry: "a2a://https://research.example.com/a2a/"
permissions:
  - "network:https://research.example.com/*"
```

---

## Loading strategies

### Python in-process

```python
# my_plugin/__init__.py
from hecate.plugin import PluginManifest, PluginContext
from hecate.plugin.spi.evaluator import EvaluatorABC

class MyEvaluator(EvaluatorABC):
    @property
    def name(self): return "my_metric"
    @property
    def description(self): return "..."
    async def evaluate(self, input): ...

MANIFEST = PluginManifest(
    type="evaluator",
    name="my_metric",
    version="1.0.0",
    api_version="1.0",
    min_platform_version="0.2.0",
    description="...",
    entry="python:my_plugin:MyEvaluator",
)

def register(registry: PluginRegistry):
    registry.register(MANIFEST, MyEvaluator())
```

Package the above as a Python wheel with a `MANIFEST.hcate-plugin` file at the root:

```
my_plugin/
├── MANIFEST.hcate-plugin    # YAML form of the manifest
├── pyproject.toml
└── my_plugin/
    └── __init__.py
```

### MCP server

External MCP server. The `entry` URL is where Hecate connects.

```yaml
type: tool
name: github-mcp
entry: "mcp://https://mcp.github.com/sse"
```

Hecate fetches the AgentCard, validates the signature if applicable, and registers the server's tools.

### A2A agent

External A2A-compliant agent.

```yaml
type: tool
name: external-research
entry: "a2a://https://research.example.com/a2a/"
```

Hecate fetches the AgentCard at `/.well-known/agent-card.json` and registers the agent's skills as tools.

---

## Validation

The `PluginRegistry` validates manifests at load time:

| Check | Failure mode |
|---|---|
| `type` is in known list | Reject with `unknown plugin type` |
| `name` matches regex | Reject with `invalid name` |
| `version` is semver | Reject with `invalid version` |
| `min_platform_version` ≤ current Hecate | Reject with `unsupported version` |
| `entry` is loadable | Reject with `entry not found` |
| `config_schema` validates against submitted config | Reject with `config validation failed` |
| (For Python) Implementation class implements ABC | Reject with `class does not implement ABC` |

Rejected plugins are logged but **do not crash Hecate**. Other plugins continue to work.

---

## Versioning

### Plugin API version

Independent of Hecate's version. Examples:

| Plugin `api_version` | Supported by Hecate |
|---|---|
| `1.0` | Hecate 0.2+ |
| `1.1` | Hecate 0.3+ (when shipped) |
| `2.0` | (breaking change) |

The registry enforces compatibility at load time.

### Hecate version

The `min_platform_version` field declares the minimum Hecate version. The plugin's `version` field follows semver for the plugin itself.

When upgrading Hecate:

1. Check which plugins declare `min_platform_version` near the new version
2. Migrate plugins as needed (newer Hecate may require plugin updates)
3. Test in staging before production upgrade

---

## Distribution

### Package the plugin

```bash
# Python wheel
pip install build
python -m build

# Result: dist/my_plugin-1.0.0-py3-none-any.whl
```

### Install locally

```bash
hecate plugin install ./dist/my_plugin-1.0.0-py3-none-any.whl
```

### Publish to a registry

```bash
pip upload twine
twine upload dist/*
```

### (Future) Community plugin hub

```bash
# Future improvements
hecate plugin hub install company-lookup
```

---

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `unknown plugin type` | `type` not in known list | Use one of the 9 types above |
| `invalid name` | `name` doesn't match regex | Match `^[a-z][a-z0-9_-]{1,63}$` |
| `invalid version` | `version` not semver | Use `MAJOR.MINOR.PATCH` |
| `unsupported version` | Hecate too old | Upgrade Hecate or lower `min_platform_version` |
| `entry not found` | `entry` path doesn't exist | Fix path; check Python imports |
| `class does not implement ABC` | Wrong ABC | Make sure your class implements the ABC for the type |
| `config validation failed` | Submitted config doesn't match schema | Re-check config against schema |

---

## Related documents

- [Plugins concept](../concepts/plugins.md) — conceptual model
- [Extension SPI & Plugin Architecture](../design/extension-architecture.md) — implementation details
- [Extension Points reference](extension-points.md) — the ABC interface signatures
- [How-to: Develop custom extensions](../how-to/develop-extensions.md) — practical recipe
- [Skills concept](../concepts/skills.md) — comparison with skills
- [REST API reference](rest-api.md) — plugin management API