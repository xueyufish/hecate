## Context

`SessionStateStore` 抽象（`src/hecate/engine/session_state.py`）在 13.4a 1/5 → 5/5 全部 5 个 change 中逐步建立并替代了 `AgentStateStore`（`src/hecate/services/state/store.py`）。当前 `AgentStateStore` 在 Hecate 内部代码中已无活跃调用方：`execution_service.py` 的执行路径已完全迁移到 `_checkpoint_store: SessionStateStore`（spec line 367-372 引用，`self._state_store` 仅在 `load` 路径保留向后兼容分支）。`AgentStateStore` 仍存在的原因：保留对 `test_execution_service.py` 中 23 个既有测试的向后兼容（它们用 `state_store=mock_state_store` 验证旧路径）以及外部用户对 `services.state` 子模块的历史 import 习惯。

`AgentState` Pydantic model 本身（`src/hecate/services/state/state.py`）**不能删除**——它是 `SessionState.agent_state` dict 的 typed validation 入口（spec line 367 引用 `AgentState.model_validate(state.agent_state)`），删除会破坏既有 chat 路径。`AgentStateStore` ABC 本身（`save/load/delete/list_sessions` 方法）才是被取代的对象。

外部风险：plugin / SDK / user fork 可能仍 `from hecate.services.state import AgentStateStore`。本 change 的目的不是立即删代码，而是**通过 Python 标准的 `DeprecationWarning` 机制 + 完整文档让外部用户有 ≥ 1 个 release cycle 迁移窗口**，然后在 `13.4a-7` 跟进硬删除。

## Goals / Non-Goals

**Goals:**

- 在 `AgentStateStore` ABC / `InMemoryStateStore` / `state_store` 参数三个用户可见点添加 PEP 562 / `DeprecationWarning` 标记，让 `python -W default` 或 `warnings.simplefilter("always", DeprecationWarning)` 立即显示警告。
- 不破坏任何现有测试（包括 23 个 `test_execution_service.py` 测试）。
- 完整迁移文档 `docs/migrations/agent-state-store.md`，覆盖代码示例、key 变化（`(agent_id, session_id)` → `(org_id, user_id, session_id)`）、配置变化、support window。
- 更新 `feature-catalog.md` / `roadmap.md` 把"pending Change 6"标记为"已 deprecate，硬删除延后到 13.4a-7"。
- spec 层面在 `distributed-session-state-store` capability 加入 deprecation Requirements（让 OpenSpec 工具链能验证 deprecation 行为）。

**Non-Goals:**

- 不删除 `services/state/` 任何文件（留给 `13.4a-7`）。
- 不删除 `WorkflowExecutionService.state_store` 参数（留给 `13.4a-7`）。
- 不修改 `AgentState` model（仍在 `SessionState.agent_state` typed validation 使用）。
- 不实现 `SessionStateStore` 自身的硬删除（13.4a 不在 deprecate 范围内）。
- 不改 chat.py 生产路径（已不传 `state_store`，无变更）。
- 不加 `__init__.py` 的 `__deprecated__`（只在子模块 `store` 上加，避免影响 `from hecate.services.state import AgentState` 的合法用法）。

## Decisions

### Decision 1: 用 PEP 562 module-level `__deprecated__` 而非 module docstring 警告

**选 PEP 562**。Python 3.12+ 推荐方式：
```python
# src/hecate/services/state/store.py
__deprecated__ = ("Use hecate.engine.session_state.SessionStateStore instead.",)
```

- **优点**：标准 PEP，PyCharm/IDLE 等 IDE 自动识别。`from store import X` 在 import time 触发（受 Python `__deprecated__` 语义），比 docstring 更可见。
- **替代方案（拒绝）**：在 `__init__.py` 顶部 `warnings.warn(...)` —— 会改变 `import` 行为且污染所有合法的内部 `from hecate.services.state import AgentState`（用于 typed validation）。PEP 562 仅在 attribute access 时警告，scope 更精确。
- **不选 `warnings.deprecated` decorator**（PEP 702，Python 3.13+ 引入）—— 项目的 `pyproject.toml` 最低 Python 3.12，兼容性不足。

### Decision 2: `InMemoryStateStore.__init__` 触发 `DeprecationWarning`，ABC 不触发

**选 concrete class 触发**。`InMemoryStateStore.__init__` 顶部加 `warnings.warn(..., stacklevel=2)`。`AgentStateStore` ABC 不触发，因为：
- ABC 不能直接 `AgentStateStore()` 实例化（Python 抽象类机制抛 `TypeError`）—— 不需要 `DeprecationWarning`。
- 在 `__init_subclass__` 加 hook 会让 `InMemoryStateStore.__init_subclass__` 也被调用，引入不必要的副作用。

**替代方案（拒绝）**：在 `__init_subclass__` 加 warning —— 增加代码复杂度，对 `InMemoryStateStore` 来说等效的副作用。

### Decision 3: `WorkflowExecutionService.__init__` 的 `state_store` 参数在提供时（非 `None`）触发 warning

**条件性 warning**。当 `state_store` 为 `None`（默认），不触发 —— 23 个既有测试用 `WorkflowExecutionService(port=...)` 默认构造，不该有 noise。当用户提供 `state_store=mock` 时才触发。

**实现方式**：`if state_store is not None: warnings.warn(..., stacklevel=2)`。

**替代方案（拒绝）**：总是触发 —— 给 23 个测试加不必要的 noise，且 chat.py 路径每次请求都会触发（虽然 chat.py 不传 `state_store`，但默认构造也会被检查）。条件性触发更精确。

### Decision 4: 不加 pytest 警告抑制

**不抑制**。`pyproject.toml` 已配置 `filterwarnings = []` 或 default 行为；既有测试运行不修改。如果未来有用户希望 CI 显式捕获 deprecation 数量，可在 `pyproject.toml` 加 `filterwarnings = ["error::DeprecationWarning:hecate.services.state.*"]`，但这是 CI 改进不属于本 change。

**替代方案（拒绝）**：在 `conftest.py` 加 `filterwarnings("ignore", DeprecationWarning, module="hecate")` —— 会掩盖未来 13.4a-7 移除时的 warning，让"硬删除生效"难以被 CI 捕获。故意暴露 warning 反而是有利的。

### Decision 5: 迁移文档路径 `docs/migrations/agent-state-store.md`

**新文件**。参考 `docs/migrations/` 目录约定（如果不存在，创建目录）。文档遵循 6 段固定结构（Why / Mapping / Key / Code Example / Configuration / Support Window）。

**替代方案（拒绝）**：放在 `docs/adr/` 或 `docs/specs/` —— 用户层文档应在 migrations 目录，符合 `feature-catalog.md` 对外发布习惯。

### Decision 6: feature-catalog 状态行从 "NOT DONE: Change 6" 改为 "RESOLVED (deprecation)"

**改文字**。行 403 当前的 `"**NOT DONE**: Change 6 AgentStateStore removal pending; K8s scaling test harness deferred to 13.4 Horizontal Scaling."` 改为 `"**RESOLVED (deprecation)**: Change 6 deprecation implemented in `13.4a-6` (Aug 2026); hard removal in `13.4a-7` (≥ next minor). K8s scaling test harness deferred to 13.4 Horizontal Scaling."`

理由：catalog 是项目级 audit 文档，状态清晰化便于外部读者立即看到 13.4a 的最新状态。

### Decision 7: `state.py` 模块（`AgentState` model 所在）不加 `__deprecated__`

`hecate.services.state.state` 子模块**不在 deprecation 范围**。原因：

- `AgentState` Pydantic model 仍被 `SessionState.agent_state` dict 用作 typed validation 入口（spec line 367 引用 `AgentState.model_validate(state.agent_state)`）。删除或 deprecate model 会破坏 chat 路径的 Pydantic 重建逻辑。
- `state.py` 与 `store.py` 是 `hecate.services.state` 包下的两个独立子模块，分别承载 model（仍活跃使用）与 ABC（被取代）。Deprecation 必须限定在被取代的 ABC 上，不能波及仍在使用的 model。
- 外部 import 路径分流：`from hecate.services.state.store import AgentStateStore`（deprecate 触发）vs `from hecate.services.state.state import AgentState`（无 warning）。后者的合法用法（`AgentState.model_validate(state.agent_state)` 重建）必须保持零 noise。

**未来触发条件**：若 `13.4a-7+` 的某次重构让 `AgentState` 也不再需要（例如 `SessionState.agent_state` 直接接受 `dict` 而非 typed model），则 state.py 加 `__deprecated__`。本 change 不预设该决策。

## Risks / Trade-offs

- [Warning 噪声] 用户启用 `python -W error` 时，`AgentStateStore` 的 deprecation warning 会变成错误。 → **Mitigation**：warning message 含 "see migration guide" + warning 默认被 `default` filter 屏蔽；CI 默认配置不会 `error`。
- [外部用户错过警告] 部分用户在生产中不启用 warning，可能错过 1+ release cycle。 → **Mitigation**：CHANGELOG 显著标注；README 顶部 deprecation banner；迁移文档。
- [Pydantic `__deprecated__` 在 Python 3.12 早期版本行为差异] PEP 562 在 Python 3.7+ 支持，但 module-level `__deprecated__` 实际是 `DeprecationWarning` 触发的 attribute lookup，行为在 3.12 已稳定。 → **Mitigation**：项目 `pyproject.toml` 强制 Python 3.12+（与 `dependencies` 段一致），无兼容性风险。
- [故意不删 `state_store` 参数] chat.py 不传，但如果有第三方 SDK 通过 `WorkflowExecutionService(state_store=...)` 调用，会触发 warning。 → **Mitigation**：warning 消息明确指向 `checkpoint_store` 替代品；`13.4a-7` 跟进硬删除。
- [feature-catalog 状态更新与 spec line 437 已有 "deprecated AgentStateStore" 重复] spec 已有，catalog 也有 —— 这是双轨文档化（spec 约束 + catalog 状态），是有意的，符合 AGENTS.md "Catalog & Roadmap sync is MANDATORY"。

## Migration Plan

无外部部署步骤 —— 本 change 是**纯软废弃**，不引入新行为。部署步骤：

1. **合并到 main** — 走 GitHub Flow，按 AGENTS.md PR 流程。
2. **release notes 公告** — 13.4a-6 在 CHANGELOG.md 显著标注 "AgentStateStore deprecated; will be removed in 13.4a-7 (≥ next minor)".
3. **README 横幅**（如果有 architecture 章节）— 添加 deprecation 链接。
4. **下个 release (≥ v0.21.0)** — 启动 13.4a-7 提案，重复 OpenSpec 流程，硬删除 `AgentStateStore` / `InMemoryStateStore` / `state_store` 参数。

**回滚策略**：如果外部用户紧急反对 deprecation 行为，可立即 revert PR。但本 change 只加 warning，不删任何代码，revert 风险为零。

## Open Questions

无。所有 3 个原候选问题项均已转化为显式决策或文档化：

- **state.py `__deprecated__`**：已升级为正式 Decision 7（`AgentState` model 与 `AgentStateStore` ABC 是独立 artifact，model 不在 deprecate 范围）。
- **CHANGELOG.md**：仓库使用 commitizen 自动从 conventional commits 生成 changelog（`pyproject.toml` `[tool.commitizen]` 段）。本 change 不手动创建 CHANGELOG.md；迁移公告通过 commit message + 迁移文档承载。
- **CI `DeprecationWarning` count check**：本 change 的 spec scenarios 5.7-5.9（tasks.md）已直接验证 warning 触发。CI count check 属于 forward-looking CI hardening，留待 `13.4a-7` 跟进时评估（届时移除代码会自然需要 CI 验证）。
