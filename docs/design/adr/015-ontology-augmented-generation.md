# ADR-015: Ontology-Augmented Generation (OAG)

> **Status**: Proposed
> **Date**: 2026-06-30

## Context

Hecate's current RAG pipeline retrieves relevant text chunks from vector stores and injects them into LLM context. This works well for simple Q&A but fails for complex reasoning tasks that require:
- Understanding entity relationships
- Multi-hop reasoning across connected concepts
- Executing business logic on retrieved data
- Writing results back to source systems

Palantir introduced "Ontology-Augmented Generation" (OAG) as an evolution of RAG that grounds LLM reasoning in a structured knowledge model with executable actions.

## Decision

Implement **OAG** as an evolution of the RAG pipeline that combines:
1. **Retrieval** (existing RAG) — find relevant knowledge
2. **Logic** (ontology functions) — apply business rules and reasoning
3. **Actions** (ontology actions) — execute decisions and write back

## Architecture

```
User Query
    │
    ▼
┌─────────────────────────────────────────┐
│         Query Understanding             │
│  • Intent classification                │
│  • Entity extraction                    │
│  • Context gathering                    │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│ RAG    │ │ Logic  │ │ Action │
│ Layer  │ │ Layer  │ │ Layer  │
│        │ │        │ │        │
│ vector │ │ rules  │ │ create │
│ search │ │ ml     │ │ update │
│ graph  │ │ llm    │ │ delete │
│ search │ │ funcs  │ │ invoke │
└───┬────┘ └───┬────┘ └───┬────┘
    │          │          │
    └──────────┼──────────┘
               ▼
┌─────────────────────────────────────────┐
│         Reasoning Engine                │
│  • Combine retrieved context            │
│  • Apply business logic                 │
│  • Determine required actions           │
│  • Generate response                    │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│         Action Execution                │
│  • Execute approved actions             │
│  • Write back to source systems         │
│  • Record decision lineage              │
└─────────────────────────────────────────┘
```

## OAG vs RAG

| Aspect | RAG | OAG |
|--------|-----|-----|
| Data source | Vector store | Ontology (objects + relations + logic) |
| Retrieval | Text chunks | Structured knowledge with context |
| Reasoning | LLM-only | LLM + business logic |
| Actions | None | Execute and write back |
| Lineage | Source citations | Full decision lineage |

## Rationale

- **Enterprise-grade reasoning**: Business logic alongside LLM reasoning
- **Closed-loop execution**: Not just retrieval but also action
- **Auditability**: Decision lineage for compliance
- **Extends existing RAG**: Builds on current pipeline, doesn't replace it

## Consequences

- RAG pipeline needs ontology-aware retrieval
- Knowledge Graph needs function/action integration
- Engine needs OAG-specific EnginePort methods
- Security layer needs action-level permissions (6.30)
