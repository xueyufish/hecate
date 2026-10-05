# Design

## Context

当前 Runner 已有 durable task/action ledger 和本地 HTTP API,但执行循环仍是 `PregelRuntime` 的专用驱动代码;平台内置后端也有一套相似的驱动、事件捕获和状态归类。step3 已有权威 schema/OpenAPI,但 Runner wheel 不携带这些 schema,HTTP 响应也使用 preview 字段。

## Goals

- 将“驱动 runtime、捕获事件、归类 succeeded/failed/cancelled/unknown”抽为 runtime 包中的应用服务。
- 平台后端和 Runner 只保留各自 adapter:身份、授权、存储、证据、生命周期和协议绑定。
- Runner 能通过语言无关 HTTP/JSON 请求完成提交、执行、状态查询、事件读取;同一 idempotency key 重试不重复执行。
- durable Runner 重启后能按 backend run ref 查询终态与事件,不需要完整 Hecate 应用或平台 ORM。

## Non-Goals

- 不实现受管投递、动作时授权、审批等待、原生 checkpoint 恢复或工作流回调。
- 不把 Runner 固定图改造成通用图编译器;本切片只共享执行应用行为。
- 不冻结 0.x 契约为正式跨版本兼容承诺;仍按演进方案由真实异构后端验证后冻结。

## Decisions

1. **应用服务位于 `hecate-runtime`,不拥有状态存储。**服务接收已装配的 runtime、稳定 session、初始输入和 observer,返回执行结果。调用方继续决定 durable state、evidence、action ledger 和 HTTP 映射。
2. **observer 可请求继续或以 unknown 停止。**内置后端的 interrupt 事件通过 observer 映射为 unknown 停止;Runner observer 只捕获事件并写入本地事实。
3. **合作取消由调用方提供谓词。**服务在每个事件后检查谓词,到达下一个执行边界前抛出合作取消;已发生的外部调用不由服务撤销或重放。
4. **Runner 正式契约通过本地 schema registry 校验。**权威 schema 仍为 `src/hecate/contracts/schemas`;Runner wheel 携带经逐字节测试钉住的 `_contract_schemas` 发布快照,运行时用本地 registry 解析 `$ref`,不访问网络。
5. **durable event id 由 run 与本地事件序稳定派生。**重启重放时同一位置同一内容幂等返回;不同内容触发冲突并进入待对账路径。

## Risks

- preview API 与正式响应并存可能让调用方误用字符串 run ref;通过测试和 README 明确正式客户端必须使用结构化 `run_ref`。
- event 写入失败发生在副作用后时不能盲目重试;引擎将其标记为待对账,由后续恢复切片收敛。
- 0.x schema 尚未冻结,Runner wheel 携带的是当前权威草案并随契约测试同步。
