# Design

## Context

- change1(`execution-backend-contract`,#198)已交付:七个权威 schema(`src/hecate/contracts/schemas/`,各带 `$id` 0.x 版本)、stdlib dataclass 映射(`contracts/execution/`)、`AgentExecutionBackend` 六方法 + 可选能力三级声明、`StubExecutionBackend`(pause 永久 unsupported 常驻负例)、制品 manifest 校验、Sandbox 契约;三方互检测试(样本×schema×映射)与 AST 纯度探针(全 import 位置 + 逃逸负例)。
- README 已固定编码规则:引用(`kind`/`issuer_domain`/`id`)、RFC3339 时间、小写枚举、缺省表达 null、未知字段容忍(`extra` map 保往返)、版本协商(`contract_version` → `version_conflict`)、幂等键作用域与内容冲突、trace 关联。
- 内部工具副作用词汇:`runtime/tool_side_effects.py` `SideEffectClass` = `readonly`/`idempotent_write`/`non_idempotent_write`/`external_side_effect`/`unknown`;G2 恢复四态(`never_started`/`claimed`/`outcome_unknown`/`store_unavailable`)已在 ToolWorker 落地。
- `errors.schema.json` 已有 `reconciliation.strategy`(`query_by_idempotency_key`/`query_by_run_ref`)。
- 用户已确认决策(2026-10-01):problem+json 映射按推荐表;`openapi-spec-validator` 进 dev extra;四块一个 change。

## Goals / Non-Goals

**Goals**:交付 step3 剩余四个契约项(HTTP 绑定、身份传递、工具契约、托管映射),使非 Python 试点 change 有可实现的端点、可认证的约定、可回调的工具声明;全部延续 0.x 草案与三方互检/纯度探针机制。

**Non-Goals**:不实现 HTTP 服务或 adapter(试点 change);不做密码学/令牌签发(step7);不改内部 `ToolRegistry`/ToolWorker 产品代码(契约只定义跨后端声明,内部词汇仅被采纳引用);不做托管服务真实验证(step8);不冻结契约(冻结在 step8)。

## Decisions

### D1: OpenAPI 3.1 文件 $ref 复用 schema,不做代码生成

`src/hecate/contracts/openapi/execution-backend.http.v0_1.yaml` 单文件,components 通过 `$ref`(相对路径/契约 URI)指向 `schemas/` 七文件与新增文件,不复制任何字段定义;OpenAPI 与 JSON Schema 之间只有一份真相。Python 侧**不生成**客户端/服务端代码——绑定是给外部实现者(含非 Python 试点)读的,Python 映射已存在且继续手工维护。验证:`openapi-spec-validator`(新 dev 依赖)校验文件结构;HTTP 层样本(请求/响应对 JSON)按 OpenAPI media schema 校验,延续三方互检为"schema × 样本 × 映射 × OpenAPI"。

*备选*:OpenAPI 内联定义再生成 schema。否决——两套真相必然漂移,且生成链引入构建依赖。

### D2: 错误映射 = problem+json,六码分"可返回/调用方合成"两层

固定映射:可返回错误 `unsupported`→501、`authorization_denied`→403、`budget_exhausted`→429(可附 `Retry-After`)、`version_conflict`→409,响应用 RFC 9457 problem+json,`type` 为契约错误 URI(如 `.../errors#unsupported`),`detail_ns`/`request_ref`/`reconciliation` 映射到 problem 的 extension 成员;`unreachable` 与 `outcome_unknown` 永不作为后端 HTTP 错误出现——前者是调用方对传输失败的记录,后者是状态值(run status payload 与提交对账后的 receipt)。绑定文档单列"调用方合成态"一节;测试各配一个负例样本(后端返回二者作为错误码 → 样本非法)。

*备选*:六码全部映射 HTTP 状态。否决——`unreachable` 根本到不了后端,`outcome_unknown` 作为 5xx 会把"状态"误表达为"故障"。

### D3: 端点面与 SSE 可选视图

`GET /capabilities`、`POST /runs`(提交,携带幂等键头 `Idempotency-Key`)、`GET /runs/{run_ref}`、`GET /runs/{run_ref}/events?cursor=&limit=`(opaque cursor,响应带 `next_cursor` 与 gap marker 事件)、`POST /runs/{run_ref}/cancel`(控制命令,响应对应 CancelReceipt 状态)、`GET /runs/{run_ref}/artifacts`;`GET /runs/{run_ref}/events:stream`(SSE,标记为 optional capability)。run 引用在路径中的编码为既有逻辑引用的字符串形式(opaque,不要求路由解析其内部)。版本协商沿用 `contract_version` 字段 + 响应头 `X-Contract-Version`。

### D4: 身份 = 声明集 schema,双 profile,密码学留实现

`schemas/security-claims.schema.json` 定义声明集:`iss`/`aud`/`sub`/`tenant`/`delegation_ref`(复用 authorization 引用)/`exp`(短时效约束,数值上限写入 description 作为规范约束,schema 不做时钟断言)。OpenAPI `securitySchemes`:`bearerHttp`(JWT profile)与 `mutualTLS`,端点默认要求 bearer,mutualTLS 为部署级替换。回调认证在绑定文档单列:后端→平台回调用独立受众凭据,禁止复用入站令牌。负例:body `role` 字段忽略——映射层不读取任何身份语义的 body 字段(测试:携带自报 role 的样本仍校验通过,但规范条文 + 映射行为测试断言其不进入任何鉴权数据结构)。

### D5: 工具契约采纳内部副作用五值,四态失败分层落到既有语义

`tool.schema.json`:`input_schema_ref`/`output_schema_ref`/`version`/`side_effect_class`(枚举 = `SideEffectClass` 五值,注释注明与 `runtime/tool_side_effects.py` 对齐)。测试直接 import 该 StrEnum 校验枚举一致(测试可依赖产品代码,契约 schema 不 import)。四态分层:参数验证 → 提交前 `400` problem+json(绑定层)或引擎内分发前拒绝;业务拒绝 → `TOOL_RESULT` 型事件携带否定 payload(复用 event envelope,payload schema 由工具 output schema 定义);系统故障 → 工具错误事件;结果未知 → 复用 `outcome_unknown` + 既有 `reconciliation`。工具选择可观察事实:event envelope 扩展 `tool_selection` 细节字段(候选列表摘要 + 实际选择),命名空间化,不进最低契约必填集。

### D6: 托管映射 = 文档 + 一处 schema 增量

`contracts/openapi/` 旁新增 `src/hecate/contracts/hosted-mapping.md`:task↔session、run↔turn 映射表、状态映射指引、subagent 事件命名空间化规则(引用 step12 边界)。schema 增量仅 `errors.schema.json` 的 `reconciliation.strategy` 枚举 + `query_by_vendor_session`,以及 capabilities schema 的对账能力声明字段(托管后端声明是否支持按会话对账)。无 Python 代码。

## Risks / Trade-offs

- [OpenAPI 与 schema 双文件漂移] → D1 的 $ref 单真相 + 四方互检测试;改任一文件不同步即测试失败。
- [problem+json extension 成员与 errors.schema 字段不一致] → 映射表写入绑定文档,测试断言样本字段一一对应。
- [`openapi-spec-validator` 与 Python 3.12 兼容性/传递依赖] → 进 dev extra 不进运行时依赖;CI 已有 Python 矩阵,若报不兼容当场降级为结构自检(风险低,该库为纯 Python)。
- [身份声明集被误用为"已认证"] → 样本与文档明示"声明集 ≠ 验证";验证语义归 step7 adapter,规范条文写"身份只来自经校验的传输层声明"。
- [工具契约与内部注册表词汇再分叉] → 测试 import `SideEffectClass` 钉死枚举一致;内部实现重构时契约枚举不跟随改名,只增不改。
- [HTTP 绑定被当成唯一传输] → 绑定文档开篇即传输中立声明;SSE 标 optional capability。

## Migration Plan

纯契约层增量:新文件 + `errors.schema.json` 枚举扩展(向后兼容——新增枚举值,旧接收方按未知字段/值容忍规则处理,且该字段只在错误体中出现);无产品行为变更,合入即生效,回退 = revert。0.x 版本号递增(`0.1`→`0.2`)仅对受影响 schema(errors/capabilities)与新文件,`execution-request` 等未动文件不 bump——沿用"按能力独立版本"规则。

## Open Questions

(无——三项决策已由用户确认;非 Python 试点的语言与 CI 接入两决策属试点 change,不阻塞本 change。)
