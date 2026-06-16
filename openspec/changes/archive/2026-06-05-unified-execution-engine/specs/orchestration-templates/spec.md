## MODIFIED Requirements

### Requirement: Production Worker integration for orchestration templates
All graph templates in `templates.py` (build_three_layer_graph, build_fan_out_pipeline, build_conditional_pipeline, build_reflection_loop) SHALL be executable by PregelRuntime with production Workers instead of _TestWorker.

#### Scenario: Three-layer graph with production Workers
- **WHEN** `build_three_layer_graph()` output is compiled and executed via PregelRuntime with production Workers
- **THEN** the guard node SHALL invoke LLM with safety checking, the planner node SHALL invoke LLM with planning, and tool_call nodes SHALL execute real tools via EnginePort

#### Scenario: Fan-out pipeline with production Workers
- **WHEN** `build_fan_out_pipeline()` output is compiled and executed via PregelRuntime with production Workers
- **THEN** researcher, analyst, and summarizer nodes SHALL invoke real sub-agents via EnginePort.agent_execute()
