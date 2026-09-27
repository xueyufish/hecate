# Tasks: runtime-boundary-pluggability

## 1. 适配器迁出 runtime（#10）

- [x] 1.1 `git mv src/hecate/runtime/agent_execution_port.py src/hecate/core/composition/agent_execution_port.py`；`core/composition/runtime_port_adapter.py` 的函数内导入改为模块顶导入。验证：`rg -c "agent_execution_port" src/hecate/runtime/` 仅剩 AGENTS.md（随后删行）；`mypy src/` 通过。
- [x] 1.2 `runtime/AGENTS.md`：删除 AgentExecutionPort 两行挂账，迁移说明更新。验证：文档自查。
- [x] 1.3 消费方回归：`pytest tests/test_runtime/test_tool_receipts.py tests/test_runtime/test_chat_engine_convergence.py -q`（覆盖 ToolWorker 与 adapter 委托路径）。验证：全绿。

## 2. 探针 AST 化（执法补洞）

- [x] 2.1 `tests/test_runtime/test_runtime_self_sufficiency.py`：新增 AST 预扫阶段——遍历 `runtime/**/*.py` 全部 `Import`/`ImportFrom` 节点（含函数内/条件/try），对照 `ALL_BLOCKED_PREFIXES`，失败信息含文件与行号。验证：对迁移后的 runtime 全绿；临时注入一个函数内懒导入的用例（测试内构造）证明能捕获后移除。
- [x] 2.2 若探针翻出清单外漏网导入：逐个补入 runtime/AGENTS.md 清单（挂账）或当场修正。验证：探针全绿且清单完整。

## 3. 契约测试套件（#11）

- [x] 3.1 新增 `tests/test_runtime/contracts/`（含 README：新基础设施实现必须注册进参数列表）：`test_event_store_contract.py` — 参数化 `[InMemoryEventStore, FakeEventStore]`（测试内手写第二实现），覆盖 append/get_events/版本语义/`get_tool_receipt` 回执查询；两 store 分别注入 ToolWorker 跑同一工具用例断言行为一致。验证：全绿。
- [x] 3.2 `test_memory_provider_contract.py` — Protocol 契约 + Fake 覆盖 search/生命周期语义；生产 impl 导入与签名冒烟（诚实边界写入 docstring）。验证：全绿。
- [x] 3.3 `test_llm_port_contract.py` — RuntimePort 的 `llm_invoke`/`llm_invoke_structured` 契约：`_ProductionRuntimePort`（stub 底座）与 FakeLLMPort 对同一脚本 turns 产出结构一致。验证：全绿。

## 4. 清单退出条件与门禁

- [x] 4.1 `runtime/AGENTS.md` 清单逐条补退出条件（可验证条件，非愿望；RuntimePort 拆分条件亦记入）。验证：文档自查。
- [x] 4.2 门禁：`ruff check src/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/test_runtime tests/test_core tests/test_services/test_workflow -q`（受影响范围；全量归 CI）。验证：0 错误。
- [x] 4.3 `openspec validate runtime-boundary-pluggability` 通过。
- [x] 4.4 AGENTS.md 停增 ABC 规则一条（本 change archive 时落地，tasks 占位提醒）。
