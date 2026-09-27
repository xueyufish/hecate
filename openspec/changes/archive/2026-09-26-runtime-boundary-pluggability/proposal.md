# Proposal: runtime-boundary-pluggability

## Why

外部安全评审 P1 #10 + #11：runtime 包"自包含"只保证了可导入，不等于业务路径可独立运行。

- **#10 具体适配器住在 runtime 里**：`runtime/agent_execution_port.py`（`AgentExecutionPort`，RuntimePort 的具体适配器）模块级依赖 SQLAlchemy、三个 ORM 模型、`core.composition` 的 memory provider，另有函数级依赖 studio 的 handoff/skill loader——它 `runtime/AGENTS.md` 自己的清单里就是挂账的越界者。同类的函数内懒导入（coordinator_worker→studio.workflows、task_memory_hook→memory ORM、guardrail_assembly→ORM）以"函数内 import"充当解耦。
- **分层执法有洞**：`test_layering_domain.py` 的 AST 扫描**明确放行函数内懒导入**（注释自述"they are invisible to runtime and therefore allowed"）——评审点名"分层扫描跳过函数、条件和 try 内的导入，不能覆盖所有运行路径"。
- **#11 扩展点缺替换证明**：EventStore/SessionStateStore/CheckpointStore/MemoryProvider 的第二实现**已经存在**（InMemory + Postgres、Protocol + impl），但没有任何契约测试证明"换实现不需要改 Worker"；`RuntimePort` 是 9+ 方法的胖接口，新增 ABC 无纪律约束。

## What Changes

- **适配器出 runtime**：`runtime/agent_execution_port.py` → `core/composition/agent_execution_port.py`（组合根本就允许知晓一切，其 studio/models 依赖在彼处合法）；更新两处消费方（`runtime_port_adapter.py` 懒导入、文档引用）。
- **执法补洞**：`test_runtime_self_sufficiency.py` 的运行时探针升级为 AST 全量扫描（含函数内/条件/try 导入），blocked 前缀不变——迁移后 runtime 对业务模块的依赖（含懒导入）归零，探针从此真正全覆盖。
- **契约测试套件（#11 核心交付）**：新增 `tests/test_runtime/contracts/`，用同一组契约用例参数化跑"生产实现 + 契约替身"两套基础设施（EventStore、MemoryProvider、LLM port），证明替换实现**不需要修改任何 Worker**——这是"第二实现"从存在变为被验证。
- **越界清单补退出条件（#11）**：`runtime/AGENTS.md` 的既有跨域依赖清单逐条补退出条件（何时、以何种方式移除）；本期不迁移清单上的其余项（coordinator_worker→studio、task_memory_hook、guardrail_assembly、egress→dlp 各有明确退出条件后另行处理）。
- **停增 ABC 纪律**：AGENTS.md 增补一条规则（无第二实现或明确消费者的扩展点不新增）——随本 change 落地。

## Capabilities

### New Capabilities

- `runtime-pluggability`: runtime 包的业务自足语义——runtime 内（含懒导入）无业务模块依赖；基础设施实现可替换且 Worker 零修改（契约测试为证）；跨域依赖清单带退出条件维护。

### Modified Capabilities

（无——适配器迁移是代码位置变化；探针强化是既有自足意图的执法补全，均归入新 capability 表述。）

## Impact

- **代码**：`runtime/agent_execution_port.py` 移至 `core/composition/`（内容微调：导入路径不变因依赖本就在彼处）、`core/composition/runtime_port_adapter.py`（消费方路径）、`tests/test_runtime/test_runtime_self_sufficiency.py`（探针 AST 化）、`runtime/AGENTS.md`（清单 + 退出条件）、`AGENTS.md`（停增 ABC 规则，archive 时）、新增 `tests/test_runtime/contracts/`。
- **行为**：运行时行为零变化（纯位置迁移 + 测试增强）。
- **风险面小**：单一文件的迁移 + 测试文件，无生产逻辑改动。
- **无数据库迁移**；无 BREAKING。
