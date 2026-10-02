# Spec Delta

## ADDED Requirements

### Requirement: Run validates and freezes its execution binding

新建平台 Run MUST 校验身份链的部署引用及 principal 与真实部署一致且活跃，委派 MUST 是授权引用，受众 MUST 非空。Run MUST 固化执行版本配置、部署能力与控制 ownership，后续部署修改 MUST NOT 改变历史快照；重试 MUST 创建新尝试并在同一 Task 上串行分配尝试号。

#### Scenario: Deployment mismatch fails without creating a run
- **WHEN** 身份链的部署或 principal 不属于所选 Deployment
- **THEN** Run 创建拒绝且不产生尝试记录

#### Scenario: Historical run survives deployment changes
- **WHEN** 已创建 Run 后部署能力或配置引用改变
- **THEN** Run 的固化快照不变

### Requirement: Mappings are validated and permanently reserved

conversation 绑定 MUST 引用真实且同 workspace 的 conversation；旧 session MUST 经其 conversation 关系解析，不能把 session ID 当 conversation ID。backend session/turn 标识 MUST 在签发域内唯一且不可覆盖，软删除 MUST NOT 释放标识。历史导入 MUST 按部署和来源幂等，不同来源同名本地 ID MUST 可区分，重复来源不得映射到其他任务。

#### Scenario: Existing backend session cannot be overwritten
- **WHEN** 为已绑定会话的 Run 再绑定另一个会话，或将软删除 Run 的会话绑定到其他 Run
- **THEN** 拒绝操作，既有绑定保留

#### Scenario: Local import is idempotent and scoped
- **WHEN** 同一部署/签发域/本地 ID 重复导入同一 Task
- **THEN** 返回原 Run，不新增尝试；另一来源同名 ID 不冲突

### Requirement: Managed admission requires trusted resolution

宿主 named references MUST 仅作为待核验声明；接收受管新 Run MUST 同时具备管理员操作、已解析宿主和信任根、已登记版本/能力及成功准入。缺少可信解析器 MUST 拒绝 admitted，拒绝/待准入记录 MUST NOT 启用 managed_new_runs。拒绝日志 MUST 保留在调用方事务，登记服务 MUST NOT 擅自提交其他业务写入。

#### Scenario: Rejected admission cannot enable managed work
- **WHEN** 拒绝准入却要求 managed_new_runs 为 true
- **THEN** 操作拒绝并留审计，选择位不改变

#### Scenario: Named but unknown host cannot be admitted
- **WHEN** 宿主提供非空引用但可信解析器缺失或解析失败
- **THEN** 不授予受管准入
