# Skills

A **skill** in Hecate is a named, addressable capability that an agent can invoke. Skills are the **unit of composition** — they're what makes Hecate's agents extensible without code changes. The `SkillRegistry` is the unified abstraction that resolves and invokes skills of different types.

This document explains the **conceptual model**: what skills are, how they differ from tools and plugins, and how to choose the right kind. For implementation details, see [Extension Architecture](../design/extension-architecture.md). For operational commands, see [CLI Reference](../reference/cli.md).

---

## What is a skill

A skill is a **named capability** that lives in a registry. When an agent runs, it references skills by name; the registry resolves them to the actual implementation.

```
agent "research-assistant" wants to call "web_search"
  ↓
SkillRegistry.resolve("web_search")
  ↓
  Found: builtin tool (ToolRegistry)
  ↓
Returns: web_search implementation ready to invoke
```

Skills are **named, versioned, and discoverable**. They have manifest metadata, ownership, and permissions, just like plugins.

---

## Six kinds of skills

The `SkillRegistry` (in `src/hecate/skill_registry/`) unifies **six skill types** under one abstraction:

| Skill kind | Identifier | Source | When to use |
|---|---|---|---|
| **Tool** | `tool` | Built-in tools shipped with Hecate (`web_search`, `read_file`, etc.) | "Agent needs a callable function" |
| **Skill** | `skill` | Skill registry — reusable prompts or procedural knowledge | "Package expertise as a reusable unit" |
| **Knowledge** | `knowledge` | Knowledge base (RAG) | "Agent needs to query documents" |
| **Workflow** | `workflow` | Multi-step workflow graph | "Agent needs to invoke a complex multi-step process" |
| **Agent** | `agent` | Another Hecate agent | "Agent delegates to another agent" |
| **Remote Agent** | `remote_agent` | A2A remote agent | "Agent delegates to a non-Hecate agent" |

The `SkillRefType` enum encodes these:

```python
class SkillRefType(StrEnum):
    TOOL = "tool"
    SKILL = "skill"
    KNOWLEDGE = "knowledge"
    WORKFLOW = "workflow"
    AGENT = "agent"
    REMOTE_AGENT = "remote_agent"
```

A single agent can reference skills of any combination.

---

## Skill vs Tool vs Plugin vs MCP

These four concepts are easy to confuse. Here's the distinction:

| Concept | Scope | Lifetime | Mutable at runtime? |
|---|---|---|---|
| **Tool** | A callable function | Process lifetime | No (compiled) |
| **Skill** | A packaged capability (often a prompt + tool chain) | Versioned, persistent | Maybe (hot reload) |
| **Plugin** | Engine extension (loaded into the engine) | Process lifetime | Yes (hot reload) |
| **MCP server** | External process | External lifetime | No (restart MCP) |

**Mental model**:
- **Tool** = a function (`def web_search(query: str) -> list[str]`)
- **Skill** = a packaged capability (`{name: "competitor-analysis", prompt: "You are an expert analyst...", tools: ["web_search"]}`)
- **Plugin** = engine-level code (can run hooks, affect every agent)
- **MCP server** = external service (your tool runs in a separate process)

When to use which:

| Question | Use |
|---|---|
| "I need to call a function" | **Tool** |
| "I need packaged expertise + tool composition" | **Skill** |
| "I need to inject logic into every agent's lifecycle" | **Plugin** |
| "I need to call an external service" | **MCP** |

---

## Skill lifecycle

Skills go through three phases:

```
                     create
   ┌────────────────────────────────────────┐
   │                                        ▼
┌──────────┐  activate   ┌──────────┐  invoke   ┌────────────┐
│  Draft   │ ───────────▶│  Active  │ ────────▶│  In-flight │
└──────────┘             └──────────┘           └────────────┘
                                  │                  │
                                  │ deactivate       ▼
                                  ▼           ┌────────────┐
                            ┌──────────┐       │  Completed │
                            │ Disabled │       └────────────┘
                            └──────────┘
```

| State | What it means |
|---|---|
| **Draft** | Skill created but not yet available to agents |
| **Active** | Skill is in the registry; agents can reference it |
| **In-flight** | Agent currently invoking the skill |
| **Completed** | (terminal) Invocations of this skill have finished |
| **Disabled** | Skill removed from registry; existing invocations finish, new ones rejected |

Skill **versioning** allows multiple versions to coexist (e.g., `web-search@1.0.0` and `web-search@2.0.0`). Agents can be pinned to specific versions.

---

## Skill resolution

When an agent references a skill by name, the `SkillRegistry` resolves it:

```python
# From src/hecate/skill_registry/registry.py
class SkillRegistry:
    def resolve(self, name: str, version: str | None = None) -> ResolvedSkill:
        """Resolve a skill reference to the actual implementation.
        
        Lookup order:
        1. Skills in the workspace (DB)
        2. Built-in tools (ToolRegistry)
        3. Knowledge bases (KBRegistry)
        4. Workflows (WorkflowRegistry)
        5. Agents (AgentRegistry)
        6. Remote agents (A2A client)
        """
```

Resolution is **scoped to the workspace** — a skill in workspace A is not visible to workspace B. This is the same isolation model as everything else ([Multi-Tenancy Architecture](../design/multi-tenancy-architecture.md)).

If the skill isn't found, the registry raises `SkillNotFoundError` with a clear message:

```
SkillNotFoundError: Skill 'web_search' not found in workspace 'engineering'. 
  Searched: skills (3), tools (5), knowledge bases (2), workflows (1), agents (4).
```

---

## Skill manifest

Each skill has a **manifest** describing its capabilities:

```python
skill_manifest = {
    "name": "competitor-analysis",
    "version": "1.0.0",
    "description": "Analyzes a competitor's positioning from public info",
    "type": "skill",
    "inputs": {
        "company_name": {"type": "string", "required": True},
        "depth": {"type": "string", "enum": ["quick", "deep"], "default": "quick"},
    },
    "outputs": {
        "summary": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "tools": ["web_search", "fetch_url"],
    "prompt": "You are an expert competitive analyst...",
    "permissions": ["network:https://*"],
    "tags": ["analysis", "competitive-intelligence"],
}
```

The manifest is **stored in Postgres** (so it survives restarts) and registered with the `SkillRegistry` on enable.

---

## Skill authoring

### CLI workflow

```bash
# Create a skill from a prompt file
hecate skill create \
  --name "competitor-analysis" \
  --prompt-file ./prompts/competitor.md \
  --tools web_search,fetch_url \
  --tags "analysis,competitive"

# List all skills
hecate skill list

# Get details
hecate skill get competitor-analysis

# Update
hecate skill update competitor-analysis --prompt-file ./prompts/v2.md

# Disable
hecate skill disable competitor-analysis

# Re-enable
hecate skill enable competitor-analysis
```

### Programmatic workflow

```python
from hecate.skill_registry import SkillRegistry, SkillRef, SkillRefType

registry = SkillRegistry(db=db_session)
await registry.register(
    workspace_id=workspace.id,
    manifest=skill_manifest,
)
```

### From a community skill hub

```bash
# (post-1.0 — community skill hub)
hecate skill hub install competitor-analysis --from community
```

---

## Skill vs MCP — when to use which

| Question | Use |
|---|---|
| "I need a function my agent can call" | **Could be either** — see below |
| "The function should run in Hecate's process" | **Skill (built-in tool)** |
| "The function should run in a separate process" | **MCP server** |
| "The function takes 50+ lines of glue code" | **Skill (with multi-tool composition)** |
| "The function uses an external system that you don't own" | **MCP server** |
| "The function requires complex dependencies (e.g., numpy)" | **MCP server** (avoid bloating Hecate deps) |
| "You need to share the function across multiple Hecate instances" | **MCP server** (single source of truth) |

---

## Skill versioning

Skills are versioned with **semver**:

```bash
# Pin to specific version
hecate skill get competitor-analysis@1.0.0

# Latest stable
hecate skill get competitor-analysis@latest

# Pinned in agent config
hecate agent update <agent-id> \
  --skills "competitor-analysis@1.2.0,web_search@latest"
```

Versioning policy:
- **Major** (1.0.0 → 2.0.0): breaking change; agents must opt-in
- **Minor** (1.0.0 → 1.1.0): new feature; backward-compatible
- **Patch** (1.0.0 → 1.0.1): bug fix; always backward-compatible

When an agent references a skill without a version, it gets the **latest stable** version.

---

## Skill observability

Every skill invocation is:

- **Traced** — span with skill name, version, duration, input/output hash
- **Audited** — actor (agent or user), workspace, timestamp, IP
- **Metered** — for billed skills: token usage, cost per invocation
- **Cacheable** — results can be cached by (skill, version, input_hash) for a configurable TTL

```bash
# Get skill invocation stats
hecate skill stats competitor-analysis --last-30d
# → invocations: 1,234
# → p50 latency: 2.1s
# → p99 latency: 8.4s
# → cache hit rate: 23%
# → total cost: $12.34
```

---

## Skill authoring best practices

| Practice | Why |
|---|---|
| **Use semantic versioning** | Communicate breaking changes |
| **Document inputs/outputs as JSON Schema** | Enable validation |
| **Keep prompts focused** | One skill = one capability |
| **Use deterministic tool ordering** | Easier to debug |
| **Add examples in the prompt** | Improves LLM accuracy |
| **Test with eval harness** | Catch regressions early |
| **Set conservative permissions** | Justify what's needed |
| **Persist idempotent results** | Cache for repeat calls |

---

## What's NOT in skills

| Feature | Status |
|---|---|
| **Community skill hub** | [post-1.0] |
| **Skill versioning UI** | CLI only; UI in P3 |
| **Skill marketplace with monetization** | P5 |
| **Skill auto-discovery from npm/PyPI** | Not planned |

---

## Related documents

- [Extension SPI & Plugin Architecture](../design/extension-architecture.md) — when to use a plugin instead
- [Tools, MCP, and A2A](tools-and-mcp.md) — how skills interact with MCP and A2A
- [Plugins](plugins.md) — the plugin concept
- [CLI Reference](../reference/cli.md) — `hecate skill *` commands
- [Guardrails and Hooks](guardrails.md) — how skill invocations are validated
-  — community skill hub timeline