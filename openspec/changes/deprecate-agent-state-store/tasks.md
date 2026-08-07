## 1. 代码废弃标记

- [x] 1.1 在 `src/hecate/services/state/store.py` 模块顶部添加 `__deprecated__ = ("Use hecate.engine.session_state.SessionStateStore instead.",)`（PEP 562）
- [x] 1.2 在 `src/hecate/services/state/store.py` 的 `AgentStateStore` ABC docstring 顶部加 `.. deprecated::` directive，指向 `hecate.engine.session_state.SessionStateStore` 与 `docs/migrations/agent-state-store.md`
- [x] 1.3 在 `src/hecate/services/state/store.py` 的 `InMemoryStateStore` docstring 顶部加 `.. deprecated::` directive，内容同 1.2
- [x] 1.4 在 `src/hecate/services/state/store.py` 的 `InMemoryStateStore.__init__` 顶部加 `warnings.warn(..., DeprecationWarning, stacklevel=2)`，message 引用 `SessionStateStore` 与迁移文档
- [x] 1.5 在 `src/hecate/services/state/store.py` 文件顶部加 `import warnings`（如果尚未 import）

## 2. WorkflowExecutionService 参数废弃

- [x] 2.1 在 `src/hecate/services/workflow/execution_service.py` 顶部加 `import warnings`（如果尚未 import）
- [x] 2.2 在 `WorkflowExecutionService.__init__` 顶部加条件性 `if state_store is not None: warnings.warn(..., DeprecationWarning, stacklevel=2)`，message 引用 `checkpoint_store` 参数与迁移文档
- [x] 2.3 在 `WorkflowExecutionService.__init__` 的 `state_store` 参数 docstring 加 `.. deprecated::` directive（若 Sphinx 渲染）或 `.. deprecated:` block（reST），内容指向 `checkpoint_store` 与迁移文档

## 3. 迁移文档

- [x] 3.1 创建 `docs/migrations/` 目录（如果不存在）
- [x] 3.2 创建 `docs/migrations/agent-state-store.md`，包含 6 段：Why deprecated、Migration mapping table、Key migration、Code example、Configuration、Support window
- [x] 3.3 在文档文档底部加 "OpenSpec spec" 链接：`[distributed-session-state-store spec](../../openspec/specs/distributed-session-state-store/spec.md)`
- [x] 3.4 在文档文档底部加 `13.4a-7` follow-up 提示（"Hard removal scheduled for ≥ next minor version"）

## 4. feature-catalog 与 roadmap 同步

- [x] 4.1 编辑 `docs/features/feature-catalog.md` line 403：`13.4a` row 的 `**NOT DONE**: Change 6 ... pending` 改为 `**RESOLVED (deprecation)**: Change 6 deprecation implemented in 13.4a-6 (Aug 2026); hard removal in 13.4a-7 (≥ next minor)`
- [x] 4.2 在 `13.4a` row 的描述末尾加一句："AgentStateStore ABC deprecated in 13.4a-6; migration guide at docs/migrations/agent-state-store.md"
- [x] 4.3 编辑 `docs/features/roadmap.md` line 458：`13.4a` row 描述加 `(deprecated in 13.4a-6)` 标注
- [x] 4.4 在 `roadmap.md` 新增 "Pending cleanups" 段（或合适的位置），加入 `13.4a-7 | AgentStateStore hard removal` 条目，标注"≥ next minor after 13.4a-6"

## 5. 验证

- [x] 5.1 跑 `python -m pytest tests/test_services/test_workflow/test_execution_service.py -q` —— 23 个既有测试零修改通过
- [x] 5.2 跑 `python -m pytest tests/test_services/test_state/ -q` —— 既有 `InMemoryStateStore` / `AgentState` 测试通过
- [x] 5.3 跑 `python -m pytest tests/test_services/test_session_state/ -q` —— 39 个 SessionStateStore 测试通过（确保未引入回归）
- [x] 5.4 跑 `ruff check src/hecate/services/state/ src/hecate/services/workflow/execution_service.py` —— 干净
- [x] 5.5 跑 `ruff format --check src/hecate/services/state/ src/hecate/services/workflow/execution_service.py` —— formatted
- [x] 5.6 跑 `mypy src/hecate/services/state/ src/hecate/services/workflow/execution_service.py` —— no issues
- [x] 5.7 跑 `python -c "import warnings; warnings.simplefilter('always'); from hecate.services.state.store import AgentStateStore"` —— 触发 DeprecationWarning，message 含 `SessionStateStore`
- [x] 5.8 跑 `python -c "import warnings; warnings.simplefilter('always'); from hecate.services.state import InMemoryStateStore; InMemoryStateStore()"` —— 触发 DeprecationWarning，message 含 `SessionStateStore`
- [x] 5.9 跑 `python -c "import warnings; warnings.simplefilter('always'); from hecate.services.workflow.execution_service import WorkflowExecutionService; WorkflowExecutionService(port=MagicMock(), state_store=MagicMock())"` —— 触发 DeprecationWarning 提及 `checkpoint_store`
- [x] 5.10 跑 `openspec validate deprecate-agent-state-store --strict` —— artifacts 完整
- [x] 5.11 跑 `git grep "AgentStateStore" src/` —— 非空（确认未删除代码）

## 6. PR 与 merge

- [ ] 6.1 跑 `git diff main` 审查：仅改 `src/hecate/services/state/store.py`、`src/hecate/services/workflow/execution_service.py`、`docs/features/feature-catalog.md`、`docs/features/roadmap.md`、新增 `docs/migrations/agent-state-store.md`
- [ ] 6.2 创建 branch `feat/deprecate-agent-state-store`
- [ ] 6.3 提交（commit message 格式 `feat(deprecation): mark AgentStateStore deprecated, hard removal in 13.4a-7`）
- [ ] 6.4 推送 + 开 PR
- [ ] 6.5 等 pre-commit hook 通过（ruff / format / mypy / pytest scoped）
- [ ] 6.6 合并到 main