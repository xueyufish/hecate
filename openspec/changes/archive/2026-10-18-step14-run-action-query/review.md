# 复核记录 — step14-run-action-query

## 结论

已在原 feature worktree 修正发现的问题，保留只读查询切片范围。未提交、推送、创建 PR 或归档。实际命令与结果见 verification.md。

## 已复现并修正

### 父 Task 未参与可见性校验

原代码只调用 registry.get_run；该方法仅验证 Run 自身的 workspace 和 deleted。父 Task 已软删除或其 workspace 不一致时，查询仍返回动作记录。追加两个回归用例，原实现均未按预期抛出 NotFound。

查询现在先核实 Run，再核实所属 Task；任一不可见统一返回请求 Run 的 404，台账零读取。HTTP 与服务测试分别覆盖跨域、删除 Run、删除 Task、父归属不一致和不存在 Run。

### 原始存储异常泄漏到客户端

原代码把异常字符串拼入 TaskControlError，再由 API mapper 原样输出。以包含连接 URL、密码和 SQL 参数的异常复现，敏感文本进入对外错误消息。原 except TaskControlError 的透传分支也可能把存储层抛出的应用异常按其类型转为错误的 4xx 或泄漏原文。

查询与投影异常统一转为固定 500 消息；只在日志记录请求 Run ID 和异常类型，原始诊断保留为内部 cause，不进入响应或日志。覆盖 RuntimeError、应用异常以及畸形读取结果。

### 验证记录超出原测试证据

原零副作用测试只核实 Task 行仍存在、命令列表为空，没有比对生命周期或非空命令；状态用例没有真正领取中的动作；HTTP 套件只验证 Stub 200、缺失 404 和覆盖 store 的 500，没有真实 SQL 成功响应及跨域/删除场景。

已补：
- 真实 claim_ex 领取与 fenced outcome，Task 终态不改写各动作事实；
- 非空动作、运行中生命周期、过期但仍 requested 的命令及 outbox/全部 durable 表完整快照；
- Task/Run ORM 全字段比对、SQL 观察只允许 SELECT 与合法事务 BEGIN；
- 未替换 get_task_control_service 的真实 SQL HTTP 成功查询及授权拒绝；
- 新服务测试使用共享 db_session，移除另建的平台 ORM engine；
- 重新编写工件，删除未证实的测试覆盖、数据量固定有界或 PostgreSQL 继承认证表述。

## 引用核实

保留 run_row_ref 的平台 Run 关联，这是现有 dispatcher/SQL hook 实际写入的身份；不改为 backend_ref 中的引擎 session ID。新增用例令二者不同，并在后端引用下记录诱饵动作，确认不会混入。此处是澄清原设计表述，不是改写现有台账身份。

## 范围与剩余工作

仅修改 task_control.py、channel/api/tasks.py、两个新查询测试文件及当前 OpenSpec 工件。未改 Runtime、durable、schema 或其他 worktree。

本 API 查询 ledger_source 声明的当前平台账本；不拉取远端宿主动作。未引入分页或证明大规模查询性能。PostgreSQL 专项本轮未执行；CI 与 PR review 由正常提交流程完成。
