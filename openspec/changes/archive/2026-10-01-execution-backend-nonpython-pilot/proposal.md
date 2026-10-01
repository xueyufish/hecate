# Proposal

## Why

step3 只剩最后一项:用一个真实非 Python 后端对执行契约做窄范围验证。当前契约(`execution-backend-contract` 0.x 草案)的全部证据来自 Python 侧自证——schema、Python 映射、`StubExecutionBackend` 与契约测试同构同源,对契约的共同误读可以全部一起错还保持全绿。方案明文要求"不能仅用 Stub 冻结接口",且 step3 验收的最后一款是"至少一个不依赖 Hecate Python 包的客户端能按标准样本解析请求、事件和错误"——这只能由真实异构实现来关闭。同时该试点是 step8 异构接入认证的前置探路:现在暴露契约歧义,比冻结后返工便宜得多。

## What Changes

- 新增 TypeScript 试点后端 `pilots/execution-backend-ts/`(独立 npm 包,不进 uv workspace、不随产品发布):按已落地的 OpenAPI 绑定实现七个路径的窄切片——能力发现(三轴声明、`pause=unsupported`)、幂等提交、确定性迷你 Run 事件流(游标分页、断线续读、缺口标记)、取消(`REQUESTED`≠`APPLIED` 两态)、无副作用 echo 工具回调(含业务拒绝变体:tool RESULT 事件、Run 继续)、最小身份校验(claims 包络在场、自报角色拒绝)。绑定 loopback、无生产凭据、无受保护写入。
- **A 侧自证**:TS 侧 vitest + ajv 测试框架,加载仓库标准样本(47 个 JSON)对已发布 schema 校验,并对真实 HTTP wire 行为断言——证明零 Hecate Python 依赖即可实现并验证契约(直接满足验收字面)。
- **B 侧互操作**:`tests/test_execution/` 新增测试专用 HTTP transport(包装成 `AgentExecutionBackend` 同一接口),把既有契约测试参数化为 `stub | live` 两态;session 级 fixture 起临时端口 node 进程,node 不存在时 `skipif` 带理由跳过(非永久 skip:A 侧是常驻证明,B 是有 node 即跑的加餐)。transport 不进 `src/hecate/`,step5d 需要正式版时再晋升。
- **契约修订闭环**:试点(A 或 B 任一侧)发现的真实契约歧义 → 本 change 内修订 schema/样本并记录理由;版本保持 0.x 不冻结;扩展走 vendor 命名空间。
- 交付运行证据报告 `docs/refactor/execution-backend-pilot-report.md`(A/B 两侧输出分开记录);勾选方案 step3 最后一项,step3 全部 20 项关闭。
- CI 不新增 node job(全仓目前无 node CI);证据以本地运行记录为准,step8 冻结时再议是否接入。

## Capabilities

### New Capabilities

- `execution-backend-pilot`:试点资产自身的治理要求——非 Python 实现不得依赖 Hecate Python 包、A 侧样本/schema 自证、B 侧与 Stub 共享同一套契约断言且 node 缺席时干净跳过、试点发现必须以记录理由的方式回流 0.x 草案、隔离边界(loopback/无凭据/无副作用工具)。

### Modified Capabilities

(无——`execution-backend-contract` 主 spec 不因试点改变;试点若发现歧义,修订随本 change 落地时按其既有 requirement 的字段扩展规则进行,不新增 requirement。)

## Impact

- **新增**:`pilots/execution-backend-ts/`(TS 源码、package.json、tsconfig、README 声明草稿性质)、`tests/test_execution/live_http.py`(测试专用 transport)与 conftest live fixture、`docs/refactor/execution-backend-pilot-report.md`。
- **修改**:`tests/test_execution/test_backend_contract.py`(引入 backend 工厂参数化,断言不变);如试点发现歧义,`src/hecate/contracts/schemas/*` 与 `tests/test_execution/samples/*` 的对应修订;方案 step3 最后一项勾选。
- **不受影响**:产品源码运行时行为、`src/hecate/execution/` 包内容、CI workflow、uv workspace 成员。
- **风险登记**:TS 工具链为本仓首次引入 Python 之外的测试运行时;node 版本漂移以 engines 钉住,`dist/` 不入库。
