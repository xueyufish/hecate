# Proposal

## Why

演进方案 step6"新增共同执行语义"首条（方案第 430 行）要求：**已接受模型决策、动作集合、配置/输入引用和继续位置纳入可靠提交；决策提交后才能派发其动作；已提交决策恢复不重新问模型替换尚未结算的动作**。执行驱动边界（#268）已就位，现有 Pregel 路径已按其支持范围保存日志、Channel 与 checkpoint，但尚无脱离图执行的已接受决策记录与单步恢复接缝；本次不否定或重授原有图路径的恢复证据。本 change 交付其中最小闭环：**单个 Agent 决策步骤的持久接受与恢复**，暂不实现完整 Controller 循环。

## What Changes

1. **新增已接受决策记录与 Runtime 接缝**：`hecate_runtime` 内新增 `AcceptedDecisionRecord` 数据类（部署作用域 Run 引用 `RunRef`、稳定决策步骤标识、输入/配置版本引用、被接受的完整模型输出、动作集合含稳定动作键与可恢复的实际参数——不只摘要；不记录隐藏推理）与 `AcceptedDecisionStore` ABC（照 `ActionLedgerHook` 先例：kernel 持有接缝，宿主/平台注入实现）。ABC 提供：`lookup`（按 run + 决策步骤恢复查找）、`commit`（原子提交，内容相同幂等返回原记录、内容不同显式冲突、携带 ownership/fencing 上下文，过期拒绝）。
2. **hecate-durable 持久化实现**：`storage/models.py` 新增决策表（独立 declarative base，PG/SQLite 可移植，部署作用域唯一约束），SQL 实现原子提交并复用既有事务、事件/outbox、`LeaseManager`/`StaleFenceError` fencing——不建第二套。平台部署侧按 `a2b3c4d5e6f7` 先例新增 alembic 迁移镜像该表。InMemory 双实现通过注入已有权威租约校验器实现，SQL 与 InMemory 注册进同一参数化契约套件（runtime-pluggability 双实现规则）。
3. **Runtime 单步决策组件（实际消费者）**：新增组件按"先查后问"工作——`lookup` 命中已接受决策则直接返回原记录（不调用模型）；未命中才经注入的模型能力（可注入的决策提供方，测试用脚本化替身）获取响应、校验动作集合后 `commit`，提交成功才返回已接受记录。**存储不可用是显式失败，不得当作"尚无决策"再次请求模型**。Runtime 只依赖自身契约；不派发业务工具。
4. **验证**：确定性断言用脚本化模型替身；决策提交后进程重启（新进程实例）返回原输出/参数/动作键且模型调用次数不增加；提交失败不产生"已接受"；同步骤重复/冲突提交幂等或显式冲突；并发提交与旧 owner 写入经真实 PostgreSQL 验证（`HECATE_STEP6_POSTGRES_URL` 既有逐测试 schema 开关，未配置显式 skip 且不计为通过）。

**边界**：不派发业务工具；不实现审批等待、Plan Revision、Child Run、完整多轮循环或入口切流；不把这个步骤标成完整 Controller 已交付；脚本化模型不构成真实模型质量认证。

## Capabilities

### New Capabilities

- `accepted-agent-decision`: Runtime 单步决策组件的可观察行为——先查后问、恢复不加模型调用、提交成功才算接受、存储不可用显式失败、决策记录不含隐藏推理、动作集合可恢复实际参数。

### Modified Capabilities

- `durable-execution-contract`: 新增"已接受 Agent 决策的持久化语义"要求——原子提交与幂等/冲突判定、部署作用域唯一性、fencing 拒绝过期写入、存储不可用显式失败、双实现注册参数化契约套件。

## Impact

- **代码**：`packages/hecate-runtime/src/hecate_runtime/`（新增接缝模块与单步组件）；`packages/hecate-durable/src/hecate_durable/storage/`（新表 + SQL 实现）、`packages/hecate-durable/src/hecate_durable/contracts/`（如需桥接数据类）；`packages/hecate-durable/src/hecate_durable/runtime_hook.py`（SQL/InMemory 双实现）；`alembic/versions/`（新增迁移，平台侧镜像表）。
- **测试**：`packages/hecate-durable/tests/`（参数化契约套件 + PG 并发/恢复）、`packages/hecate-runtime/tests/`（组件行为）、`tests/test_execution/`（接入 `HECATE_STEP6_POSTGRES_URL` 既有 harness 的重启恢复）。
- **数据库**：新增一张决策表（expand-only，无既有表变更）；平台 alembic 新增一个 revision。
- **不涉及**：工具派发、审批流、聊天路由/开关、feature-catalog/inventory 生成区（如需登记 1.3.x 条目按增量规则另行追加，不阻塞本 change）。
