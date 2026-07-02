# negotiation-templates Specification

## Purpose
TBD - created by archiving change multi-agent-advanced-collaboration. Update Purpose after archive.
## Requirements
### Requirement: Negotiation graph template
The system SHALL provide a `build_negotiation_graph` factory function in `engine/templates.py` that returns a `GraphConfig` implementing a two-agent negotiation protocol: proposal → response → accept/reject, with configurable max rounds.

#### Scenario: Negotiation template structure
- **WHEN** `build_negotiation_graph(agent_a_model, agent_b_model, max_rounds=5)` is called
- **THEN** the graph SHALL contain: 2 AGENT nodes (proposer, responder), 1 CONDITION node (check_agreement), edges forming a negotiation loop, and a `negotiation_channel` LAST_VALUE channel for inter-agent proposals

#### Scenario: Negotiation round trip
- **WHEN** the negotiation graph executes and the proposer sends a proposal
- **THEN** the responder SHALL receive the proposal via the shared negotiation channel and respond with accept or counter-proposal

#### Scenario: Negotiation terminates on agreement
- **WHEN** the responder accepts a proposal (writes `agreement_status="accepted"` to the channel)
- **THEN** the CONDITION node SHALL route to `__end__` without further rounds

#### Scenario: Negotiation terminates on max rounds
- **WHEN** the negotiation reaches `max_rounds` without agreement
- **THEN** the graph SHALL terminate with `agreement_status="max_rounds_reached"`

### Requirement: Debate graph template
The system SHALL provide a `build_debate_graph` factory function returning a `GraphConfig` implementing a multi-round debate between 2 agents with an optional judge.

#### Scenario: Debate template structure
- **WHEN** `build_debate_graph(debater_a_model, debater_b_model, judge_model, rounds=3)` is called
- **THEN** the graph SHALL contain: 2 AGENT nodes (debater_a, debater_b), 1 optional AGENT node (judge), a round counter LAST_VALUE channel, edges for alternating debate turns, and judge evaluation at the end

#### Scenario: Debate round execution
- **WHEN** the debate graph executes round 1
- **THEN** debater_a writes an argument, debater_b reads it and writes a rebuttal, then the round counter increments

#### Scenario: Debate with judge
- **WHEN** all debate rounds complete and a judge is configured
- **THEN** the judge node SHALL read all arguments and produce a final verdict

#### Scenario: Debate without judge
- **WHEN** all debate rounds complete and no judge is configured
- **THEN** the graph SHALL terminate with all arguments accumulated in the messages channel

### Requirement: Templates follow existing conventions
Both negotiation and debate template functions SHALL follow the established pattern: accept model/prompt parameters, return `GraphConfig`, use `NodeType.AGENT` for agent nodes, and define channels in the `state` dict.

#### Scenario: Import template factory
- **WHEN** `from hecate.engine.templates import build_negotiation_graph, build_debate_graph` is executed
- **THEN** the import SHALL succeed

#### Scenario: Returned GraphConfig is compilable
- **WHEN** `build_negotiation_graph(...)` produces a GraphConfig
- **THEN** `GraphCompiler.compile(graph_config)` SHALL succeed without errors

