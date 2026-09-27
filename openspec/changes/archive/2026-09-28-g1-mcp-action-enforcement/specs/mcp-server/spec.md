# Spec Delta: mcp-server

## MODIFIED Requirements

### Requirement: Agent CRUD tools
The system SHALL expose the following agent management MCP tools:
- `agent_list(workspace_id: str | None)`: List agents with pagination
- `agent_create(name, persona, model_config, mode, tools, knowledge_base_ids)`: Create a new agent
- `agent_update(agent_id: str, fields: dict[str, Any])`: Update agent fields via a single fields mapping (fastmcp 4 does not support `**kwargs` tool parameters)
- `agent_delete(agent_id: str)`: Soft-delete an agent

`agent_create`、`agent_update` 与 `agent_delete` 属于变更类动作,SHALL 要求调用者持有显式 workspace 的 editor 及以上角色;viewer 角色与受限身份(无 workspace 成员资格)SHALL 被拒绝且不产生任何写入。拒绝结果 SHALL 与 REST 入口对同一操作的授权结果一致(见 `action-authorization` 能力)。

#### Scenario: Create agent via MCP tool
- **WHEN** an editor-role client calls `agent_create(name="Test Agent", model_config={"model": "gpt-4o"}, mode="chat")`
- **THEN** the server creates an `AgentModel` in the caller's workspace and returns the agent's UUID and metadata

#### Scenario: List agents
- **WHEN** a client calls `agent_list()`
- **THEN** the server returns a list of all non-deleted agents visible to the caller's workspace with id, name, mode, and model_config

#### Scenario: Update agent fields
- **WHEN** an editor-role client calls `agent_update(agent_id="<uuid>", fields={"persona": "You are helpful", "model_config": {"model": "gpt-4o"}})`
- **THEN** the server updates the listed fields on the matching `AgentModel` and returns the updated metadata

#### Scenario: Viewer cannot create or modify agents
- **WHEN** a viewer-role client calls `agent_create`、`agent_update` 或 `agent_delete`
- **THEN** the server rejects the call with an explicit authorization error before any database write, and the agent catalog is unchanged

#### Scenario: Restricted identity cannot write agents
- **WHEN** an authenticated identity without a workspace membership (role is None) calls `agent_create`
- **THEN** the server rejects the call with an explicit authorization error, and no agent is created in any workspace including the shared zero workspace

### Requirement: Knowledge base tools
The system SHALL expose the following knowledge base MCP tools:
- `knowledge_list()`: List all knowledge bases
- `knowledge_search(kb_id, query, limit, mode)`: Search a knowledge base using dense/sparse/hybrid search
- `knowledge_create(name, description, embedding_model, chunk_strategy)`: Create a new knowledge base
- `knowledge_ingest(kb_id, content, metadata)`: Ingest text content into a knowledge base

`knowledge_create` 与 `knowledge_ingest` 属于变更类动作,SHALL 要求 editor 及以上角色;拒绝行为与 Agent CRUD tools 一致。

#### Scenario: Search knowledge base
- **WHEN** a client calls `knowledge_search(kb_id="<uuid>", query="machine learning", limit=5, mode="hybrid")`
- **THEN** the server invokes `KnowledgeBaseService.search()` and returns a list of matching chunks with content, score, and metadata

#### Scenario: Ingest text into knowledge base
- **WHEN** an editor-role client calls `knowledge_ingest(kb_id="<uuid>", content="Some document text...")`
- **THEN** the server invokes `KnowledgeBaseService.ingest_document_text()` and returns the ingestion result

#### Scenario: Viewer cannot create knowledge bases
- **WHEN** a viewer-role client calls `knowledge_create`
- **THEN** the server rejects the call with an explicit authorization error before any database write

### Requirement: Tool execution tools
The system SHALL expose the following tool MCP tools:
- `tool_list(source: str | None)`: List registered tools, optionally filtered by source
- `tool_execute(tool_name, arguments)`: Execute a registered tool by name
- `tool_create(name, description, parameters, source)`: Register a new tool

`tool_execute` SHALL 在服务端从已验证的调用者身份派生执行上下文(租户与文件资源根),不接受调用者提供的工作区根或身份;文件类工具的读写 SHALL 限定在该调用者 workspace 的资源根内,不同 workspace 的文件互不可见,解析后指向根外的路径(包括符号链接) SHALL 在读写前拒绝。用于授权检查的工具身份、来源与可信元数据 SHALL 与最终执行的工具定义/记录一致;同名记录造成歧义、来源不一致或风险元数据缺失时 SHALL 在副作用前拒绝。`approval_required=true`、`risk_level=HIGH`、缺少所需 agent/session 上下文或无法建立服务端授权绑定的工具 SHALL 显式拒绝。不得因为工具名称可解析或来自 builtin/custom/MCP/REST 而默认授权;只有具备明确资源范围及可信授权元数据的工具可在本入口执行。受限身份与无 workspace 身份 SHALL 在执行前被拒绝。`tool_create` 属于变更类动作,SHALL 要求 editor 及以上角色,且新工具 SHALL 落在调用者的显式 workspace,不得回退写入共享 zero workspace。

#### Scenario: Execute a builtin tool
- **WHEN** an editor-role client calls `tool_execute` for a uniquely resolved tool whose trusted definition permits this entry and whose required context is available
- **THEN** the server executes that same resolved definition with server-derived context and returns the tool's result

#### Scenario: File operations are isolated per workspace
- **WHEN** clients from two different workspaces call `tool_execute(tool_name="write_file", ...)` followed by `tool_execute(tool_name="read_file", ...)` with the same relative path
- **THEN** each workspace's `read_file` observes only its own write; neither workspace can read the other's file

#### Scenario: Unapproved high-risk tool rejected before side effects
- **WHEN** a client calls `tool_execute` for a tool marked `approval_required=true` 或 `risk_level=HIGH`
- **THEN** the server rejects the call with an explicit error stating the tool is not executable on this entry, and no side effect occurs

#### Scenario: Ambiguous tool identity rejected before execution
- **WHEN** two accessible tool records share the requested name or the definition checked for authorization differs from the definition selected for execution
- **THEN** the server rejects the call before any tool handler or external adapter is invoked

#### Scenario: Tool without an authorizable execution context is rejected
- **WHEN** a tool requires agent, session, approval, resource, or provenance context that this entry cannot establish and verify
- **THEN** the server returns an explicit error before any side effect

#### Scenario: Workspace file root cannot be escaped through a symlink
- **WHEN** a file tool follows a path whose resolved target is outside the caller's workspace root
- **THEN** the server rejects the operation before reading or writing the target

#### Scenario: List tools by source
- **WHEN** a client calls `tool_list(source="builtin")`
- **THEN** the server returns only builtin tools with name, description, and parameters

#### Scenario: Viewer cannot register tools
- **WHEN** a viewer-role client calls `tool_create`
- **THEN** the server rejects the call with an explicit authorization error before any database write

## ADDED Requirements

### Requirement: Fail-closed execution without transport identity
当传输层未提供已验证身份时(包括显式配置 `MCP_AUTH_TYPE=none` 的开发逃生门),MCP 工具 SHALL 失败关闭:变更类与执行类工具一律不可用并返回明确错误,SHALL NOT 回退到任何全局或匿名权限。默认配置 SHALL 强制传输认证。

#### Scenario: No-auth mode cannot execute tools
- **WHEN** the deployment sets `MCP_AUTH_TYPE=none` and a client calls `tool_execute` 或任一变更类工具
- **THEN** the call fails with an explicit error and no tool execution or database mutation occurs

#### Scenario: Default configuration enforces transport auth
- **WHEN** the deployment uses the default `MCP_AUTH_TYPE` and a request carries no valid credential
- **THEN** the transport rejects the request with 401 before any tool is invoked
