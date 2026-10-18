# Design

## Context

SqlDurableStore.list_run_actions 按 (run_issuer, run_id) 查询，返回恢复事实、intent/outcome 以及 session、claim token、result_payload 等内部内容。平台 dispatcher 和 entry bridge 将动作关联到平台 Run 行引用，不是 Run.backend_ref 中的引擎 session 引用。

TaskRunRegistry.get_run 仅验证 Run 的 workspace 与 deleted，不能代替所属 Task 的存续校验。两个读取都使用既有 registry，不修改其共享行为或任何控制路径。

## Goals / Non-Goals

交付一个只读 API 和一个服务方法，保持新旧 Run 的独立动作事实，明确台账来源及能力状态。不新增台账、扩展接口、迁移或执行行为；不做远端宿主台账聚合、工作台或完整 Evidence Receipt 系统。

## Decisions

### D1: Run 和父 Task 在读取前共同授权

服务先 get_run(run_id, workspace_id)，再 get_task(run.task_id, workspace_id)。Run 不存在、已删除、跨 workspace，或父 Task 不可见时，统一返回所请求 Run 的 NOT_FOUND；不暴露父引用，不进行台账查询。这是直接验证指定 Run 的父资源，不会列出任务所有 Runs 或替换查询对象。

### D2: 使用已部署的平台台账关联

run_row_ref(run) 保持 dispatcher/SQL hook 的 (Run issuer, platform Run ID) 绑定；不把 backend_ref/session 混作平台动作所属 Run。测试令这两个 ID 不同，在后端引用下另放诱饵记录，证明历史查询只返回指定平台 Run 自身的动作。

本片的 ledger_source 标识当前绑定的平台台账，supported 标识该台账是否提供查询能力；不代表已同步或扫描远端宿主全部动作。

### D3: 白名单与故障边界

服务对每行新建 dict，字段固定为 action_key、execution_id、tool_call_id、tool_name、arguments_digest、side_effect_class、state、outcome、result_digest、pending_reconciliation。既有四态恢复词汇与具体 outcome 分列，不将 succeeded/failed 强塞进恢复枚举，也不由 Task 终态推断动作状态。

查询方法不可调用时返回 supported=false、来源和空列表；真实可查询但无行返回 supported=true。读取或结果投影失败均转换为固定 TaskControlError，经既有 mapper 返回 500；异常原文只保留在内部 cause，不进入响应或日志。日志仅含 Run ID 与异常类型，便于关联而不泄露连接 URL、SQL 参数或结果内容。

### D4: 零副作用与测试装配

使用项目共享 ORM session，真实 SQL 台账在独立临时 SQLite 文件，二者本来就是分开的资源边界；查询不需要为平台额外建立 engine 或进行跨库事务。

服务与 HTTP 测试覆盖：
- 多次尝试、历史引用、不同 backend_ref、真实 success/claimed/failed/unknown；
- workspace、Run/Task 删除和父归属不一致，拒绝发生在查询之前；
- 真实 SQL 200、真实空列表、实际 InMemory Stub、故障 500、无 workspace 403、无效 ID 422；
- HTTP 使用原 get_task_control_service，仅注入数据库、身份和 composition 资源，不以假的 service 返回 dict；
- 对已有 claimed/完成动作、运行中生命周期、过期但仍 requested 的命令及 outbox，全表快照查询前后相同；Task/Run ORM 行也比对全部字段，SQL 观察仅允许 SELECT 和事务 BEGIN，不允许数据写入。

### D5: 分层与交付

delta 只落 platform-task-control。无 schema 升降级或数据回填；回退为撤销新增入口/方法，既有动作事实保留。共享检查与实际测试结果记录在 verification.md，不把缺失场景或未运行 PostgreSQL 计为通过。

## Risks / Trade-offs

- 保留未分页列表协议；本片没有证明动作数存在固定上限或完成大规模查询性能认证，需要时另做分页切片。
- 当前支持的是绑定台账的读取，不承诺远端宿主动作已被中心同步。
- 不通过改动 Runtime、durable schema、演进总计划或功能清单扩大本 change，也不修改其他 worktree。
