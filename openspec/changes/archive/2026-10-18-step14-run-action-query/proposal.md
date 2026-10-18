# Proposal

## Why

客户端需要按指定 Run 查询动作事实，包括历史执行尝试。现有待对账视图按任务选择最新 Run，不能替代常规动作查询；持久台账已有 list_run_actions 能力。本 change 增加只读入口，不引入另一份动作或证据真源。

## What Changes

- 新增 GET /api/runs/{run_id}/actions，协议层仅传递可信 workspace、Run ID 和既有错误映射。
- 服务先核实 Run 与所属 Task 在当前 workspace 均存在且未删除，失败统一 404，并在任何台账读取之前结束。
- 平台本地台账按原派发链使用的 run_row_ref 查询，包含 issuer 与平台 Run ID；该引用与 backend_ref 中的引擎 session ID 分开，不回退到最新 Run 或其他引用。
- 逐字段白名单响应：动作键、执行与 tool-call 标识、工具名、参数摘要、副作用类别、恢复状态、已记录 outcome、结果摘要和待对账标记；不返回原始参数、结果内容、session、claim token 或凭据。
- 区分真实空台账、查询能力不支持和存储故障；故障返回固定消息，服务端只记录 Run ID 和异常类型，不转发可能含连接 URL、SQL 参数和结果内容的诊断。
- 成功、拒绝及故障查询均不执行、重试、对账或迁移状态。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- platform-task-control：按指定 Run 查询动作记录，含父 Task 可见性、历史隔离、显式来源/支持状态、字段白名单和零副作用。

## Impact

- 仅修改 task_control.py、channel/api/tasks.py、两个独立测试文件与本 change 工件；不触及其他在途 responder 或 evaluation 实现。
- 完整 spec-driven：新增公开 API，涉及 workspace 资源访问边界；需相关回归、共享检查、CI 和 PR review。本地验证不替代 CI/PR。
- ORM 测试使用共享 db_session；真实 SqlDurableStore 使用隔离 SQLite 文件，不另外创建平台 ORM engine。
- 无数据库迁移、Runtime/durable 实现变更、工作台、Evidence Receipt、动作派发、全局搜索或分页协议。本片只查询当前绑定的平台账本，不拉取远端宿主台账。
