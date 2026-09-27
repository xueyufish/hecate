# Design: runtime-boundary-pluggability

## Context

- **迁移对象单一**：`runtime/agent_execution_port.py`（AgentExecutionPort）是评审点名、`runtime/AGENTS.md` 挂账的具体适配器。消费方仅两处：`core/composition/runtime_port_adapter.py` 的函数内导入（`knowledge_query` 委托）与文档引用。它模块级 import SQLAlchemy + AgentModel/KnowledgeBaseModel/ToolModel + `core.composition.memory_provider`；函数级 import `studio.skill.loader`、`studio.agents.handoff`、`narrowed_tool_names`。
- **执法洞**：`tests/test_layering_domain.py` 的 AST 扫描只看模块顶层导入（注释自述函数内导入"invisible to runtime and therefore allowed"）；`tests/test_runtime/test_runtime_self_sufficiency.py` 是子进程探针，`ALL_BLOCKED_PREFIXES` 存在但同样不覆盖懒导入。
- **第二实现现状**（#11 的好消息）：EventStore（InMemory + `studio/event_state/postgres_store.py`）、SessionStateStore（InMemory + Postgres）、CheckpointStore（ABC + Postgres `services/checkpoint_store.py`）、MemoryProvider（`core/composition/memory_provider.py` Protocol + `hecate-memory` impl）。缺口只在**契约测试**——没有任何测试证明同一组 Worker 用例对两个实现都通过。
- **runtime/AGENTS.md** 已有"允许的跨域依赖"清单（tool_access→shell_analysis、coordinator_worker→studio.workflows、agent_execution_port→handoff/span_adapter、egress→dlp 等），无退出条件列。
- **tool_access.py 的 shell_analysis**：函数内导入 + 纯函数调用（无 I/O 对象可注入），且 runtime/AGENTS.md 已挂账。为它建 ABC 属于"无第二实现的扩展点"——#11 纪律反对。

## Goals / Non-Goals

**Goals:**

- AgentExecutionPort 迁出 runtime；runtime 对业务前缀（含懒导入）依赖归零。
- 自足探针 AST 全量覆盖，懒导入逃逸从此被抓。
- EventStore / MemoryProvider / LLM port 三条契约测试线（生产实现 + 契约替身参数化），Worker 零修改可替换。
- 跨域清单每条带退出条件；停增 ABC 纪律入 AGENTS.md。

**Non-Goals:**

- 不拆 RuntimePort 胖接口——全部消费者经单一适配器使用全表面，拆分是纯 churn；等出现"只用子面"的真实第二消费者再拆（记入清单退出条件）。
- 不迁移清单上其余越界者（coordinator_worker→studio.workflows 属动态编排特性本身；task_memory_hook/guardrail_assembly 的 ORM 读取是其特性实现；egress 的 dlp 已走 DI+函数内回退）——各自补退出条件后按条件另行立项。
- 不为 shell_analysis 建 ABC（无第二实现，纯函数依赖，清单挂账即可）。
- 不动 Temporal（前一批已 fail-fast，其活动集成另行立项）。
- 不改任何 Worker / 生产运行时逻辑。

## Decisions

### D1: 迁移 = `git mv` + 消费方改路径，不建兼容 shim

`agent_execution_port.py` → `core/composition/agent_execution_port.py`。消费方仅 `runtime_port_adapter.py` 的函数内导入一处（改为模块顶导入——它本就在 composition，无需懒加载）；`runtime/AGENTS.md` 清单同步删行。**不建 re-export shim**：仓库内部消费方唯一，shim 只会延续旧路径的幻觉。备选（否决）：保留 `runtime/agent_execution_port.py` 为 re-export——违反本 change 的自足主张。

### D2: 探针 AST 化 = 在既有子进程探针内加 AST 预扫

`test_runtime_self_sufficiency.py` 增加第一阶段：遍历 `runtime/**/*.py`，AST 解析后收集**所有** `Import`/`ImportFrom` 节点（不限顶层），对照 `ALL_BLOCKED_PREFIXES` 匹配。子进程探针保留（运行时行为验证），AST 预扫提供精确到文件/行号的失败信息。备选（否决）：改 `test_layering_domain.py` 全域 AST 化——影响所有域的判定语义，超出本 change；runtime 先行，全域化留作后续。

### D3: 契约测试 = 参数化用例集 + 显式契约替身（不引新框架）

`tests/test_runtime/contracts/`：
- `test_event_store_contract.py` — 用例参数化为 `[InMemoryEventStore, FakeEventStore]`（Fake 为测试内手写第二实现：dict 存储、版本递增、get_events 语义对齐），覆盖 append/get_events/版本冲突/回执查询（`get_tool_receipt`）；再用两个 store 分别注入 ToolWorker 跑同一工具用例，断言 Worker 行为一致。
- `test_memory_provider_contract.py` — 以 memory provider Protocol 为契约，生产 impl（hecate-memory）可导入性冒烟 + Fake 覆盖 search/生命周期语义（生产 impl 依赖外部向量库，契约集以 Fake 对 Fake 的"协议一致性"为主，生产侧做导入与签名冒烟——诚实边界，写入用例 docstring）。
- `test_llm_port_contract.py` — RuntimePort 的 llm_invoke/llm_invoke_structured 契约：`_ProductionRuntimePort`（stub 底座）与 FakeLLMPort 对同一脚本化 turns 产出结构一致（内容块/终止块/usage 透传）。
规则：任何新基础设施实现合入时必须在参数列表里加自己的名字（清单即执法），写入 contracts/README。

### D4: 清单退出条件格式 = 表格加一列，逐条可执行

runtime/AGENTS.md 清单每条补"退出条件"，写法为可验证条件而非愿望（例：coordinator_worker→studio.workflows——退出条件：动态编排模板 DSL 化完成、模板构建经参数注入时移除；tool_access→shell_analysis——退出条件：shell 分析出现第二实现或被引擎 hook 取代时提升为注入接口）。`AgentExecutionPort` 两行随迁移删除。

### D5: 停增 ABC 规则进 AGENTS.md（本 change 的 archive 步骤执行）

规则一行："运行时扩展点（ABC/Protocol）无第二实现且无明确消费者时不新增；提议时必须附第二实现计划或消费者。" 落在 Conventions/Coding rules 附近，一行即止。

## Risks / Trade-offs

- [迁移触碰 import 路径] `runtime_port_adapter` 的函数内导入改动极小；无外部包消费该适配器 → 风险近零。
- [探针 AST 化可能翻出存量懒导入] 迁移后 runtime 应为零命中；若探针翻出清单外的漏网导入 → 逐个补清单（挂账）或当场修，测试先红后绿是预期过程。
- [契约 Fake 与生产实现语义漂移] Fake 对齐的是当前契约用例，生产实现演化时用例可能不再代表真实语义 → 契约用例以既有 Worker 测试的断言为蓝本，生产实现侧的冒烟测试守底。
- [MemoryProvider 生产契约覆盖弱] 生产 impl 需要向量库，CI 无法跑真实语义 → 明确记录为诚实边界；协议签名冒烟 + Fake 契约先行，真实双实现对跑留待 hecate-memory 测试基建。

## Migration Plan

1. `git mv` 迁移 + 消费方路径更新（一次提交内完成，避免中间态 import 断裂）。
2. 探针 AST 化合入——若翻出漏网导入，同批补清单或修正。
3. 契约套件合入并在 contracts/README 记录"新实现必须注册"规则。
4. 回滚 = revert（无迁移、无行为变化）。

## Open Questions

无——迁移面、执法面、契约面的事实均已核实。
