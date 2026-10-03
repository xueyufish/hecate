# standalone-runner-host Delta

## Purpose

独立执行宿主(hecate-runner)在无管理平台、无平台管理表、无仓库源码路径的环境中,从 profile 目录加载制品 manifest 并按 step3 执行后端 HTTP 绑定提供最小服务入口,使业务 App 能以技术预览档独立消费执行能力并留下可查询的本地证据。

## ADDED Requirements

### Requirement: Cold start without control plane

宿主 MUST 仅凭 profile 目录(manifest、宿主配置、身份信任材料)完成启动并服务请求;MUST NOT 要求管理平台地址、平台管理数据库或完整 `hecate` 主应用的安装;控制面地址未配置或不可达时,只读执行 MUST 仍然完成并留下本地证据。宿主进程 MUST NOT 因控制面缺席而进入降级自授权——本地授权来自客户配置的信任材料,语义与在线无关。

#### Scenario: Clean install cold start completes a read-only run

- **WHEN** 在干净 venv 中仅安装 hecate-runner 及其声明的依赖,以无控制面配置的 profile 启动宿主并提交一个只读执行
- **THEN** 执行完成,响应携带结果引用,本地证据记录该次执行,全程无主应用代码与仓库源码路径参与

#### Scenario: Missing profile artifacts fail fast

- **WHEN** profile 缺少 manifest、信任材料或配置文件,或 manifest 摘要不匹配
- **THEN** 宿主启动失败并给出指明缺失项的错误,不提供任何服务入口

### Requirement: Server-verified identity at the service entry

宿主 MUST 对每个服务请求做服务端身份校验:凭证对本地信任材料验证,principal、角色与数据域作用域由服务端映射解析。请求体或请求头中自报的角色、域或权限声明 MUST 被忽略;未认证或凭证不可信的请求 MUST 拒绝并写入拒绝证据。预览档角色集仅含 `read_only`;write 与 approval 角色不存在,不能通过请求获得。

#### Scenario: Forged role claim is ignored

- **WHEN** 持有效凭证的调用方在请求体中自报 `admin` 或其他越权角色
- **THEN** 服务端按信任材料解析出的 `read_only` 角色与域作用域执行,自报字段不产生任何权限变化

#### Scenario: Unknown credential rejected with evidence

- **WHEN** 请求携带不在信任材料中的凭证
- **THEN** 请求被拒绝,拒绝记录(含原因类别)可在本地证据中查询

### Requirement: Manifest-gated read-only preview profile

预览档宿主 MUST 在启动时校验制品 manifest:声明 `write` 或 `approval_required` 权限工具的 manifest MUST 使启动失败并指明违规工具;仅允许列表内的 `read` 工具可参与执行;工具参数 MUST 在分发前通过其声明的 schema 校验。后台自动重试、长任务恢复与持久任务 MUST 经能力声明机制报告为 `unsupported`,MUST NOT 以缺省或静默方式表达。

#### Scenario: Write tool in manifest fails startup

- **WHEN** profile 的 manifest 声明了一个 `write` 权限的工具
- **THEN** 宿主启动失败,错误指明该工具名与权限类别,不加载任何执行能力

#### Scenario: Invalid tool arguments rejected before dispatch

- **WHEN** 执行请求中的工具参数不符合工具声明的 schema
- **THEN** 分发被拒绝,错误与拒绝证据记录参数校验失败,业务 API 未被调用

### Requirement: Local evidence with no default upload

宿主 MUST 将每次执行与每次拒绝追加写入本地 append-only 证据存储,记录至少:时间、principal、动作或执行标识、outcome 分类(成功/拒绝类别/失败)与结果引用;证据 MUST 可经本地查询接口按 outcome 与 principal 过滤读取。宿主 MUST NOT 向配置之外的任何目标发送业务内容;除已配置的模型 endpoint 与工具指向的业务 API 外,默认零外发。

#### Scenario: Denial evidence is queryable locally

- **WHEN** 发生一次越权拒绝后按拒绝类别查询本地证据
- **THEN** 该次拒绝的记录可读取,含 principal、原因类别与时间

#### Scenario: No upload path exists by default

- **WHEN** 检查预览档宿主的配置与代码路径
- **THEN** 不存在未配置即启用的中心上传通道;证据仅落本地存储

### Requirement: Deterministic CI model and configurable endpoint

宿主默认 MUST 使用确定性 stub 模型,使 CI 场景无网络、无真实模型凭据可运行;配置模型 endpoint 时 MUST 经该 endpoint 调用并把模型来源记录进该次执行证据;使用真实 endpoint 的运行 MUST NOT 被表述为供应商或模型已认证。

#### Scenario: Stub-backed run is deterministic

- **WHEN** 以默认 stub 模型连续运行同一 CI 场景
- **THEN** 执行结果与证据中的业务断言一致,不发生外部模型调用

#### Scenario: Endpoint run labels its model source

- **WHEN** 配置模型 endpoint 并执行
- **THEN** 证据记录模型来源为配置 endpoint,而非 stub,且不携带任何认证性表述

### Requirement: Health and honest shutdown

宿主 MUST 提供健康端点,报告就绪状态与 profile 摘要(backend 类型、能力声明、模型来源);关停 MUST 拒绝新请求并如实报告在途执行状态;预览档 MUST NOT 在关停或崩溃时宣称状态可恢复——持久化保证按能力声明为 `unsupported`。

#### Scenario: Health reflects readiness and profile

- **WHEN** 宿主就绪后查询健康端点
- **THEN** 响应包含就绪状态、能力声明摘要与模型来源,与实际 profile 一致

#### Scenario: Shutdown reports in-flight honestly

- **WHEN** 关停请求到达时存在在途执行
- **THEN** 宿主拒绝新请求,报告在途执行的最终状态或显式不可恢复,不伪造持久化承诺
