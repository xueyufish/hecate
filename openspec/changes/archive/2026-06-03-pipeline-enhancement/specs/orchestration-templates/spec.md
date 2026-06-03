## ADDED Requirements

### Requirement: Fan-out Pipeline template
The system SHALL include a pre-built "Fan-out Pipeline" orchestration template demonstrating parallel processing with a researcher agent fanning out to multiple analyst agents and merging results.

#### Scenario: Fan-out template structure
- **WHEN** the Fan-out Pipeline template is loaded
- **THEN** the graph SHALL contain 1 researcher AGENT node, 1 FAN_OUT node, 3 analyst AGENT nodes (analyst_a, analyst_b, analyst_c), 1 MERGE node, and 1 summarizer AGENT node

#### Scenario: Fan-out template edges
- **WHEN** the template is compiled
- **THEN** edges SHALL be: researcher→fanout, fanout→[analyst_a, analyst_b, analyst_c], analyst_*→merge, merge→summarizer, summarizer→__end__

### Requirement: Conditional Pipeline template
The system SHALL include a pre-built "Conditional Pipeline" orchestration template demonstrating multi-key conditional routing based on classification.

#### Scenario: Conditional template structure
- **WHEN** the Conditional Pipeline template is loaded
- **THEN** the graph SHALL contain 1 classifier AGENT node, 1 CONDITION node, and 3 specialist AGENT nodes (finance_agent, tech_agent, legal_agent) with multi-key conditional edge routing

#### Scenario: Conditional template routing
- **WHEN** the classifier agent outputs a category
- **THEN** the CONDITION node SHALL route to the matching specialist based on the category value

### Requirement: Reflection Loop template
The system SHALL include a pre-built "Reflection Loop" orchestration template demonstrating iterative refinement with a quality check loop.

#### Scenario: Reflection template structure
- **WHEN** the Reflection Loop template is loaded
- **THEN** the graph SHALL contain 1 drafter AGENT node, 1 reviewer AGENT node, 1 CONDITION node, and 1 reviser AGENT node with a loop edge from reviser back to reviewer

#### Scenario: Reflection loop iteration
- **WHEN** the reviewer determines quality is insufficient
- **THEN** the CONDITION node SHALL route to the reviser, which then routes back to the reviewer for re-evaluation

#### Scenario: Reflection loop termination
- **WHEN** the reviewer determines quality is approved
- **THEN** the CONDITION node SHALL route to __end__
