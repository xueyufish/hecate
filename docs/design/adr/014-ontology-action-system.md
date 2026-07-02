# ADR-014: Ontology Action System for Decision Execution

> **Status**: Proposed
> **Date**: 2026-06-30

## Context

Hecate's current knowledge system is read-only: agents retrieve information via RAG but cannot write back to source systems or execute actions on ontology objects. This limits agents to passive information retrieval rather than active decision execution.

Platforms like Palantir AIP solve this with an "Action System" — a way to define operations that modify ontology objects and write back to source systems, with approval workflows and audit trails.

## Decision

Implement an **Ontology Action System** that enables agents to execute actions on knowledge objects:

1. **Action Definitions**: Schema-defined operations that modify objects/relationships
2. **Action Execution**: Agent invokes actions via Action Tool with approval workflow
3. **Write-Back**: Safe propagation of changes to source systems
4. **Decision Lineage**: Complete audit trail of who did what based on what data

## Architecture

```
┌─────────────────────────────────────────┐
│           Ontology Layer                │
│  ┌─────────────┐  ┌─────────────────┐  │
│  │ Objects      │  │ Actions         │  │
│  │ (nouns)      │  │ (verbs)         │  │
│  │ - entities   │  │ - create        │  │
│  │ - properties │  │ - update        │  │
│  │ - relations  │  │ - delete        │  │
│  └──────┬──────┘  │ - invoke        │  │
│         │         │ - writeback      │  │
│         │         └────────┬────────┘  │
│         │                  │           │
│         ▼                  ▼           │
│  ┌──────────────────────────────────┐  │
│  │     Ontology SDK (OSDK)         │  │
│  │  • Type-safe CRUD operations    │  │
│  │  • Action execution API         │  │
│  │  • Decision lineage tracking    │  │
│  └──────────────────────────────────┘  │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│         Agent Layer                     │
│  • Action Tool (execute actions)        │
│  • Manual/Auto execution modes          │
│  • Pre-execution approval               │
└─────────────────────────────────────────┘
```

## Key Design Decisions

### Action Types
- **Simple Actions**: Update a single property value
- **Compound Actions**: Modify multiple objects in one transaction
- **External Actions**: Write back to source systems (ERP, CRM, etc.)
- **LLM-Backed Actions**: Use LLM to determine action parameters

### Execution Modes
- **Manual**: Agent proposes action, human approves before execution
- **Automatic**: Agent executes action directly (for low-risk operations)
- **Conditional**: Action executes only if conditions are met

### Decision Lineage
Every action records:
- Who initiated (human or agent)
- When it was executed
- What data version was used
- What the outcome was
- Which approval workflow was followed

## Rationale

- **Closes the loop**: Agents can not only retrieve but also act on knowledge
- **Enterprise-grade**: Approval workflows and audit trails for compliance
- **Enterprise parity**: Key differentiator for enterprise agent platforms
- **Extends existing infrastructure**: Builds on Knowledge Graph (3.5.1) and EventStore

## Consequences

- Knowledge Graph needs action type definitions (3.5.1 extended)
- Engine needs Action Tool integration via EnginePort
- Security layer needs action-level permission control
- Audit system needs decision lineage tracking (6.21)
