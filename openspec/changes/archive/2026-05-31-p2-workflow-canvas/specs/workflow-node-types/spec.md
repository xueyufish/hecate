## ADDED Requirements

### Requirement: LLM Call node type
The system SHALL provide a `conversation` node type with a configuration panel containing: model selector (dropdown from /v1/models), system prompt (textarea), temperature (slider 0-2), max_tokens (number input).

#### Scenario: Configure LLM node with model and prompt
- **WHEN** user clicks on an LLM Call node
- **THEN** a side panel opens showing model dropdown, system prompt textarea, temperature slider, and max_tokens input pre-filled with the node's current config

#### Scenario: Save LLM node configuration
- **WHEN** user modifies the model selector and system prompt in the side panel
- **THEN** the node's config is updated and the node label changes to show the selected model name

### Requirement: Condition node type
The system SHALL provide a `condition` node type with a configuration panel containing an expression field. Condition nodes SHALL have multiple output handles (one per branch).

#### Scenario: Configure condition with two branches
- **WHEN** user configures a condition node with expression and connects two edges labeled "true" and "false"
- **THEN** the graph DSL stores the condition config and the edge targets as a dict `{"true": "node-a", "false": "node-b"}`

### Requirement: Tool Call node type
The system SHALL provide a `tool-call` node type with a configuration panel containing a tool selector dropdown populated from GET /api/tools.

#### Scenario: Configure tool call node
- **WHEN** user selects a tool from the dropdown
- **THEN** the node config is updated with `tool_name` and the node label shows the selected tool name

### Requirement: Sub-Agent node type
The system SHALL provide an `agent` node type with a configuration panel containing an agent selector dropdown populated from GET /api/agents.

#### Scenario: Configure sub-agent node
- **WHEN** user selects an agent from the dropdown
- **THEN** the node config is updated with `agent_ref` set to the agent's ID

### Requirement: Knowledge Retrieval node type
The system SHALL provide a `knowledge-retrieval` node type with a configuration panel containing a knowledge base selector dropdown populated from GET /api/knowledge-bases and a query template textarea.

#### Scenario: Configure knowledge retrieval node
- **WHEN** user selects a knowledge base and enters a query template
- **THEN** the node config stores `knowledge_base_id` and `query_template` and the node label shows the knowledge base name

### Requirement: Variable Set node type
The system SHALL provide a `variable-set` node type with a configuration panel containing channel name input and value expression textarea for writing to graph state channels.

#### Scenario: Configure variable set node
- **WHEN** user enters a channel name and value expression
- **THEN** the node config stores the channel writable mapping and the node label shows the channel name
