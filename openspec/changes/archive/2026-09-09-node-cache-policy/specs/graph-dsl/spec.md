## ADDED Requirements

### Requirement: Node cache policy block
The Graph DSL document SHALL accept an optional per-node `cache` config object carrying `ttl`（必填，正整数秒）and `key_func`（可选，具名函数名）。`parse_graph()` SHALL propagate the block into the parsed node configuration, and `CompiledGraph.to_json()` SHALL roundtrip it so persisted graph definitions keep their cache policies. Nodes without a `cache` block SHALL behave exactly as before（永不查询缓存）。Caching SHALL be opt-in per node with no node-type restriction: any node type MAY declare a `cache` block and the author owns the purity judgment（命中会跳过该节点的全部执行，含副作用）。

#### Scenario: Parse and compile with cache block
- **WHEN** a node declares `"cache": {"ttl": 300}` and another declares `"cache": {"ttl": 600, "key_func": "kb_query_key"}`
- **THEN** the parser SHALL accept both and the compiled graph SHALL carry the corresponding cache policies

#### Scenario: to_json roundtrips cache block
- **WHEN** a compiled graph carrying node `cache` blocks is serialized via `to_json()` and re-parsed
- **THEN** every cache policy SHALL be preserved unchanged

#### Scenario: Absent cache block means no caching
- **WHEN** a node config omits `cache`
- **THEN** parsing and compilation SHALL succeed and the node SHALL never consult the cache at runtime

#### Scenario: Invalid ttl rejected
- **WHEN** a node declares `"cache": {}`（缺 ttl）or `"cache": {"ttl": 0}` or `"cache": {"ttl": -5}` or a non-integer ttl
- **THEN** the DSL SHALL reject the document with `GraphValidationError` indicating the cache path

## MODIFIED Requirements

### Requirement: Compiler validates entry point, edges, and handoff cycles
The `GraphCompiler.compile()` SHALL perform validation stages before producing a `CompiledGraph`: entry point, edges, handoff cycles, fan-out/merge structural constraints, execution-mode-aware node restrictions, channel access validation, routing config validation, declarative interrupt list validation（列表引用的 node ID 必须存在），and node cache policy validation（`key_func` 引用的函数必须已注册）。When `execution_mode="task"` is passed to compile(), the compiler SHALL reject graphs containing SUGGESTION node types or non-empty `interrupt_before`/`interrupt_after` lists by raising `GraphValidationError`.

#### Scenario: Entry point not found
- **WHEN** the declared entry point references a non-existent node
- **THEN** it SHALL raise `GraphValidationError` with field="entry"

#### Scenario: Edge target references non-existent node
- **WHEN** an edge target is neither a declared node ID nor a sentinel (`__start__`, `__end__`)
- **THEN** it SHALL raise `GraphValidationError` with field indicating the edge path

#### Scenario: Unreachable nodes logged as warning
- **WHEN** nodes exist that are not reachable from the entry point via BFS
- **THEN** the compiler SHALL log a WARNING with the unreachable node IDs but SHALL NOT raise an error

#### Scenario: Handoff between non-agent nodes
- **WHEN** a handoff edge source or target is not an AGENT-type node
- **THEN** it SHALL raise `GraphValidationError`

#### Scenario: Fan-out without merge
- **WHEN** a graph contains a FAN_OUT node but no MERGE node is reachable from any of its branches
- **THEN** it SHALL raise `GraphValidationError` with message "FAN_OUT node '{id}' has no reachable MERGE node"

#### Scenario: Merge without fan-out
- **WHEN** a graph contains a MERGE node but no FAN_OUT node is upstream
- **THEN** it SHALL raise `GraphValidationError` with message "MERGE node '{id}' has no upstream FAN_OUT node"

#### Scenario: Fan-out branches must match merge
- **WHEN** a FAN_OUT node has 3 branches but the downstream MERGE node's config lists a different fan_out_source
- **THEN** it SHALL raise `GraphValidationError`

#### Scenario: Interrupt list references non-existent node
- **WHEN** `interrupt_before: ["ghost"]` is declared and "ghost" is not a declared node ID
- **THEN** the compiler SHALL raise `GraphValidationError` with field indicating the interrupt list path

#### Scenario: Task mode with interrupt lists
- **WHEN** `execution_mode="task"` is passed to `compile()` and the graph declares a non-empty `interrupt_before` or `interrupt_after` list
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating declarative interrupts are forbidden in task mode

#### Scenario: Task mode with SUGGESTION node
- **WHEN** `execution_mode="task"` is passed to `compile()` and the graph contains a SUGGESTION node
- **THEN** the compiler SHALL raise `GraphValidationError` with message indicating SUGGESTION nodes are forbidden in task mode

#### Scenario: Conversational mode allows all node types
- **WHEN** `execution_mode="conversational"` is passed to `compile()` and the graph contains SUGGESTION nodes and non-empty interrupt lists
- **THEN** the compiler SHALL compile successfully without raising mode-related errors

#### Scenario: No execution mode defaults to conversational
- **WHEN** `execution_mode` is not provided to `compile()`
- **THEN** the compiler SHALL default to `"conversational"` behavior and allow all node types

#### Scenario: Routing config validation for intent mode
- **WHEN** a CONDITION node has `routing_mode: "intent"` but no `routing_config.intent_patterns`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating intent routing requires intent_patterns

#### Scenario: Routing config validation for dynamic mode
- **WHEN** a CONDITION node has `routing_mode: "dynamic"` but no `routing_config.candidate_agents`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating dynamic routing requires candidate_agents

#### Scenario: Dynamic routing candidates must reference existing nodes
- **WHEN** a CONDITION node has `routing_mode: "dynamic"` and `candidate_agents: ["agent_a", "nonexistent"]`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating candidate "nonexistent" is not a declared node

#### Scenario: Invalid routing mode rejected
- **WHEN** a CONDITION node has `routing_mode: "unknown"`
- **THEN** `parse_graph()` SHALL raise `GraphValidationError`

#### Scenario: Channel access warnings logged
- **WHEN** a node declares `channels.readable: ["nonexistent"]` and "nonexistent" is not in graph `state`
- **THEN** the compiler SHALL log a WARNING about undeclared channel access

#### Scenario: Cache key_func references unregistered function
- **WHEN** a node declares `"cache": {"ttl": 300, "key_func": "ghost_key"}` and no key function is registered under `ghost_key`
- **THEN** the compiler SHALL raise `GraphValidationError` indicating the unknown key function name（与未知 reducer 名同款 fail loud）

#### Scenario: Cache declared on any node type is accepted
- **WHEN** a conversation node, a knowledge-retrieval node, and a tool-call node each declare a valid `cache` block
- **THEN** the compiler SHALL accept all three without type-based rejection（纯度判断归作者，见 cache 语义需求）
