# ADR-006: Four-Level Memory with Progressive Implementation

> **Status**: Accepted

## Context

Hecate's memory system design draws on cognitive science models and prior art (Letta, Mem0, Claude Code). The challenge was determining which memory levels to implement first and how to avoid over-engineering the initial release.

## Decision

Implement a **four-level memory architecture** (L1–L4) with progressive delivery by priority:

- **L1 Working Memory** — Named blocks in the context window (agent-editable)
- **L2 Conversation Memory** — Conversation history with auto-compression pipeline (snip → microcompact → autocompact)
- **L3 User Memory** — Cross-session persistent facts (Mem0-style extraction + pgvector + multi-signal fusion ranking)
- **L4 Knowledge Memory** — Equivalent to the RAG pipeline (document ingestion, embedding, retrieval)

## Rationale

Multi-turn conversations require at least L2 (conversation history with truncation). RAG is already in the feature list (equivalent to L4). L1 working memory blocks and L3 cross-session memory are valuable but not blocking for an end-to-end Agent application.

The BGE-M3 model was selected as the default embedding for its triple hybrid retrieval (dense + sparse + ColBERT), 100+ language coverage, and efficient deployment (FP16, ~1.5 GB VRAM).

## Consequences

- Each memory level is independently usable — L2 alone supports conversations, L4 alone supports RAG
- The compression pipeline prevents token overflow in long conversations
- L3 extraction runs asynchronously to avoid blocking the response path
