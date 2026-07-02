## Why

Hecate's multi-agent orchestration supports 6 collaboration patterns (2.7a) and a rich canvas UI, but agents currently have no enforced channel access boundaries — any agent can read or write any channel. Additionally, routing between agents is limited to static condition expressions and handoff edges. Enterprise platforms (Google ADK, AutoGen, Huawei AgentArts) provide intent-based and dynamic LLM-driven routing as first-class features. Without channel access control and advanced routing, multi-agent graphs cannot express real-world isolation boundaries or intelligent routing decisions.

## What Changes

- **Channel access validation**: Compiler enforces that each node's `readable`/`writable` channel config is consistent with declared graph channels. Runtime warns on unauthorized channel access.
- **Broadcast mode UX**: ChannelSelector enhanced to show broadcast participation (TOPIC channel opt-in) and channel access summary per agent.
- **Intent-based routing**: New `routing_mode: "intent"` on condition nodes — LLM classifies user intent and routes to the matching target via configurable `intent_patterns`.
- **Dynamic routing**: New `routing_mode: "dynamic"` on condition nodes — LLM selects the next speaker from a candidate agent list at runtime, inspired by AutoGen's SelectorGroupChat.
- **Dynamic handoff edge**: New edge trigger `"dynamic_handoff"` where the LLM decides the handoff target at runtime (inspired by Google ADK's transfer_to_agent).

## Capabilities

### New Capabilities
- `channel-access-control`: Compile-time validation and runtime enforcement of per-node channel read/write access boundaries
- `advanced-routing-modes`: Intent-based and dynamic LLM-driven routing modes extending the condition node

### Modified Capabilities
- `graph-dsl`: Add `routing_mode` and `routing_config` fields to CONDITION node config; add `"dynamic_handoff"` edge trigger
- `multi-agent-canvas`: Enhanced ChannelSelector with broadcast mode UX; routing mode configuration panel for condition nodes
- `agent-handoff`: Support dynamic handoff edges where LLM selects target at runtime

## Impact

- **Engine**: `compiler.py` gains channel access validation pass and routing mode compilation; `pregel.py` gains runtime channel access warning and dynamic routing evaluation via `EnginePort.llm_invoke()`
- **Graph DSL schema**: `schemas/graph-dsl.schema.json` — add `routing_mode`, `routing_config`, `intent_patterns`, `candidate_agents` to CONDITION node config; add `"dynamic_handoff"` to edge trigger enum
- **API**: Graph validation endpoints gain channel access checks and routing config validation
- **Frontend**: `channel-selector.tsx` enhanced with broadcast mode and access summary; new routing config panel in condition node config; `edge-type-selector.tsx` gains dynamic handoff option
- **Tests**: New test files for channel access validation, routing mode compilation, dynamic routing evaluation
