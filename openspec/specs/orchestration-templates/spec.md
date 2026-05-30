## ADDED Requirements

### Requirement: Orchestration template listing API
The system SHALL expose a `GET /api/orchestration-templates` endpoint returning all available orchestration templates with name, description, and a preview of the graph structure.

#### Scenario: List templates
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the response contains a list of templates, each with `id`, `name`, `description`, `category`, and `preview` fields

#### Scenario: Template preview contains graph summary
- **WHEN** a template is returned in the list
- **THEN** the `preview` field contains the number of agent nodes and edges in the template

### Requirement: Orchestration template detail API
The system SHALL expose a `GET /api/orchestration-templates/{template_id}` endpoint returning the full Graph DSL JSON for the template.

#### Scenario: Get template detail
- **WHEN** `GET /api/orchestration-templates/customer-service-triage` is called
- **THEN** the response contains the complete Graph DSL JSON that can be loaded into the canvas editor

#### Scenario: Template not found
- **WHEN** `GET /api/orchestration-templates/nonexistent` is called
- **THEN** the response is 404 with error code `NOT_FOUND`

### Requirement: Customer Service Triage template
The system SHALL include a pre-built "Customer Service Triage" orchestration template with a router agent connected to billing, technical, and general specialist agents via handoff edges.

#### Scenario: Triage template structure
- **WHEN** the Customer Service Triage template is loaded
- **THEN** the graph contains 4 AGENT nodes (router, billing, technical, general) and 3 handoff edges from router to each specialist

#### Scenario: Triage template router has handoff tool
- **WHEN** the template is compiled and the router agent executes
- **THEN** the router agent's tool list includes `handoff_to_agent` tool targeting the 3 specialists

### Requirement: Content Pipeline template
The system SHALL include a pre-built "Content Pipeline" orchestration template with researcher, writer, and reviewer agents connected in a linear chain.

#### Scenario: Pipeline template structure
- **WHEN** the Content Pipeline template is loaded
- **THEN** the graph contains 3 AGENT nodes (researcher, writer, reviewer) connected by standard edges in a linear chain with a condition node checking review status

### Requirement: Hierarchical Supervisor template
The system SHALL include a pre-built "Hierarchical Supervisor" orchestration template with a supervisor agent that delegates to worker agents via tool-based invocation.

#### Scenario: Supervisor template structure
- **WHEN** the Hierarchical Supervisor template is loaded
- **THEN** the graph contains 1 supervisor AGENT node with `invocation_mode: "tool"` referencing N worker agents, and a condition loop for re-delegation
