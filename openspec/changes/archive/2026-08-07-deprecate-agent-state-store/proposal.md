## Why

`AgentStateStore`（`src/hecate/services/state/store.py`）是 SessionState 抽象（`src/hecate/engine/session_state.py`）之前的工作会话状态持久化实现。13.4a 1/5 → 5/5 已完成 `SessionStateStore` 的端到端替代（双键 `(agent_id, session_id)` → 三键 `(org_id, user_id, session_id)`、单 process in-memory → Redis + Postgres + Tiered、单个 superstep 同步 → 跨请求 jitter retry 锁语义）。当前 `AgentStateStore` 仅遗留 `InMemoryStateStore` 一个未实现 dashboard 功能的写空路径、`WorkflowExecutionService.__init__` 一个 `state_store` 参数、以及 archive 4 处历史文档引用——任何外部调用方（plugin / SDK / user fork）若继续使用 `AgentStateStore` 会让"session 真的能跨副本持久化"的核心承诺无声失效。feat-catalog 13.4a 进度条 line 403 明确标注 `NOT DONE: Change 6 AgentStateStore removal pending`，本 change 兑现该收尾。

## What Changes

- `src/hecate/services/state/store.py`：在 `AgentStateStore` ABC 与 `InMemoryStateStore` 实现上添加 Python 3.12 `__deprecated__` module-level attribute（PEP 562）+ 构造时 `warnings.warn(DeprecationWarning, stacklevel=2)`。两个类的 docstring 顶部加 `.. deprecated::` 标记指向 `SessionStateStore` 与 `docs/migrations/agent-state-store.md`。
- `src/hecate/services/workflow/execution_service.py`：`__init__` 上的 `state_store: AgentStateStore | None = None` 参数改为发出 `DeprecationWarning`（保留向后兼容以不破既有 23 个 `test_execution_service.py` 测试）。chat.py 调用路径已不传 `state_store`（spec line 437 已固定），本 change 不动 chat.py 行为。
- `src/hecate/services/state/__init__.py`：`AgentStateStore` 与 `InMemoryStateStore` 标记为 deprecated re-export（添加 `DeprecationWarning` 触发于 `from hecate.services.state import AgentStateStore`）。
- `src/hecate/services/state/state.py` 的 `AgentState` Pydantic model **保留** —— 它仍是 `SessionState.agent_state` 的 typed validation 入口（spec line 367 引用 `AgentState.model_validate(state.agent_state)`），删除它会破坏现有 chat 路径。`AgentState` 的 `summary` / `context` / `permission_context` / `tool_context` / `task_context` / `environment_root` / `metadata` 字段继续作为 `SessionState.agent_state` dict 的类型规约。
- **不删除**任何文件：不删除 `store.py` / `state.py` / `__init__.py` —— 硬删除归未来 `13.4a-7` change（与 release cadence 对齐，留至少 1 个 release cycle 给外部用户）。
- 新增 `docs/migrations/agent-state-store.md`：用户迁移指南，含为什么 deprecated、迁移到 `SessionStateStore` 的代码示例、OpenSpec spec 引用、版本支持时间表。
- `README.md` 加 deprecation 公告横幅（如果 README 含 architecture 章节）。
- `openspec/specs/distributed-session-state-store/spec.md` 加入 **Requirement: AgentStateStore deprecation** 子节，覆盖 ABC warning、构造 warning、向后兼容窗口、硬删除时间表。
- ADR-020 (`docs/design/adr/020-async-execution-distributed-state.md`) 不是本 change 改动对象（它是设计背景）；仅在迁移文档中引用。

**BREAKING**：无 —— `AgentStateStore` 暂时保留，所有 warning 默认只走 `warnings.warn`（Python 默认 `default` filter 屏蔽 `DeprecationWarning`），不影响运行行为。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `distributed-session-state-store`：新增 Requirement 子节覆盖 AgentStateStore 软废弃（构造 warning + module-level `__deprecated__` + 用户迁移指南 + 硬删除时间表）。

## Impact

### 受影响代码

- `src/hecate/services/state/store.py`（+ warning + `__deprecated__`）
- `src/hecate/services/state/__init__.py`（+ re-export warning）
- `src/hecate/services/state/state.py`（**不变** —— `AgentState` model 保留）
- `src/hecate/services/workflow/execution_service.py`（`__init__` 参数 DeprecationWarning）
- `tests/test_services/test_state/test_state.py`（不变 —— 测试代码继续 import `AgentStateStore` 应触发 warning，pytest 默认 filter 不显示，可静默通过）
- `tests/test_services/test_workflow/test_execution_service.py`（不变 —— 23 个测试继续 import + 使用 `state_store=mock`，warning 被默认 filter，静默通过）

### 受影响文档

- `docs/migrations/agent-state-store.md`（新建）
- `README.md`（可能加公告横幅）
- `docs/features/feature-catalog.md` line 403（修改 `NOT DONE` 状态为 `Deprecated`，标注 13.4a-7 硬删除时间表）
- `docs/features/roadmap.md` line 458（`13.4a` 状态从 `✅ (5/5)` 调整为 `Deprecated` 前的 `✅ (5/5)` + deprecation 备注，或保持不变由 `13.4a-7` 一次性更新）
- `openspec/specs/distributed-session-state-store/spec.md`（新增 Requirement 子节）

### 外部接口

- 第三方依赖（plugin / SDK / user fork）：若 import `from hecate.services.state import AgentStateStore`，将触发 `DeprecationWarning`（被默认 filter 屏蔽），但 `python -W default` 或 `warnings.simplefilter("always", DeprecationWarning)` 会显示。本 change **不删任何公开 API**，下游代码继续工作直到 13.4a-7。
