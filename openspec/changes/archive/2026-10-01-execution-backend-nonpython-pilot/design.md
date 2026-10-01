# Design

## Context

契约侧现状(探索阶段核实):`src/hecate/contracts/` 已有 schema 文件 + OpenAPI 绑定(七个路径:`/capabilities`、`/runs`、`/runs/{run_ref}`、`/runs/{run_ref}/events`、`.../events:stream`、`.../cancel`、`.../artifacts`)、stdlib Python 映射(10 个模块)、47 个标准样本;`tests/test_execution/test_backend_contract.py` 直接实例化 `StubExecutionBackend`(尚无参数化夹具),断言覆盖 UNSUPPORTED 负例、幂等、游标、取消两态、版本窗口。工具链:`web/` 为 npm + TS + vitest,但全仓 CI 无任何 node job。

用户已拍板:验证拓扑选 **A+B**(探索会话定案);其余默认(TS、`pilots/` 位置、本地证据先行、测试专用 transport)未被否决,视为确认。

## Goals / Non-Goals

**Goals**

- 关闭 step3 最后一项与验收最后一款("不依赖 Hecate Python 包的客户端按标准样本解析请求、事件和错误")。
- 用真实异构差异检验 0.x 契约,发现歧义即修订并记录理由。
- B 侧与 Stub 共享同一套断言,使"互认"是测试事实而非口头声明。

**Non-Goals**

- 不实现 LLM 调用、真实模型、生产凭据、受保护写入;不承诺试点可用于生产或代表任何供应商后端。
- 不新增 CI node job;不改 CI workflow。
- 不在 `src/hecate/` 新增任何生产代码;transport 晋升留给 step5d/step8。
- 不冻结契约(仍 0.x);不建 SDK 或多语言发布物(step18)。

## Decisions

### D1: 验证拓扑 A+B,两侧互补

A(TS 自证)满足验收字面并暴露"纯独立实现"视角;B(Python live 参数)堵住"同一套代码既实现又验证、共同误读一起错"的盲点——跨语言互认。两侧断言刻意不复制:B 断言=既有契约测试全集(零改动),A 断言=样本校验 + wire 硬语义子集。重叠即互认,差异即覆盖。

*备选*:仅 A(最小满足验收)或仅 B(互认但不满足字面)。均否决——证据强度不对等,成本差异却很小。

### D2: TypeScript,tsc 直出 dist,依赖压到三个

运行时 `node:http`(无框架);schema 校验 ajv(v8,支持 2020-12);测试 vitest(与 web 同栈)。构建 `tsc` 直出 `dist/`(无 bundler),B 侧 fixture 直接 `node dist/server.js`。`package.json` 以 `engines` 钉 node 主版本;`dist/`、`node_modules/` 进 `.gitignore`;schema/样本经相对路径引用仓库文件,不复制(与 OpenAPI `$id` 引用同一原则——试点改契约必须改到源头)。

*备选*:Go(单一二进制、CI 轻,但团队新语言且 JSON Schema 校验生态弱)、node:test(零依赖但放弃 vitest 生态)。均否决。

### D3: 位置 `pilots/execution-backend-ts/`,仓库身份是"验证资产"

不进 `packages/`(uv workspace 是产品包)、不用 `examples/`(暗示 API 稳定,契约尚为 0.x)、不进 `tests/`(非 Python 测试)。独立 `package.json` + lockfile;README 首段声明草稿性质与 step8 冻结条件。仓库根 `.gitignore` 增补试点构建产物条目(仅该目录模式,不影响其他)。

### D4: B 侧 transport 为测试专用,接口同构包装

`tests/test_execution/live_http.py`:实现与 `StubExecutionBackend` 相同的 `AgentExecutionBackend` 方法面——`submit()` → `POST /runs`(problem+json → `ExecutionBackendError`/`UnsupportedCapabilityError` 反解)、`read_events()` → 游标 GET、`request_cancel()` → cancel POST、`describe_capabilities()` → discovery GET。既有测试文件改为 backend 工厂参数化(`stub` | `live`),断言体零改动。conftest 增 session 级 fixture:临时端口起进程、健康探测、终结回收;`shutil.which("node")` 为空即 `skipif` 并写理由。

*备选*:transport 放 `src/hecate/execution/` 作为首个正式 HTTP 客户端。否决——那是对未冻结契约的生产承诺,且 runtime-pluggability 规则下它作为扩展点需要更重的论证;step5d 有真实消费者时再晋升,届时它已被两侧淬炼。

### D5: 试点后端的功能切片(确定性、无模型)

提交即驱动一个确定性状态机:`run_started → tool_call(echo) → tool_result → run_completed`,事件持久在内存按序号存储,游标分页返回、断线按游标记续读、模拟缺口出 gap marker;取消为 cooperative:立即回 `REQUESTED` 回执,执行到下一个检查点才发 `APPLIED` 事件;echo 工具带业务拒绝变体(如参数超出白名单)——按契约失败分层,业务拒绝是 tool RESULT 事件且 Run 继续。身份最小化:校验 security-claims 包络在场与 audience、自报角色字段落 extra 且不进授权结构(负例照 2a 的行为测试)。

### D6: 证据报告与契约修订闭环

报告 `docs/refactor/execution-backend-pilot-report.md`(该目录已被 git 跟踪):A/B 两侧输出、环境版本、复现命令、发现清单及处置(修订了什么/为什么/保持 0.x)。发现按"来源侧(A/B)、歧义点、修订"三列登记;无发现的样本面也要记录覆盖范围,防止"没测就说没问题"。

## Risks / Trade-offs

- [A 侧自证盲区(共同误读)] → B 侧同一断言集互认;修订必须双侧同步改样本。
- [node 版本/平台漂移(开发机 Windows,CI 无 node)] → `engines` 钉版本;证据报告记录环境;B 侧 skipif 带理由,不伪装覆盖。
- [ajv 严格模式与 schema 2020-12 细节差异] → A 侧加载的 schema 与 Python 侧 `jsonschema` 校验的是同一批文件;差异本身即契约发现,走 D6 闭环。
- [试点腐化(无人跑)] → 报告含复现命令;step8 冻结时决定是否升格为 CI job;在此之前试点身份是验证资产而非门禁。
- [测试文件参数化重构动到既有断言] → 断言体零改动是验收条件;重构 PR 中 diff 应只见夹具与参数,不见断言变化。

## Migration Plan

纯新增资产 + 测试目录内重构:合入即生效,回退 = revert。无产品行为变化、无 schema/数据迁移;若试点触发 schema 修订,按既有三方互检测试(样本×schema×映射)同步更新,版本保持 0.x。

## Open Questions

(无——语言、位置、拓扑、CI、transport 位置均已在探索阶段定案;唯一留到实施期的事实问题是你机器的 node 可用版本,由任务 1.1 的环境检查步骤确认。)
