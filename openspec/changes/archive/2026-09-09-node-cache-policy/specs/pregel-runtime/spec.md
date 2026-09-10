## ADDED Requirements

### Requirement: Node-level cache dispatch seam
The Pregel runtime SHALL consult the node cache before dispatching an Invocation for any node carrying a cache policy, using a key derived from that node's input（见键构成需求）。On a cache hit, the runtime SHALL fabricate the worker result from the cached value and SHALL NOT execute the node's worker. On a cache miss, the node SHALL execute normally and its result SHALL be stored under the same key before the superstep commits. Nodes without a cache policy SHALL never consult the cache. Concurrent identical keys MAY each execute（v1 接受重复执行，不合并）。

#### Scenario: Hit skips worker execution
- **WHEN** a node with a cache policy is scheduled and a valid (non-expired) entry exists for its key
- **THEN** the runtime SHALL use the cached value as the node result and the worker SHALL NOT be invoked

#### Scenario: Miss executes and stores
- **WHEN** a node with a cache policy is scheduled and no valid entry exists
- **THEN** the node SHALL execute normally and the result SHALL be stored before the superstep's WAL commit

#### Scenario: Nodes without policy are never consulted
- **WHEN** a graph contains nodes without a `cache` block
- **THEN** the runtime SHALL not perform any cache lookup for those nodes, whatever the cache contents

#### Scenario: Expired entry re-executes
- **WHEN** a node's cached entry has exceeded its ttl
- **THEN** the node SHALL execute normally and refresh the entry

### Requirement: Cache key composition and scopes
The default cache key SHALL be composed of: the node id, node-type-relevant execution identity（CONVERSATION 节点 SHALL 掺入 model 配置哈希，使模型升级自动失效），and the canonical JSON of the node's readable-channel slice. A registered `key_func` SHALL override the default derivation. Keys SHALL be namespaced by scope: `session`（默认，键含 session id）or `tenant`（opt-in，键含 tenant id 且不含 session id，面向只读 KB 检索类节点）。A `global` scope SHALL NOT be available in v1.

#### Scenario: Identical input within a session hits
- **WHEN** a cache-enabled node is scheduled twice in one session with identical readable-channel values
- **THEN** both schedules SHALL derive the same key and the second SHALL hit

#### Scenario: Tenant scope serves multiple sessions
- **WHEN** a node declares tenant scope and two sessions of the same tenant schedule it with identical readable-channel values
- **THEN** the second session SHALL hit the entry stored by the first（键不含 session id）

#### Scenario: Session scope isolates sessions
- **WHEN** a node uses the default session scope and a different session presents identical readable-channel values
- **THEN** the lookup SHALL miss（键含 session id）

#### Scenario: Model config change invalidates conversation node keys
- **WHEN** a CONVERSATION node's model configuration changes between runs with identical readable-channel values
- **THEN** the derived keys SHALL differ and the lookup SHALL miss

#### Scenario: Registered key_func overrides default
- **WHEN** a node declares `key_func` naming a registered function
- **THEN** the key SHALL be derived by that function from the node input, replacing the default derivation

### Requirement: Cache-hit log identity
A cache hit and a cache miss SHALL produce identical event-log trajectories for the node: NODE_START, NODE_END, the node's channel writes, and the enclosing STEP_END. The NODE_END event on a hit SHALL carry a cached marker（`cached: true`）plus the cache key hash; the runtime SHALL NOT synthesize LLM_REQUEST / LLM_RESPONSE events for hits（审计日志记录实际发生的事，不虚构模型调用）。Cache contents SHALL never enter the event log, and fold / replay / fork results SHALL be identical whether a step was served from cache or executed. Clearing the cache SHALL NOT change any state rebuild.

#### Scenario: Log trajectory identical on hit and miss
- **WHEN** the same node with the same input executes once as a miss and once as a hit
- **THEN** both runs SHALL emit the same event types in the same order with the same channel writes, differing only in the NODE_END cached marker and key hash

#### Scenario: No synthetic LLM events on hit
- **WHEN** a CONVERSATION node result is served from cache
- **THEN** the log SHALL NOT contain an LLM_REQUEST or LLM_RESPONSE event for that superstep（无调用即无模型事件）

#### Scenario: Fold equivalence across hit and miss
- **WHEN** a session is restored (fold) after steps that were served from cache
- **THEN** the rebuilt channel state SHALL be identical to the state rebuilt from executed steps with the same inputs

#### Scenario: Cache flush does not affect rebuild
- **WHEN** the entire node cache is flushed between two restores of the same log
- **THEN** both restores SHALL produce identical channel state

### Requirement: Node cache storage and key function registry
The node cache SHALL be an in-memory per-replica store with per-entry TTL expiry and LRU eviction under a bounded entry cap. Key functions SHALL be registered by name via a runtime registry（`register_node_key_func(name, fn)`），mirroring the accumulator reducer registry; the registry SHALL expose listing. The cache SHALL be discardable at any time without correctness impact（ADR-030：缓存是优化不是权威）。

#### Scenario: LRU eviction under cap
- **WHEN** storing a new entry would exceed the cache's entry cap
- **THEN** the least-recently-used entry SHALL be evicted first

#### Scenario: Registry listing
- **WHEN** key functions are registered under several names
- **THEN** the registry SHALL list all registered names

#### Scenario: Cache is discardable
- **WHEN** the cache is cleared mid-execution at any point
- **THEN** execution SHALL continue correctly with cache misses（仅成本变化，无状态变化）
