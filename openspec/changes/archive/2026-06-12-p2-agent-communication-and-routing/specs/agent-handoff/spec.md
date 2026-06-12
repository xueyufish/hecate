## ADDED Requirements

### Requirement: Dynamic handoff edge type
The system SHALL support a `"dynamic_handoff"` edge trigger in the Graph DSL. When a handoff edge has `trigger: "dynamic_handoff"`, the `handoff_to_agent` tool SHALL be injected with multiple candidate targets. The LLM decides which target to hand off to at runtime.

#### Scenario: Dynamic handoff tool injected with multiple targets
- **WHEN** a graph DSL contains an edge with `trigger: "dynamic_handoff"` from agent node "triage" with target dict `{"billing": "billing_agent", "tech": "tech_agent"}`
- **THEN** the system injects `handoff_to_agent` tool into the "triage" agent's tool list with `target` parameter accepting values `["billing_agent", "tech_agent"]`

#### Scenario: Dynamic handoff LLM selects target
- **WHEN** the LLM calls `handoff_to_agent(target="tech_agent")` on a dynamic handoff edge
- **THEN** the worker returns `Command(goto="tech_agent")` and the Pregel runtime executes the "tech_agent" node in the next superstep

#### Scenario: Dynamic handoff invalid target rejected
- **WHEN** the LLM calls `handoff_to_agent(target="unknown_agent")` and "unknown_agent" is not in the allowed target list
- **THEN** the worker SHALL return an error response to the LLM indicating the target is not valid, prompting a retry

#### Scenario: Dynamic handoff cycle detection
- **WHEN** the compiler processes a `dynamic_handoff` edge
- **THEN** it SHALL apply the same cycle detection logic as regular handoff edges

#### Scenario: Dynamic handoff edge parsed from DSL
- **WHEN** `parse_graph()` receives an edge with `{"source": "router", "target": {"billing": "billing_agent", "tech": "tech_agent"}, "trigger": "dynamic_handoff"}`
- **THEN** the resulting `Edge` SHALL have `trigger="dynamic_handoff"` and `target={"billing": "billing_agent", "tech": "tech_agent"}`
