## ADDED Requirements

### Requirement: SkillRegistry resolves heterogeneous skill references
The system SHALL provide a `SkillRegistry` service that resolves `SkillRef` objects (containing `ref_type` and `ref_id`) into `ResolvedSkill` objects with uniform metadata, regardless of whether the underlying resource is a Tool, Skill, Knowledge Base, Workflow, or Agent.

#### Scenario: Resolve a tool reference
- **WHEN** SkillRegistry.resolve() receives a SkillRef with `ref_type: "tool"` and `ref_id: "weather_api"`
- **THEN** the system queries ToolModel by name and returns a ResolvedSkill with `name`, `description`, `parameters` (JSON Schema), and `source: "tool"`

#### Scenario: Resolve a knowledge base reference
- **WHEN** SkillRegistry.resolve() receives a SkillRef with `ref_type: "knowledge"` and a valid KB UUID
- **THEN** the system queries KnowledgeBaseModel by UUID and returns a ResolvedSkill with `name`, `description`, and `source: "knowledge"`

#### Scenario: Resolve a workflow reference
- **WHEN** SkillRegistry.resolve() receives a SkillRef with `ref_type: "workflow"` and a valid workflow UUID
- **THEN** the system queries WorkflowModel by UUID and returns a ResolvedSkill with `name`, `description`, and `source: "workflow"`

#### Scenario: Resolve an agent reference
- **WHEN** SkillRegistry.resolve() receives a SkillRef with `ref_type: "agent"` and a valid agent UUID
- **THEN** the system queries AgentModel by UUID and returns a ResolvedSkill with `name`, `description`, and `source: "agent"`

#### Scenario: Resolve unknown reference returns error
- **WHEN** SkillRegistry.resolve() receives a SkillRef that cannot be found
- **THEN** the system raises a `SkillNotFoundError` with the ref_type and ref_id

### Requirement: SkillRegistry invokes resolved skills uniformly
The system SHALL provide a `SkillRegistry.invoke()` method that executes any resolved skill via the appropriate execution path (tool_execute, agent_execute, workflow_execute, knowledge_query, or skill instruction injection).

#### Scenario: Invoke a tool skill
- **WHEN** SkillRegistry.invoke() is called with a tool-type SkillRef and arguments
- **THEN** the system delegates to EnginePort.tool_execute() with the tool name and arguments

#### Scenario: Invoke a knowledge skill
- **WHEN** SkillRegistry.invoke() is called with a knowledge-type SkillRef and a query
- **THEN** the system delegates to EnginePort.knowledge_query() with the KB ID and query

#### Scenario: Invoke a workflow skill
- **WHEN** SkillRegistry.invoke() is called with a workflow-type SkillRef and input
- **THEN** the system delegates to EnginePort.workflow_execute() with the workflow ID and input

#### Scenario: Invoke an agent skill
- **WHEN** SkillRegistry.invoke() is called with an agent-type SkillRef and a task message
- **THEN** the system delegates to EnginePort.agent_execute() with the agent ID and messages

### Requirement: SkillRegistry formats skills for LLM context injection
The system SHALL provide a `SkillRegistry.format_for_llm()` method that produces a uniform text representation of resolved skills suitable for LLM system prompt injection.

#### Scenario: Format tools for LLM
- **WHEN** format_for_llm() receives a list of ResolvedSkills including tools
- **THEN** the output contains tool names, descriptions, and parameter schemas in a standardized format (e.g., XML or JSON)

#### Scenario: Format knowledge bases for LLM
- **WHEN** format_for_llm() receives a list of ResolvedSkills including knowledge bases
- **THEN** the output describes each KB as a searchable knowledge source with retrieval instructions

### Requirement: AgentModel supports unified skill_ids field
The system SHALL add an optional `skill_ids` JSON field to AgentModel that stores a list of SkillRef objects, complementing (not replacing) the existing `tools`, `skills`, `knowledge_base_ids` fields.

#### Scenario: Agent with unified skill_ids
- **WHEN** an agent is created with `skill_ids: [{"ref_type": "tool", "ref_id": "search"}, {"ref_type": "knowledge", "ref_id": "<uuid>"}]`
- **THEN** the SkillRegistry SHALL resolve both references and the agent SHALL have access to both the tool and the knowledge base

#### Scenario: Backward compatibility with existing fields
- **WHEN** an agent has `tools: ["search"]` but no `skill_ids`
- **THEN** the SkillRegistry SHALL still resolve the tool via the legacy `tools` field, ensuring existing agents work without migration

### Requirement: SkillRegistry resolves A2A remote agents
The system SHALL support `ref_type: "remote_agent"` in SkillRef, resolving to A2A remote agents discovered via AgentCard, enabling agents to use remote A2A agents as skills.

#### Scenario: Resolve remote agent skill
- **WHEN** SkillRegistry.resolve() receives a SkillRef with `ref_type: "remote_agent"` and a URL
- **THEN** the system fetches the remote AgentCard and returns a ResolvedSkill with capabilities from the card

#### Scenario: Invoke remote agent skill
- **WHEN** SkillRegistry.invoke() is called with a remote_agent SkillRef
- **THEN** the system delegates to A2AClient.send_message() with the remote agent URL
