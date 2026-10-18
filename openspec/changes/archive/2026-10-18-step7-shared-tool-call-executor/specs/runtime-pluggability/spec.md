# Spec Delta — runtime-pluggability

## ADDED Requirements

### Requirement: 单次工具调用可脱离图与 Worker 执行

单次工具调用的完整治理链(参数解析与摘要、恢复判定、访问策略、审批、pre/post middleware、sandbox 路由、Action Ledger 镜像、结果回执、证据捕获)SHALL 由具体类共享组件承载,调用方 SHALL NOT 为执行一次工具调用而构造 Pregel 图、ChannelManager 或 Worker 实例。组件经注入的 `RuntimePort`、治理组件与 `ActionLedgerHook` 工作,SHALL NOT 引入新的扩展点接口或第二套动作服务/账本;行为契约(调用身份生成、事件格式、恢复规则)SHALL 与提取前保持一致。独立调用方 SHALL 显式提供执行上下文(provenance)——会话标识为稳定执行身份所必需,非空 tool-call ID 同样是稳定恢复前提;缺失标识的 legacy 调用保持随机 ID 回退且不承诺稳定恢复。图调度字段不作为独立调用前提,省略 superstep 时按事件格式使用 0。

#### Scenario: 禁止图执行机制导入后仍可执行单次调用

- **WHEN** 全新 Python 进程在 runtime 首次导入前阻断图执行机制与 Worker/workers 的模块导入,随后构造共享执行组件并经注入的 RuntimePort 完成一次只读工具调用
- **THEN** 调用成功返回结果消息,全流程未构造图、ChannelManager 或 Worker,也未触发图执行机制导入

#### Scenario: 独立调用提供等价 provenance

- **WHEN** 独立调用方仅提供会话标识与节点标签,不含图调度上下文
- **THEN** 组件按会话标识及非空 tool-call ID 派生稳定执行身份,证据携带调用方节点标签,事件回执保持 node_id=None 和既有 execution_id/tool_call_id 关联;superstep 缺省为 0,不要求图调度字段存在

### Requirement: 直接调用与 Worker 委托两入口行为一致

`ToolWorker` SHALL 委托共享组件执行单次调用;同一行为用例(策略拒绝、审批拒绝与无 callback、middleware 链与 legacy hook、sandbox 路由、成功/异常/未知结果、账本 claim 与已完成动作真实回填、未知结果与账本故障的既有恢复规则)以"直接调用共享组件"与"经 `ToolWorker` 调用"两个入口参数化运行 SHALL 全部通过且结果逐项一致。批次路径(消息中工具调用提取、并发调度、结果排序、citation 标记、`WorkerResult` 包装)行为保持不变,受保护副作用 SHALL NOT 因重构重复发生。

#### Scenario: 双入口行为参数化一致

- **WHEN** 同一组行为用例分别以直接调用共享组件与经 `ToolWorker` 执行
- **THEN** 全部用例通过,结果消息、事件序列、审批与账本交互逐项一致

#### Scenario: 受保护副作用不因重构重复

- **WHEN** 已完成动作经恢复路径重放(含账本回填与事件日志回执)
- **THEN** 真实结果被回填,业务工具不被第二次调用;未知结果与账本故障按既有规则停止或待对账
