# 执行契约非 Python 试点验证报告(execution-backend-nonpython-pilot)

> 性质:step3 最后一项的窄范围验证证据;不构成任何后端、供应商或部署组合的接入认证。契约(`execution-backend-contract` / `sandbox-provider-contract`)保持 0.x 草案,冻结条件由 step8 决定。

## 验证拓扑

按 change 设计(D1):**A 侧**为 TypeScript 试点后端(`pilots/execution-backend-ts/`)及其 vitest+ajv 自证,满足验收字面("不依赖 Hecate Python 包的客户端按标准样本解析请求、事件和错误");**B 侧**为 pytest 测试专用 transport(`tests/test_execution/live_http.py`),把既有契约断言不加改动地同时作用于 Stub 与 live 两态,堵住"实现者与验证者同源导致共同误读"的盲点。

## A 侧(非 Python 自证)

- 环境:node v24.18.0,npm 11.16.0,TypeScript 5.x(target ES2023,NodeNext),ajv v8(draft 2020-12),vitest 2.1.9;Windows 11 本机。
- 复现:`cd pilots/execution-backend-ts && npm install && npm test`
- 结果:**31 passed**(independence 2、wire-semantics 17、samples-schema 12)。
- 覆盖:
  - 全部 47 个标准样本按主题对已发布 schema 校验(经 ajv 按 `$id` 注册解析跨文件 `$ref`);HTTP 样本对的请求体对 execution-request、202/200 响应体对各自响应 schema;negative 样本对(把 outcome_unknown/unreachable 当 HTTP 错误)被断言为禁止形状。
  - wire 硬语义:幂等两态(同键同内容回放原回执/同键异内容 409 `idempotency_key_content_mismatch`)、契约版本窗口(窗外 409)、游标分页与断线续读、显式 gap 标记(洞 2..2,标记在序号 3,遵循样本约定)、取消 REQUESTED≠APPLIED(两态在 wire 可区分)、业务拒绝为 tool RESULT 事件且 Run 继续、`events:stream` 对不支持能力返回 501 结构化 problem、身份校验(claims 包络+audience;自报角色字段被忽略)。
  - 独立性:运行时依赖仅 `ajv`;src 无任何 `hecate` 导入或 Python 子进程;schema/样本按相对路径引用仓库源文件(临时移除 security-claims.schema.json 后 A 侧立即失败,已还原)。

## B 侧(跨语言互操作)

- 环境:同一台机器,Python 3.14 + 本仓 `.venv`;pytest 经 `tests/test_execution/conftest.py::live_backend` session fixture 拉起 `node dist/server.js --port 0`。
- 复现:`python -m pytest tests/test_execution/ -q`(无 node 时 live 用例带理由跳过,非永久 skip)
- 结果:**182 passed + 12 skipped**(skip 为既有的单 schema HTTP 样本治理跳过,非本次引入)。
- 覆盖:
  - `test_backend_contract.py` 四项共享断言参数化为 `stub | live`:能力声明(常驻 UNSUPPORTED 负例)、pause 结构化拒绝、幂等提交、幂等冲突;断言体未改动(仅两处字面 `"stub"` 泛化为 fixture 提供的签发域,语义不变);Stub 注入类用例(append_event/inject_gap/settle_cancel/unreachable_submit)保持 stub-only,live 等价物见下。
  - `test_live_pilot.py` 七项 live 语义:取消两态经 wire 走通、simulate_gap 的 gap 标记、业务拒绝后 Run 继续、stream 501 结构化 problem、版本窗口 409、未知 run 的 binding 404、backend-issued artifacts。

## 试点发现与处置

| 编号 | 来源侧 | 歧义点 | 处置 |
|---|---|---|---|
| F1 | A(设计时)+B | 绑定/OpenAPI 未定义未知 run 引用的响应(错误码集合中没有 not_found) | 试点以 binding 级 404(`type=.../binding/run_not_found`,无契约码)应答;双方测试钉住。建议 step8 冻结时在 OpenAPI 增补该响应定义 |
| F2 | A(设计时) | 认证失败的错误形状在无 run 上下文的路由(如 `/capabilities`)上未定义,而 errors.schema 要求每个契约错误必带 `request_ref`;run 作用域路由有路径引用可用 | 试点分层:run 作用域 403 = 契约 problem(`authorization_denied` + 路径 run_ref);无上下文 403 = binding 级 problem(`.../binding/authorization_required`,无契约码)。建议 step8 把两种形状写入 OpenAPI |
| F3 | B | 互操作首跑即抓到试点服务端自身两个契约错误体缺 `request_ref`(version_conflict、stream-501)——schema 必填字段违反,纯 Python 自证无法发现(服务端与断言同源) | 已修复:错误体补齐 body 内 run_ref / 路径 run_ref;transport 对缺失 `request_ref` 的契约错误保持严格(拒绝映射,报 binding 违规)。作为 B 侧价值的直接证据记录 |

无 schema 语义修订:两项发现均在 OpenAPI 绑定层(响应定义缺口),契约 schema 本体经受住了异构实现检验;0.x 版本未动,无冻结声明。试点专属扩展仅 `/pilot-ns/` 命名空间下的确定性 checkpoint 路由,不授予平台权限。

## 边界与不承诺

本报告证明:契约可被零 Hecate Python 依赖地实现与验证;两侧对 wire 语义的理解一致。本报告不证明:真实模型调用、受保护写入、生产凭据、任何供应商/托管后端行为、生产性能或可用性。这些属于后续 step(5/6/7/8)的验收范围。
