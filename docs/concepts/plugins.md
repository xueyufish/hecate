# Plugins

Hecate's plugin system is how third-party code extends the platform without forking it. A plugin is a self-contained Python package that registers a manifest with Hecate and adds **one capability** — a new evaluation metric, a new auth method, a new notification channel, or custom logic at runtime interception points.

This document explains the **conceptual model**: what plugins are, when to write one, and how to choose the right type. For implementation details, see [Extension SPI & Plugin Architecture](../design/extension-architecture.md). For the API reference, see [Extension Points](../reference/extension-points.md).

---

## What is a plugin

A plugin is a Python package with three parts:

```
my-plugin/
├── src/
│   └── my_plugin.py        # Implementation of an ABC
├── MANIFEST.hcate-plugin   # Plugin manifest (metadata + entry)
└── README.md               # Human-readable description
```

When Hecate starts (or the plugin is hot-loaded), it:

1. Reads `MANIFEST.hcate-plugin` for metadata
2. Validates against `PluginManifest` schema
3. Loads the entry point (Python class or MCP/A2A URL)
4. Registers with the `PluginRegistry`
5. Makes the capability available to the relevant subsystem

---

## Six plugin types

Hecate supports six plugin types, each with a specific purpose. Choosing the right one is the most important decision:

| Type | ABC | Purpose | When to choose |
|---|---|---|---|
| **Tool** | `ToolPluginABC` | Add a callable tool that agents can invoke | "I want my agent to do X" |
| **Evaluator** | `EvaluatorABC` | Add a custom evaluation metric | "I want to measure Y" |
| **Channel** | `ChannelABC` | Add an external communication channel (Slack, Teams, etc.) | "I want messages from X" |
| **Auth Provider** | `AuthProviderABC` | Add a new authentication method | "I want users to log in via X" |
| **Notifier** | `NotifierABC` | Add a new notification destination | "I want events delivered to X" |
| **Extension** | `ExtensionPluginABC` | Inject custom logic into the execution flow | "I want to transform Y at runtime" |

**Rule of thumb**: pick the type that matches the **subsystem** you want to extend, not the language or framework you want to use. If you don't see your use case, the answer is usually **Extension** (most flexible).

---

## Tool vs MCP: when to choose a plugin

The most common confusion is between **Tool plugins** and **external MCP servers**. Both add capabilities an agent can invoke.

| Aspect | Tool plugin | MCP server |
|---|---|---|
| **Distribution** | Installed via `hecate plugin install` | Connected via MCP endpoint URL |
| **Code location** | Runs in Hecate process | Runs in separate process (often remote) |
| **Update cadence** | New Hecate release required | Independent of Hecate |
| **Trust boundary** | Must trust plugin author | Must trust MCP server operator |
| **Latency** | In-process (fast) | Network round-trip (slower) |
| **Use when** | Logic must be in Hecate (e.g., guardrail hooks) | Service already exists as MCP server |

**Examples**:

| Use case | Recommendation |
|---|---|
| "My agent needs to query our internal customer DB" | Tool plugin (if DB access should be in Hecate process) or MCP server (if it should be isolated) |
| "Add a Slack notifier" | Plugin if you own the Slack workspace; Notifier plugin |
| "Connect to GitHub for PR review" | MCP server (GitHub already has an official MCP server) |
| "Custom PII redaction beyond Presidio" | Extension plugin (auto-wired into PreLLM hook) |
| "Quality metric for our domain glossary" | Evaluator plugin |

---

## Plugin lifecycle

Plugins go through five states. The transitions are managed by `PluginRegistry`:

```
                            install
   ┌──────────────────────────────────────────────────┐
   │                                                  ▼
┌──────┐    enable     ┌─────────┐    invoke    ┌──────────┐
│ New  │ ─────────────▶│ Loaded  │ ───────────▶│  Active  │
│      │ ◀─────────────│         │ ◀───────────│          │
└──────┘    disable    └─────────┘    complete  └──────────┘
   ▲                                                  │
   │                  uninstall                       │
   └──────────────────────────────────────────────────┘
```

| State | What it means |
|---|---|
| **New** | Plugin package installed on disk, not loaded |
| **Loaded** | Module imported, manifest validated, instance created |
| **Active** | Registered with the relevant subsystem, can be invoked |
| **Disabled** | Manifest preserved, instance kept, no new invocations |
| **Uninstalled** | Package removed, instance destroyed |

Lifecycle hooks (optional): `on_load`, `on_enable`, `on_disable`, `on_uninstall`. Implement only what you need.

---

## PluginManifest: the contract

Every plugin declares its metadata in a `PluginManifest`:

```python
manifest = PluginManifest(
    type="evaluator",                       # One of: tool, evaluator, channel, auth, notifier, extension
    name="domain_specific_score",           # Unique within type
    version="1.0.0",                        # Semantic version
    api_version="1.0",                     # Required: SPI version you target
    min_platform_version="0.2.0",          # Required: minimum Hecate version
    description="Domain-specific scorer",   # Human-readable
    entry="python:my_plugin:DomainScorer", # How to load
    permissions=("db:read:glossary",),     # Permissions you need
    config_schema={...},                    # JSON Schema for config
)
```

The manifest is **immutable** (frozen). Plugins cannot grant themselves permissions after registration.

---

## Permissions: declared vs granted

Each plugin declares required permissions in the manifest. Hecate enforces them at the boundary:

| Permission | What it allows |
|---|---|
| `network:https://*` | Outbound HTTPS to any host |
| `network:https://api.example.com/*` | Outbound HTTPS to specific host (more restrictive) |
| `db:read:agents` | Read access to the `agents` table |
| `db:write:audit_logs` | Write access to the `audit_logs` table |
| `mcp:invoke` | Call MCP servers |
| `a2a:invoke` | Call A2A agents |

When a plugin tries to do something outside its declared permissions, the request is **denied at the boundary** (network egress filter, DB permission check, MCP/A2A guard). The violation is logged to the audit trail.

**Best practice**: declare the **minimum** permissions needed. A tool plugin that only reads from one table should declare `db:read:agents`, not `db:*`.

---

## Hot reload

Plugin development benefits from fast iteration. Hecate supports hot reload:

```bash
# Edit a plugin file
vim my_plugin.py

# Reload without restarting Hecate
hecate plugin reload my_plugin
```

Hot reload preserves:
- Other plugins' state
- In-flight agent sessions (if the plugin isn't currently invoked by them)
- The registry's other entries

Hot reload does **not** preserve:
- In-memory state of the plugin itself
- Sessions that were calling the plugin at reload time (they'll fail with a transient error)

---

## When to write a plugin

Write a plugin when you need to extend Hecate with code that's **specific to your deployment** but **reusable across the codebase**. Don't write a plugin when:

- **You only need config changes** — use `.env` or the Management API
- **You only need a one-off agent** — use the existing agent model
- **You need to modify Hecate's core engine** — fork and submit a PR instead
- **The capability is better as an MCP server** — external services that change independently should be MCP

---

## Plugin vs extension: the escape hatch

If your use case doesn't fit any of the six plugin types, you probably want an **Extension plugin**. Extensions are special — they're auto-wired into all four guardrail hook points:

```
PreLLMHook → on_pre_llm() → modify messages → continue
PostLLMHook → on_post_llm() → modify response → continue
PreToolHook → on_pre_tool() → validate args → continue / block
PostToolHook → on_post_tool() → validate result → continue / block
```

An extension can return `GuardrailResult(action=BLOCK)` to short-circuit execution. Use this for:

- Custom PII detection (beyond Presidio)
- Custom output validation (e.g., "no competitor mentions")
- Custom audit logging
- Custom cost tracking

If even extension doesn't fit, file an issue — we may be missing the right escape hatch.

---

## Plugin development workflow

A typical plugin development cycle:

```
1. Identify the right plugin type (table above)
2. Read the corresponding ABC and example
3. Write the implementation
4. Test locally with `hecate plugin load ./my-plugin`
5. Validate permissions by trying to do too much
6. Package as MANIFEST.hcate-plugin
7. Install in your environment: `hecate plugin install ./my-plugin`
8. Configure permissions via the Management API
9. Monitor via audit logs
10. Distribute (if open source) to plugin hub
```

---

## Examples

### Custom Evaluator (metrics)

See [Extension Architecture > Writing a custom SPI plugin](../design/extension-architecture.md#writing-a-custom-spi-plugin) for a complete code example.

### Custom Auth Provider

Add support for a new authentication method (e.g., SAML assertion format your IdP uses):

```python
class CustomSAMLProvider(AuthProviderABC):
    @property
    def scheme(self) -> str: return "custom_saml"
    
    @property
    def description(self) -> str: return "Custom SAML assertion format"
    
    async def authenticate(self, token, db):
        # Custom parsing logic
        ...
```

### Custom Guardrail (Extension plugin)

Inject custom validation at all four hook points:

```python
class CompetitorBlocker(ExtensionPluginABC):
    def on_post_llm(self, response, config):
        if "competitor-name" in response["content"]:
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                reason="competitor mention detected",
            )
        return None  # Continue
```

---

## Distribution

Plugins can be:

- **Private** — installed on a single Hecate instance, never shared
- **Team-shared** — distributed via a private package registry
- **Public** — published to the (future) Hecate Plugin Hub

For the current state of plugin distribution, see [post-1.0].

---

## Related documents

- [Extension SPI & Plugin Architecture](../design/extension-architecture.md) — implementation details, ABC signatures, full examples
- [Extension Points Reference](../reference/extension-points.md) — API reference for all 11 core + 4 SPI extension points
- [How-to: Develop Custom Extensions](../how-to/develop-extensions.md) — step-by-step practical recipe
- [Tools, MCP, and A2A](tools-and-mcp.md) — when to use plugins vs MCP servers vs A2A agents
-  — plugin marketplace and distribution are post-1.0