# Spec Delta

## MODIFIED Requirements

### Requirement: Cold start without control plane

宿主 MUST 仅凭 profile 目录(manifest、宿主配置、身份信任材料)完成启动并服务请求;MUST NOT 要求管理平台地址、平台管理数据库或完整 `hecate` 主应用的安装;控制面地址未配置或不可达时,只读执行 MUST 仍然完成并留下本地证据。宿主进程 MUST NOT 因控制面缺席而进入降级自授权——本地授权来自客户配置的信任材料,语义与在线无关。受管 profile 是可选叠加:配置了平台地址时宿主按 `managed-runner-enrollment` 能力接入(注册、租约、投递、投影),未配置时行为与本要求所述独立模式完全一致;受管连接的存在 MUST NOT 成为独立执行或本地证据留存的前提。

#### Scenario: Clean install cold start completes a read-only run

- **WHEN** 在干净 venv 中仅安装 hecate-runner 及其声明的依赖,以无控制面配置的 profile 启动宿主并提交一个只读执行
- **THEN** 执行完成,响应携带结果引用,本地证据记录该次执行,全程无主应用代码与仓库源码路径参与

#### Scenario: Missing profile artifacts fail fast

- **WHEN** profile 缺少 manifest、信任材料或配置文件,或 manifest 摘要不匹配
- **THEN** 宿主启动失败并给出指明缺失项的错误,不提供任何服务入口

#### Scenario: 受管地址未配置不影响独立模式

- **WHEN** profile 未配置控制面地址,宿主执行只读任务并写本地证据
- **THEN** 行为与未安装受管能力时一致,无注册/心跳/上传尝试,无因通道缺席的失败

## ADDED Requirements

### Requirement: 受管通道为可选叠加且凭据服务端验证

宿主的控制面客户端 SHALL 仅在配置了平台地址与凭据引用时激活;通道认证使用部署域签发的短期凭据,凭据经平台侧验证——客户端自报的身份、角色或作用域声明 MUST NOT 产生任何权限。通道失败(注册被拒、心跳超时、租约过期)MUST NOT 影响独立模式下已授权的本地执行与证据留存语义。

#### Scenario: 自报身份不产生权限

- **WHEN** 宿主客户端在请求中携带自报的管理员身份或角色声明
- **THEN** 平台按凭据验证结果与 enrollment 记录裁决,自报字段被忽略
