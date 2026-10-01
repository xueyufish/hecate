# Proposal

## Why

step3 契约主体（#198，`execution-backend-contract`）交付了七个权威 schema、stdlib 映射、`AgentExecutionBackend` 接口、Stub 与制品/Sandbox 契约,但方案 step3 还有四个契约项未关闭:HTTP/JSON 绑定、托管后端映射、工具契约、进程外身份与授权传递。没有传输绑定,外部实现者无法按同一语义接入,后续的非 Python 试点 change 也没有可实现的端点;没有身份传递约定,adapter 无法安全认证(方案明令"不接受客户端自报的管理员身份");没有工具契约,试点承诺的"无副作用工具回调"无契约可依。本 change 补全这四块,使 step3 只剩非 Python 试点一项。

探索阶段已定决策(2026-10-01 用户确认):错误响应用 RFC 9457 problem+json;`openapi-spec-validator` 进 dev extra;四块捆一个 change。

## What Changes

- **HTTP/JSON 绑定**:OpenAPI 3.1 文件落 `src/hecate/contracts/openapi/`,**$ref 复用既有七个 schema 不复制**;端点覆盖能力发现、提交、状态查询、事件游标读取、控制命令(取消)、artifact 列表;SSE 流式订阅为**可选视图**(同一事件序列的订阅视角,断线后回落游标查询,不作为唯一读取方式);绑定文档声明传输中立(其他传输由 adapter 映射同一语义)。
- **六码错误 ↔ HTTP 映射**:problem+json 载体,`type` 指向契约错误 URI;四个后端可返回错误——`unsupported`→501、`authorization_denied`→403、`budget_exhausted`→429(可选 `Retry-After`)、`version_conflict`→409;两个调用方合成态——`unreachable`(传输失败时调用方自行记录)与 `outcome_unknown`(run 状态 payload 中的状态值,非错误响应)**不是后端 HTTP 错误**,该分层写入绑定文档并由测试钉住。
- **身份与授权传递**:新 `security-claims.schema.json` 定义声明集的名称与约束(`iss`/`aud`=后端服务身份/`sub`=调用方工作负载身份/租户作用域/委派链引用/短时效);样本携带声明集合而非真实 JWT(密码学验证归 step7 adapter);OpenAPI `securitySchemes` 提供 bearer 与 mutualTLS 两个 profile;回调认证(后端→平台)单独成节;授权上下文沿用既有 `authorization` 引用类型;body 自报角色必须被忽略——规范条文 + 负例样本。
- **工具声明契约**:新 `tool.schema.json`:输入/输出 schema 引用、版本、副作用类别**原样采纳**内部 `SideEffectClass` 五值(`readonly`/`idempotent_write`/`non_idempotent_write`/`external_side_effect`/`unknown`);四态失败分层——参数验证失败=提交前 schema 拒绝、业务拒绝=工具**结果**而非错误(Run 继续)、系统故障=工具级错误事件、远端结果未知=复用 `outcome_unknown` 语义并按幂等 ID 对账(与 G2 四态恢复同构);工具候选/实际调用作为可观察事实关联同一 Run(供 step10 诊断,不暴露隐藏推理)。
- **托管后端映射**:`contracts/` 内映射语义文档(平台 task↔供应商 session、run↔turn、subagent 事件归命名空间化 Run 内部事件、绝不自动成为 Team 成员);`errors.schema.json` 的 `reconciliation.strategy` 增加 `query_by_vendor_session`;提交响应丢失按供应商 ID/幂等能力对账、无法对账标未知。
- **依赖**:`openapi-spec-validator` 加入 dev extra,契约文件结构合法性由专业校验器把关并接入测试。
- 全部延续 0.x 草案与按能力独立版本化,不冻结(冻结在 step8);样本/映射/测试延续三方互检模式;`contracts/` 与 `execution/` 继续受纯度探针看守。

**不做**:不实现任何真实网络服务或 adapter(非 Python 试点属后续 change);不做密码学/令牌签发验证(step7);不修改内部 `ToolRegistry` 产品代码(契约只定义跨后端声明格式);不做托管服务的真实验证(step8)。

## Capabilities

### New Capabilities

(无。)

### Modified Capabilities

- `execution-backend-contract`:新增四条 requirement——HTTP/JSON 绑定与错误映射(problem+json、四错误+两合成态分层)、进程外身份与授权传递(声明集、双 securityScheme profile、回调认证、自报身份忽略)、工具声明契约(副作用五值、四态失败分层、选择可观察)、托管后端映射语义(session/turn 映射、subagent 事件命名空间、按供应商会话对账)。

## Impact

- **新增**:`src/hecate/contracts/openapi/`(OpenAPI 3.1 文件)、`schemas/tool.schema.json`、`schemas/security-claims.schema.json`、`contracts/execution/` 对应映射、`tests/test_execution/samples/` 增 HTTP 请求/响应对与工具/声明集样本、托管映射文档。
- **修改**:`schemas/errors.schema.json`(`reconciliation.strategy` 枚举扩展)、既有映射与测试的增量、`pyproject.toml` dev extra。
- **不受影响**:产品源码、API 契约、数据库 schema;`RuntimePort` 方向与现有执行链不变。
- **后续**:本 change 合入后,非 Python 试点 change(语言/CI 两个待决点)可在 HTTP 绑定之上实施;step5a 不依赖本 change(可并行)。
