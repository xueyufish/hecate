# agent-deployment Delta

## Purpose

定义 Agent principal 与 AgentDeployment 的登记契约:负责人与组织归属、生命周期与身份映射、同一 AgentVersion 到不同执行后端的部署登记、托管后端双轴配置、旧 Agent 的 builtin 回填兼容,以及 workspace 隔离与单一写入方规则,使后续 Task/Run(step4 第二支)与受管注册(step6/7)有可引用的部署与身份实体。

## ADDED Requirements

### Requirement: Agent principal is a governed identity entity

Agent principal MUST 作为独立登记实体与现有 Agent 关联,记录组织归属、负责人、生命周期状态与身份提供方映射。负责人 MUST 是人类用户或明确的企业责任主体(可解析到组织/用户登记),MUST NOT 以 persona 字符串充当;persona 字段可以继续存在但不承载治理语义。生命周期状态转换 MUST 显式登记并留审计字段;principal 撤销后 MUST NOT 被新 Deployment 引用。

#### Scenario: Principal links a responsible owner

- **WHEN** 登记一个 Agent principal
- **THEN** 记录指向可解析的组织与负责人,且负责人不是自由文本字段

#### Scenario: Persona is not an owner

- **WHEN** 某 Agent 仅有 persona 描述而无可映射负责人
- **THEN** principal 不被自动创建或自动赋平台身份,而是登记为待治理项

#### Scenario: Revoked principal rejects new deployments

- **WHEN** 一个生命周期已撤销的 principal 被用于新建 Deployment
- **THEN** 登记服务拒绝该操作并保留拒绝记录

### Requirement: Deployment binds version and backend axes

AgentDeployment MUST 固定其绑定的 AgentVersion、backend 类型与版本、接入方式(进程内/本地进程/远程服务)、传输契约版本、能力快照、凭据引用与部署签发域;签发域与 ID MUST 可解析为执行契约的 deployment `BackendRef`。实现语言仅为登记元数据,MUST NOT 参与权限或能力等级判定。同一 AgentVersion MUST 能够登记多个不同后端的 Deployment;同一 Agent 的默认部署标记 MUST 至多一个。

#### Scenario: Same version registers two backends

- **WHEN** 同一 AgentVersion 分别登记 builtin 与一个远程后端 Deployment
- **THEN** 两条 Deployment 记录并存且互不覆盖,各自拥有独立标识

#### Scenario: Deployment resolves to BackendRef

- **WHEN** 将某 Deployment 传入执行契约调用
- **THEN** 其签发域与 ID 构成合法 deployment `BackendRef`,无需查询平台 ORM 即可被后端方解析

#### Scenario: Language metadata does not grant standing

- **WHEN** 两个 Deployment 仅实现语言元数据不同
- **THEN** 权限、准入与能力判定结果一致

### Requirement: Hosted backend configuration records verifiable axes

托管后端的 Deployment 配置 MUST 记录 harness 提供方与环境提供方(复用执行契约的 OwnershipAxes 语义)、服务地区、数据驻留与保留/删除条件、供应商内部工具范围及企业网关路径。上述条件缺少可核实值时,MUST 登记为未核验,MUST NOT 默认判为私有部署、本地驻留或强制治理等级;登记值 MUST 保留来源与核验时间字段。

#### Scenario: Unverified residency is explicit

- **WHEN** 某托管后端 Deployment 未提供可核实的驻留声明
- **THEN** 配置记录显式标注未核验,下游绑定不得将其当作满足私有部署条件

#### Scenario: Axes match the execution contract

- **WHEN** 读取托管后端 Deployment 的双轴配置
- **THEN** 其取值可与执行契约的 `OwnershipAxes` 互相转换而不丢失枚举语义

### Requirement: Legacy agents backfill to builtin deployment

对存量 Agent,迁移 MUST 为每个 Agent 建立默认 builtin Deployment(backend_type=builtin、进程内接入)并标记为该 Agent 的默认部署;回填后既有执行链路行为 MUST 不变。存量 Agent MUST NOT 自动获得 principal 平台身份映射;不可映射负责人或组织的数据 MUST 登记为待治理并保持可执行,不得阻塞 builtin 回填。

#### Scenario: Legacy agent keeps executing

- **WHEN** builtin 回填完成后运行既有的 Agent chat/workflow 执行
- **THEN** 执行路径与结果与回填前一致

#### Scenario: Missing governance data stays pending

- **WHEN** 某 Agent 缺少可映射的负责人或组织
- **THEN** 其 builtin Deployment 正常建立,待治理项被登记且不自动生成 principal 映射

### Requirement: Deployment registry is single-writer and workspace-isolated

Deployment 与 principal 的登记 MUST 经过 execution 域的唯一应用服务写入;其他域 MUST NOT 直接写这两张表或并行维护同义映射(禁止双写)。跨 workspace 读取或指定其他 workspace 的 Deployment/principal MUST 被拒绝;同一 AgentVersion 的 Deployment 集合变更 MUST 留下可查询的登记记录。

#### Scenario: Cross-workspace access is denied

- **WHEN** 以 workspace A 的上下文查询或指定 workspace B 的 Deployment
- **THEN** 服务返回拒绝且不泄露目标记录存在性

#### Scenario: Single writer enforced

- **WHEN** execution 域之外出现对 Deployment/principal 表的直接写调用
- **THEN** 分层/边界测试将其识别为违规并失败

#### Scenario: Registration is auditable

- **WHEN** 某版本登记新 Deployment 或变更默认部署标记
- **THEN** 登记记录可按 Agent 与时间查询到操作与结果
