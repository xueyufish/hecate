# ADR-008: Security via Engine-Level Hooks and Plugin Extension Points

> **Status**: Accepted

## Context

Security is a cross-cutting concern spanning all layers (Gateway → Orchestration → Engine → Services). The feature list includes four-level risk authorization, approval scopes, security guardrails, audit logging, and sandbox isolation. The design needed to determine where to implement security controls without hardcoding them in the engine.

## Decision

Implement security through **engine-level guardrail hooks** (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook) and the **Plugin system's Decision and Observe extension points** — not hardcoded in the engine core.

## Rationale

Security requirements vary widely across deployments. A research lab may want minimal friction; a financial institution may require strict approval workflows. By providing hook interfaces rather than hardcoded policies, Hecate allows each deployment to configure its own security posture.

The four guardrail hooks provide interception at the boundaries of LLM and tool execution — the two highest-risk operations in any agent system. Each hook has a NoOp default; custom hooks are registered at the Worker level.

Tool and Agent entities carry `risk_level` (LOW/MEDIUM/HIGH/CRITICAL) and `approval_scope` (once/session/project/global) fields from the start, providing the data model for authorization decisions.

## Consequences

- Security policies are pluggable — deployments choose their strictness level
- The engine remains policy-agnostic — it provides hooks, not decisions
- PII masking, content filtering, and audit logging are implemented as hook consumers
