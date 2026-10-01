# Proposal

## Why

演进方案 step4（`agent-deployment-task-model`，本 change 为其拆分的第 1/2 支）要求"让同一个 Agent 定义可以部署到不同 Runtime"。step3 已交付平台→后端契约（`contracts/execution/` 的 `BackendRef`/`deployment_ref`、`OwnershipAxes` 双轴、`AgentExecutionBackend` ABC），但登记侧是空的:仓库中不存在任何 Deployment 表或 Agent principal 实体——同一 AgentVersion 无法登记到不同后端、负责人只是 `AgentModel.persona` 字符串（agent.py:41，无组织/负责人/生命周期）、托管后端的双轴数据流无登记落点。下游 `task-run-model`（step4 第二支，RunModel 需外键引用 Deployment 与身份链）和 `runtime-shared-assembly`（step5a）都以此为前置。

## What Changes

- 新增 `AgentPrincipalModel`(`models/agent_principal.py`):Agent principal 与现有 Agent 的关联、组织、负责人(人类用户或明确企业责任主体,复用 `UserModel`/`OrganizationModel` 外键)、生命周期、身份提供方映射。负责人 MUST NOT 用 persona 字符串充当;旧 Agent 缺可映射负责人时不自动赋平台身份,登记为待治理。
- 新增 `AgentDeploymentModel`(`models/agent_deployment.py`):固定 AgentVersion、backend 类型与版本、接入方式(in-process/本地进程/远程服务)、传输契约版本、endpoint/配置引用、能力快照(复用 step3 `OwnershipAxes`)、接入等级、健康状态、凭据引用、部署签发域(`issuer_domain`,与 `contracts/execution/references.py` 的 `deployment_ref` 对齐)。托管后端配置另记录 harness/环境提供方、服务地区、数据驻留与保留/删除条件、供应商内部工具范围、企业网关路径;缺少可核实条件时不得默认判为私有部署或强制治理。实现语言仅为登记元数据,不决定权限或能力等级。
- 身份链类型:区分人类发起者、Agent principal、执行工作负载身份与 on-behalf-of 委派,与 `contracts/execution/security.py` 的 `SecurityClaims`(`sub` 为工作负载身份、`delegation_ref`)对齐;本 change 交付类型定义与 Deployment 的工作负载身份绑定,Run 固定身份链由 `task-run-model` 消费。
- builtin 回填:为每个现存 Agent 建默认 builtin deployment(backend_type=builtin,in-process 接入),旧行为不变;存量 Agent 不自动建 principal 映射,按待治理登记。
- execution 域新增登记应用服务:Deployment/principal 的唯一写入方(禁止跨域直写或业务双写)、BackendRef 解析、workspace 隔离校验。
- alembic 增量迁移:新表(expand)→ 回填(migrate)→ 收紧约束(contract)。
- 不新增 HTTP 路由(登记 API 随 task-run-model/step6 交付);不改变现有执行链路行为。

## Capabilities

### New Capabilities

- `agent-deployment`:Agent principal 与 Deployment 的登记契约——principal 的负责人/组织/生命周期语义、Deployment 的版本绑定与后端双轴登记、builtin 回填兼容、workspace 隔离与单一写入方。

### Modified Capabilities

(无——`agent-versioning` 的 requirement 不变,Deployment 引用版本表但不改其行为;`user-authentication` 不涉改动。)

## Impact

- **新增**:`models/agent_principal.py`、`models/agent_deployment.py`(ORM 归共享 models/,读写权归 execution 域)、`src/hecate/execution/` 登记应用服务、身份链类型(`contracts/execution/` 或 execution 域内)、alembic 迁移 2 个 revision、`tests/test_execution/` 测试。
- **修改**:`models/__init__.py` 导出;`tests/test_layering_domain.py` 若新增子包则补边界登记。
- **不受影响**:现有 chat/workflow/MCP/A2A 执行链路零行为变更(builtin 回填只是登记,不改执行路径);API 契约、现有 spec 行为不变。
- **下游**:`task-run-model`(RunModel 外键)、`runtime-shared-assembly`(共享装配引用 builtin deployment)、`managed-runner-enrollment`(受管注册流程消费本表)。
