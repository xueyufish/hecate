## ADDED Requirements

### Requirement: Channel access summary in config panel
The agent node config panel SHALL display a channel access summary section showing which channels the selected agent can read from and write to. The summary SHALL group channels by type (LAST_VALUE, TOPIC, ACCUMULATOR) and highlight broadcast participation (TOPIC channels shared with other agents).

#### Scenario: Channel access summary displayed
- **WHEN** the user selects an agent node that has declared `channels.readable: ["messages", "context"]` and `channels.writable: ["messages"]`
- **THEN** the config panel shows a "Channel Access" section with "Readable: messages (topic), context (last_value)" and "Writable: messages (topic)"

#### Scenario: Broadcast participation highlighted
- **WHEN** the user selects an agent node that shares a TOPIC channel "shared_context" with 2 other agent nodes
- **THEN** the channel access summary SHALL highlight "shared_context" with a broadcast icon and show "Shared with 2 agents"

#### Scenario: No channel declaration shown as informational
- **WHEN** the user selects an agent node with no `channels` config
- **THEN** the config panel shows "No channel access configured" with a suggestion to configure channel access

### Requirement: Routing mode configuration for condition nodes
The condition node config panel SHALL provide a routing mode selector with 3 options: "Condition" (expression-based), "Intent" (pattern + LLM classification), and "Dynamic" (LLM selects next speaker). Each mode SHALL show mode-specific configuration fields.

#### Scenario: Condition mode selected (default)
- **WHEN** the user selects a condition node and the routing mode is "condition" (or unset)
- **THEN** the config panel shows the existing expression field with no additional routing fields

#### Scenario: Intent mode selected
- **WHEN** the user changes routing mode to "Intent"
- **THEN** the config panel shows an "Intent Patterns" section with add/remove pattern rows (each with pattern regex and target node selector), and an optional "Routing Prompt" text field for LLM fallback

#### Scenario: Dynamic mode selected
- **WHEN** the user changes routing mode to "Dynamic"
- **THEN** the config panel shows a "Candidate Agents" multi-select listing all agent nodes in the graph, a "Routing Prompt" text field, and an "Allow Repeated Speaker" toggle (default off)

#### Scenario: Routing mode persisted to graph DSL
- **WHEN** the user selects "Intent" mode and adds intent patterns
- **THEN** the graph DSL node config SHALL include `routing_mode: "intent"` and `routing_config: {intent_patterns: [...], routing_prompt: "..."}`

### Requirement: Dynamic handoff edge type in canvas
The edge type selector SHALL include "Dynamic Handoff" as a 5th option. Dynamic handoff edges SHALL be rendered as a dashed purple line with a sparkle icon, visually distinct from static handoff edges (dashed purple, no sparkle).

#### Scenario: User creates dynamic handoff connection
- **WHEN** the user connects agent node A to agent nodes B and C, and selects "Dynamic Handoff" in the edge type selector
- **THEN** the graph DSL stores the edge with `trigger: "dynamic_handoff"` and `target: {"b": "agent_b", "c": "agent_c"}`

#### Scenario: Dynamic handoff edge rendered distinctly
- **WHEN** a graph contains an edge with `trigger: "dynamic_handoff"`
- **THEN** the canvas renders the edge as a dashed purple Bezier curve with a sparkle icon, distinct from static handoff edges
