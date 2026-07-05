## Why

The A2A protocol (Google's Agent-to-Agent) has become the de facto industry standard for inter-agent communication, with 150+ organizations running it in production (July 2026) and 8 platinum Linux Foundation members (AWS, Cisco, Google, IBM, Microsoft, Salesforce, SAP, ServiceNow) on the governing TSC. All competing protocols (IBM ACP, AGNTCY ACP) have converged into A2A. Hecate's current multi-agent capabilities (6 collaboration patterns, EventBus, TaskAllocator, P2P Negotiator) are process-local — there is no protocol layer for cross-platform agent discovery and delegation.

Meanwhile, Hecate's skill/tool/knowledge/workflow associations are fragmented: tools and skills use name strings, knowledge bases use UUIDs, workflows use a single UUID reference. No platform in the industry has unified these as a single "Skill" abstraction — this is Hecate's differentiation opportunity. Agent-Workflow mutual embedding is one-directional today (AGENT nodes exist in DAGs, but agents cannot invoke workflows as skills).

## What Changes

- **A2A Protocol (2.10)**: New `a2a/` module implementing A2A v1.2 — Hecate as both A2A server (AgentCard, JSON-RPC task lifecycle, SSE streaming, artifacts) and A2A client (agent discovery, task submission, push notification receiver). Uses official `a2a-sdk` Python package.
- **Signed Agent Cards (2.10a)**: JWS signatures with ES256 algorithm, RFC 8785 JSON Canonicalization, JWKS public key distribution at `/.well-known/jwks.json`, algorithm pinning to prevent downgrade attacks.
- **Unified Skill Registry (2.9)**: `SkillRegistry` service unifying Tools, Skills, Knowledge Bases, Workflows, and Agents as a single `SkillRef` abstraction with `resolve()`, `invoke()`, `format_for_llm()`. Zero data migration — reads from existing tables.
- **Agent-Workflow Mutual Embedding (2.9a)**: Agent → Workflow invocation as a tool (extends EnginePort with `workflow_execute()`); Workflow → Agent (AGENT node type already exists). Recursive nesting with `max_depth=3` (IBM anti-pattern guidance).
- **Collaborative Conflict Handling (2.8)**: Extends existing `ConflictResolver` with distributed lock coordination, task-level conflict detection, permission scope mismatch handling for A2A agents, integration with `P2PNegotiator`.

## Capabilities

### New Capabilities
- `a2a-protocol`: A2A v1.2 server (AgentCard, JSON-RPC, SSE, task lifecycle, artifacts, push notifications) and client (discovery, task submission) implementation
- `signed-agent-cards`: JWS signature generation and verification for Agent Cards with JWKS key distribution
- `unified-skill-registry`: SkillRegistry service abstracting Tools, Skills, Knowledge Bases, Workflows, and Agents as unified SkillRef entries
- `agent-workflow-embedding`: Bidirectional Agent ↔ Workflow invocation with recursive nesting (max_depth=3)

### Modified Capabilities
- `event-bus`: Add A2A-specific CollaborationEventType entries (A2A_TASK_DELEGATED, A2A_ARTIFACT_RECEIVED) for cross-protocol event correlation
- `agent-tool`: Extend AgentTool to support A2A remote agents as invocation targets (not just local agent_execute)

## Impact

- **New code**: `src/hecate/a2a/` module (server, client, signing), `src/hecate/skill_registry/` module, workflow embedding service
- **Modified code**: `engine/eventbus.py` (new event types), `engine/agent_tool.py` (A2A target support), `engine/temporal/conflict.py` (distributed conflicts), `services/orchestration/agent_execution_port.py` (workflow_execute), `models/agent.py` (unified skill_ids field), `api/` (A2A endpoints + skill registry API)
- **New dependencies**: `a2a-sdk` (official Python SDK), `cryptography` (already present via auth module)
- **Migrations**: Add `agent_card_keys` table for signing key pairs, `a2a_tasks` table for task lifecycle persistence
- **Config**: New `A2A_*` settings (server URL, signing key path, JWKS URL), `SKILL_REGISTRY_*` settings
