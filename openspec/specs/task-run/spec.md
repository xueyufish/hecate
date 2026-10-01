# task-run Specification

## Purpose

定义平台 Task 与 Run 的数据契约:Task 承载业务目标、发起者与验收责任而不承载执行事实;Run 绑定一次后端执行尝试并固化身份链、部署与配置;平台记录与独立宿主记录字段分权、独立部署注册的模型面,以及 conversation/task/run/backend-session 标识映射的单写入方规则,使 step5d/step6 与受管注册有可引用的任务与执行实体。

## Requirements

### Requirement: Task is the platform responsibility record

Task MUST 记录业务目标描述、发起者(引用 `contracts/execution/identity.py` 的身份链,含人类发起者/Agent principal/on-behalf-of 委派)、验收标准与责任归属、workspace 归属与签发域。Task MUST NOT 承载执行事实(后端状态、事件、checkpoint 引用属 Run 或执行方);Task 级工作流状态机(queued/waiting_approval 等)为 step6 交付,本能力 MUST NOT 预先引入其语义。跨 workspace 读取或指定其他 workspace 的 Task MUST 被拒绝且不泄露存在性。

#### Scenario: Task records acceptance and responsibility

- **WHEN** 通过登记服务创建一个 Task
- **THEN** 其业务目标、发起者身份链引用、验收标准与责任归属可查询,且不含任何后端执行状态字段

#### Scenario: Cross-workspace Task access is denied

- **WHEN** 以 workspace A 的上下文查询或引用 workspace B 的 Task
- **THEN** 服务返回拒绝且不泄露目标记录存在性

### Requirement: Run is one backend execution attempt

Run MUST 绑定其 Task、Deployment(引用 Change 1 的 AgentDeployment,含固定配置与能力快照引用)、递增尝试号、身份链快照(`IdentityChain` 的不可变序列化)、后端运行 ID(带签发域的后端引用)与事件序号游标。同一 Task 的重试 MUST 创建新 Run 并递增尝试号,MUST NOT 复用既有 run_id 冒充恢复;托管后端的 Run MUST 记录实际绑定的供应商 session/turn 引用。Run 的后端状态投影字段(平台保存的最近观察)与执行方权威执行状态 MUST 分列,投影 MUST NOT 覆盖执行事实。

#### Scenario: Retry creates a new Run

- **WHEN** 某 Task 的 Run 失败后发起重试
- **THEN** 新 Run 创建且尝试号为前次加一,两个 Run 的标识互不相同,均可追溯至同一 Task

#### Scenario: Run fixes the identity chain

- **WHEN** 读取一个 Run 的身份链快照
- **THEN** 其与创建时提交的 `IdentityChain` 序列化一致,后续身份或委派变更不改变该 Run 的固化值

#### Scenario: Projection never overwrites executor facts

- **WHEN** 平台侧更新某 Run 的后端状态投影
- **THEN** 仅投影字段变化,执行方权威字段的既有值不被平台写入触碰

### Requirement: Platform and host records have separate field owners

平台 Task 的验收/责任字段与 Run 的投影字段归平台写入;实际执行生命周期、checkpoint 与动作结果归执行方。按来源导入的历史 Run MUST 以观察 `origin` 标记创建,MUST NOT 自动取得投递、调度或审批权,活跃 Run MUST NOT 因注册或重连改变模式。独立运行 MUST NOT 读取平台表;本地部署来源、本地 ID 映射、事件序号与控制 ownership MUST 作为显式字段登记在 Run/enrollment 记录上。

#### Scenario: Imported observation gains no dispatch rights

- **WHEN** 一个本地宿主的历史 Run 按来源导入平台
- **THEN** 该 Run 以观察 origin 创建,任何新工作投递不因导入而路由给它

#### Scenario: Field owners are enforced by the boundary

- **WHEN** execution 域之外的代码直接写 Task/Run 表
- **THEN** 分层/边界测试将其识别为违规并失败

### Requirement: Standalone deployment registration is explicit and audited

独立部署的注册记录 MUST 登记宿主身份与信任根(校验其可解析性)、已安装版本与能力清单、是否显式选择接收受管新 Run 的选择位、操作者与审计字段(操作时间、准入结果)。模式转换(观察 → 受管)MUST 带操作者与准入记录;网络重连 MUST NOT 自动改变调度权。本能力仅覆盖模型与登记语义;注册、断连与重连的**真实执行路径**由受管注册 change(step6/7)交付。

#### Scenario: Registration validates trust root

- **WHEN** 登记一个宿主身份不可解析或信任根缺失的部署
- **THEN** 注册服务拒绝并保留拒绝记录

#### Scenario: Reconnect does not change scheduling rights

- **WHEN** 已注册部署的网络连接状态变化
- **THEN** 其受管新 Run 选择位与调度权保持不变,无操作者参与的转换被拒绝

### Requirement: ID mapping is single-writer and non-reusable

`conversation_id / task_id / run_id / backend_session_id` 的映射 MUST 由 execution 域唯一应用服务维护,业务方独立双写 MUST 被边界测试禁止。一个会话 MUST 能关联多个不同 Task;一个 Task MUST 能拥有多次 Run 尝试;同一后端签发域内 backend_session_id 与 Run 的绑定 MUST 唯一,既有绑定 MUST NOT 被复用到另一个 Run。

#### Scenario: Conversation maps to multiple tasks

- **WHEN** 同一会话先后发起两个不同业务任务
- **THEN** 两个 Task 均可经映射服务追溯到该会话,映射不互相覆盖

#### Scenario: Backend session binding is not reusable

- **WHEN** 尝试将某后端 session/turn 引用绑定到已绑定于其他 Run 的第二个 Run
- **THEN** 映射服务拒绝并报告冲突,既有绑定不变

#### Scenario: No dual writes outside the registry

- **WHEN** execution 域之外出现对映射数据的直接写调用
- **THEN** 边界测试将其识别为违规并失败

### Requirement: Legacy data maps without behavior change

存量 chat session MUST 通过惰性映射建立 conversation 关联(首次经登记服务触及时创建),MUST NOT 触发重新执行或改变既有执行链路行为;缺少发起者/负责人等治理数据的映射记录 MUST 登记为待治理,不自动赋平台身份。迁移 MUST 仅新增新表与可空列,约束收紧 MUST 在回填校验之后。

#### Scenario: Legacy session maps lazily

- **WHEN** 既有会话首次经过登记服务创建 Task
- **THEN** conversation 映射建立,会话历史不被重放,执行路径与迁移前一致

#### Scenario: Missing governance data stays pending

- **WHEN** 某存量映射缺少可解析的发起者治理数据
- **THEN** 记录登记为待治理,映射本身成功,不自动生成身份映射
