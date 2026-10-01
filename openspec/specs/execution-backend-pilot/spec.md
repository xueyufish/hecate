# execution-backend-pilot Specification

## Purpose

定义执行契约非 Python 试点资产的治理要求:一个真实 TypeScript 后端及其自证测试框架证明契约可被零 Hecate Python 依赖地实现与验证,一个共享断言的 Python live 参数证明跨语言互认,二者发现的歧义以记录理由的方式回流 0.x 草案,使 step3 的"不能仅用 Stub 冻结接口"约束被可审计地满足。

## Requirements

### Requirement: Pilot backend implements the contract without Hecate Python

试点后端 MUST 以非 Python 语言实现,其源码、构建与测试 MUST NOT 依赖任何 Hecate Python 包(hecate 及 workspace 包);MUST 按已发布的 OpenAPI 绑定暴露能力发现、提交、状态、事件游标、取消与产物引用路径。能力声明 MUST 携带三轴归属(harness/environment/tool execution),且至少一个能力(如 pause)声明为 `unsupported` 并在对应该能力的请求上返回结构化 UNSUPPORTED 错误(problem+json),MUST NOT 伪造成功。服务器 MUST 仅绑定 loopback,MUST NOT 配置生产凭据或执行受保护写入;工具回调 MUST 限于无副作用类别。

#### Scenario: Discovery works over HTTP without Hecate Python

- **WHEN** 客户端向试点后端请求能力发现
- **THEN** 返回符合 capabilities schema 的声明,含三轴归属与验证条目,且实现与验证过程中未导入任何 Hecate Python 包

#### Scenario: Unsupported capability is a structured error on the wire

- **WHEN** 对声明 `pause=unsupported` 的试点后端提交暂停类请求
- **THEN** 收到 problem+json 的结构化 UNSUPPORTED 错误响应(如 501),错误体携带契约错误码与请求引用,不存在伪造的成功响应

#### Scenario: Pilot stays isolated

- **WHEN** 检查试点后端的网络绑定、配置与工具清单
- **THEN** 仅绑定 loopback,无生产凭据配置,工具回调均为无副作用类别

### Requirement: A-side self-verification parses standard samples

试点仓 MUST 自带非 Python 测试框架:以独立于 Hecate 的 JSON Schema 校验器加载已发布 schema,对仓库标准样本逐一校验,并对真实 HTTP wire 行为断言——同幂等键同内容返回同一回执、同键异内容返回版本冲突(指明 `idempotency_key_content_mismatch`)、事件游标可续读且缺口为显式标记、取消请求态与生效态在 wire 上可区分、业务拒绝表现为 tool RESULT 事件且 Run 继续。该框架的运行 MUST NOT 调用 Hecate Python 代码。

#### Scenario: Samples validate in the non-Python harness

- **WHEN** 运行试点自证框架
- **THEN** 标准样本对已发布 schema 校验通过,样本与 wire 断言全部执行且不经过任何 Hecate Python 代码路径

#### Scenario: Wire behavior asserts the hard semantics

- **WHEN** 试点自证框架执行幂等、游标续读、取消两态与业务拒绝用例
- **THEN** 各用例对真实 HTTP 响应断言通过;同键异内容得到版本冲突而非第二次执行

### Requirement: B-side live parameter shares the contract assertions

Python 契约测试 MUST 提供测试专用的 HTTP transport,把对试点后端的调用包装为与 Stub 相同的后端接口,使既有契约断言不改动地同时作用于 stub 与 live 两态。该 transport MUST 只存在于测试目录,MUST NOT 进入 `src/hecate/` 的任何包。live 态由 session 级 fixture 在临时端口拉起试点进程;运行环境无 node 时 MUST 以写明理由的条件跳过代替失败或永久跳过。

#### Scenario: Same assertions pass through HTTP

- **WHEN** 在有 node 的环境以 live 参数运行契约测试
- **THEN** 与 Stub 相同的断言集全部通过,包括 UNSUPPORTED 负例、幂等提交、游标续读与取消两态

#### Scenario: Missing node skips with a reason

- **WHEN** 运行环境不存在可用的 node 可执行文件
- **THEN** live 参数用例以显式理由跳过,Stub 参数与全部其余契约测试不受影响

#### Scenario: Transport stays test-only

- **WHEN** 检查 `src/hecate/` 包内容
- **THEN** 不存在试点 HTTP transport 的实现或导入;transport 仅可从测试目录导入

### Requirement: Findings revise the draft with recorded rationale

试点任一侧发现契约歧义或缺陷时,修订 MUST 在同一 change 内完成:schema/样本的修改 MUST 附理由(发现来源、误解点、修订方式),契约版本 MUST 保持 0.x 草案,试点专属扩展 MUST 使用 vendor 命名空间且 MUST NOT 授予平台权限。运行证据 MUST 以报告形式交付,A 侧(非 Python 自证)与 B 侧(互操作)输出分开记录,并包含复现命令。

#### Scenario: Draft status is preserved through revisions

- **WHEN** 试点发现导致 schema 修订
- **THEN** 修订后的 `$id` 仍为 0.x,修订理由记录在 change 工件中,无冻结声明

#### Scenario: Evidence report separates both sides

- **WHEN** 查看运行证据报告
- **THEN** A 侧与 B 侧的运行输出、环境(node/Python 版本)与复现命令分别可查,报告不宣称超出窄范围验证的任何认证
