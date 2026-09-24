# memory-policy Specification(Delta)

## Purpose

定义记忆策略对象:workspace 级策略与 agent 级覆盖的数据模型、platform→workspace→agent 生效链与收敛规则、策略字段语义,以及在平台各执行点(工具面、整合触发、flush、TTL/淘汰、namespace 共享)的落实方式。策略把记忆治理旋钮从平台环境变量解放到 workspace/agent 粒度。

## ADDED Requirements

### Requirement: 策略对象模型

系统 SHALL 提供记忆策略对象,承载两类 scope:workspace 级(每个 workspace 至多一条)与 agent 级(每个 `(workspace, agent)` 至多一条)。策略字段 SHALL 涵盖:

- 记忆工具面:允许该 agent 使用的记忆工具子集;
- 触发阈值:flush 登记与整合调度的启用及阈值参数(如 idle 时长、压力阈值);
- 生命周期参数:分层 TTL、容量上限、淘汰预算与保护窗口、晋升门槛与开关;
- 共享上限:该 scope 允许的最宽 namespace 共享级别(actor / team / workspace);
- 预算:检索与整合的 per-scope 预算参数。

策略对象 SHALL 有独立的创建/读取/更新/删除管理面(经治理 REST API,见 `memory-api` capability)。

#### Scenario: 创建 workspace 级策略

- **WHEN** workspace 管理者创建该 workspace 的记忆策略并配置分层 TTL 与工具子集
- **THEN** 策略保存成功并即时参与生效链解析,无需重启

#### Scenario: 创建 agent 级覆盖

- **WHEN** 管理者为某 agent 创建覆盖策略,仅覆盖工具子集字段
- **THEN** 该 agent 的解析结果中工具面来自 agent 级覆盖,其余字段回落 workspace 级

### Requirement: 生效链与收敛规则

策略解析 SHALL 按固定顺序进行:平台环境变量默认值 → workspace 级策略 → agent 级策略;无任何策略时解析结果 SHALL 等于平台默认值(行为与未部署策略对象时一致)。收敛规则 SHALL 区分两类字段:

- 权限面字段(记忆工具子集、namespace 共享上限):后级 SHALL 只能收窄,不得扩权——工具子集只能是其父级子集的子集;共享级别只能收紧为相同或更窄;
- 数值面字段(TTL、容量、预算、阈值):后级可自由配置,但 SHALL 受平台硬上限约束,超过上限的配置 SHALL 被管理面拒绝。

#### Scenario: agent 收窄工具

- **WHEN** workspace 级策略允许全部记忆工具,agent 级覆盖仅允许 `memory_search` 与 `memory_add`
- **THEN** 该 agent 的生效工具面为 `memory_search` 与 `memory_add`

#### Scenario: 越权扩权被拒

- **WHEN** workspace 级策略的共享上限为 team,某 agent 级覆盖尝试将共享上限设为 workspace
- **THEN** 该配置被管理面拒绝并返回明确错误

#### Scenario: 空策略退化平台默认

- **WHEN** 某 workspace 及其 agent 均无策略记录
- **THEN** 该 scope 的全部记忆行为(工具面、触发、TTL、淘汰)与平台环境变量默认值一致

### Requirement: 策略执行点

生效策略 SHALL 在以下执行点被一致应用:

- 工具 seeding:记忆工具的注册集合为平台 flag 允许集与生效策略工具子集的交集(见 `agent-memory-tools` capability);
- 整合与 flush:trigger bus 的调度参数(idle 时长、优先级、flush 启用)按生效策略解析;
- 生命周期:TTL、容量、淘汰预算、保护窗口、晋升门槛全部取自生效策略;
- namespace 共享:跨 namespace 共享请求(含晋升)的宽度 SHALL NOT 超过生效策略的共享上限;
- 治理 API:管理面读写同样按生效策略校验合法性。

#### Scenario: 执行点一致应用

- **WHEN** 某 agent 的生效策略收窄了工具面并配置了更短的 L3 episodic TTL
- **THEN** 该 agent 的工具 seeding 与其 scope 的 TTL 清扫均按生效值执行,与其他未配置 agent 互不影响

#### Scenario: 共享请求不越过策略上限

- **WHEN** 某操作试图将一条 actor 级记忆共享到 workspace 级,而生效策略共享上限为 team
- **THEN** 该操作被拒绝

### Requirement: 策略变更校验与生效

策略管理面 SHALL 校验:未知工具名、超过平台硬上限的数值、越权的权限面配置 SHALL 被拒绝并返回可读错误。策略变更 SHALL 即时生效(下一轮解析即采用),SHALL NOT 要求服务重启;策略的创建/更新/删除 SHALL 记入审计日志。

#### Scenario: 非法配置被拒

- **WHEN** 管理者提交包含未知工具名或超上限 TTL 的策略
- **THEN** 保存被拒绝,错误信息指明非法字段与允许范围

#### Scenario: 变更即时生效

- **WHEN** 管理者修改某 workspace 的 TTL 配置
- **THEN** 后续解析与清扫即采用新值,期间无需重启任何服务实例
