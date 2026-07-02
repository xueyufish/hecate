# ADR-002: Five-Layer System Architecture

> **Status**: Accepted

## Context

Hecate needed to determine system layering approach, balancing modularity and complexity. Common patterns include four layers (Gateway→Services→Engine→Data) or three layers (Presentation→Business→Data).

## Decision

Adopt a **five-layer architecture**: Gateway → Orchestration → Execution Engine → Capability Services → Infrastructure.

## Rationale

Decoupling the orchestration layer (what to do — graph compilation, workflow management, multi-agent patterns) from the execution engine layer (how to run — Pregel runtime, channels, checkpoints) enables independent evolution. The orchestration layer can add new patterns without touching the engine. The engine can swap execution strategies (in-process threads → cross-process workers → distributed backends) without affecting orchestration logic.

The capability services layer is separated from the engine to enforce dependency direction: services provide concrete implementations that the engine calls through abstract Port interfaces, keeping the engine free from external dependencies.

## Consequences

- Five layers add conceptual complexity compared to simpler architectures
- Each layer has a well-defined interface to the layer below
- The engine layer maintains zero external dependencies (except jsonschema)
- The conceptual 5-layer model is physically organized as 10 modules (see [architecture.md](../architecture.md) "Module Architecture"): Access Channel, Agent Studio, Agent Engine, Ops Center, Model Hub, Tool Platform, Knowledge & Memory, Enterprise Foundation, Security, and Ecosystem — each mapping to one or more conceptual layers
