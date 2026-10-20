# Design: step14-task-command-write-access-boundary

## 背景

`POST /api/tasks/{task_id}/commands`(:284)与 `POST /api/tasks/{task_id}/cancel`(:230)均汇入 `_issue`(:316),只向 `TaskControlService.issue_command` 传 `workspace_id=_workspace_id(ctx)` 与 `issuer=str(ctx.user_id)`。服务层(:686)经 `_get_task(workspace_id, task_id)` 做 workspace 归属校验后,先做幂等重放(:710-721:同 `command_id` 且绑定比对一致即返回既有回执),再记录命令、写 outbox 并触达后端——全链路无角色判定。`_workspace_id`(:158)对缺 workspace 身份一律 403 `NO_WORKSPACE`,system-scope key 因此今天就不能发命令。

## 权限门设计

**位置:`_issue` 入口处**(两个写入口与未来命令种类一并覆盖;`workflow-callback` 走独立方法不在其内)。判定次序与 #283/#287 模式一致——先可见性后角色,存在性不泄露:

1. **payload 形状校验**(现有 422 `PAYLOAD_WITHOUT_SCHEMA` 保持原位:只涉及请求体,不触任务、不泄露存在性)。
2. **任务可见性**:按 workspace 查询 Task(`registry.get_task(task_id, workspace_id)` 既有作用域语义);跨 workspace、软删除、不存在三种情形返回**同一个** 404 envelope,不区分原因。若既有 `get_task` 未过滤软删除,则在门内补 `~deleted` 判定。
3. **角色门**:`ensure_workspace_role(ctx, WorkspaceRole.EDITOR)`(system scope 由其内部豁免;缺 workspace/role 抛 `WorkspaceRoleRequiredError`)。`_workspace_id` 的 403 `NO_WORKSPACE` 保持在前——缺 workspace 身份在角色门之前即被拒,行为与现状一致。
4. 通过后才进入既有 `service.issue_command`(幂等重放、记录、outbox、后端调用全部原样)。

403 映射复用既有模式(`WorkspaceRoleRequiredError` → HTTPException 403,`{"error": {"code": "FORBIDDEN", ...}}` envelope);404 沿用该 API 既有的 NOT_FOUND envelope 格式。

## 关键语义

- **门槛先于幂等回放**:重放路径在服务层内部,角色门在 API 层先执行——降权用户的旧 `command_id` 重放在触达服务前即 403。服务签名与行为不变；`submit_workflow_callback` 是既有内部生产调用方，不经 REST 门，必须保留其兼容行为。
- **服务端身份**:`ctx` 来自 `get_auth_context` 依赖;请求体 `CommandRequest` 无角色字段,不引入。
- **system scope**:`is_system_scope` key 无 workspace,`_workspace_id` 依旧 403——不新增跨 workspace 命令能力(与现状逐字节一致)。
- **既有语义零变化**:审批绑定、wait token、`expected_revision`、期限、幂等键绑定比对(:710-721)、回执状态机全部原样;EDITOR 只是把关人,不豁免任何既有校验。

## 测试设计

新增 `tests/test_api/test_task_command_access_boundary.py`,参照 `test_agent_version_access_boundary.py` 的双 workspace 模板(`_workspace_with_role`/`_client_as`/`_no_role_context`/`_no_workspace_context`/`_system_context`):

- **双 workspace**:W2 成员对 W1 Task 的两个入口均 404,且与随机不存在 Task 的响应体一致;W1 内 zero side-effect 断言。
- **软删除**:deleted Task 的命令/取消 → 404 与不存在一致。
- **角色**:viewer 两入口 403;editor/admin 两入口 200/201 按既有语义(REQUESTED 回执、幂等重放正常)。
- **无角色/缺 workspace**:role=None 同 workspace → 403;`_no_workspace_context` → 403 NO_WORKSPACE(现状保持)。
- **system scope**:显式 system key → 维持既有行为(403 NO_WORKSPACE,不新增能力)。
- **降权重放**:EDITOR 下发成功 → 角色降为 viewer → 同 `command_id` 同请求体重发 → 403,既有回执逐字段不变。
- **零副作用**:被拒请求前后命令存储记录数、Task/Run 状态、outbox 条目、后端(stub)调用计数全部不变。
- **审批/revision 锚点**:editor 对带 `expected_revision` 冲突或需审批的请求仍按既有拒绝语义(证明门槛不替代审批)。
- **回归**:既有 Task 命令查询/回执/workflow-callback 测试(`tests/test_api/` 下 task 相关文件)全绿。

需要种子 Task:沿用既有 Task API 测试的提交路径(`POST /api/tasks` 或 service 直建),种子后显式 commit(与既有边界测试相同的 durability 模式)。

## 兼容性

| 场景 | 行为 |
|---|---|
| editor/admin 正常下发 | 与现状一致(门槛放行) |
| viewer/无角色下发 | 从可记录命令变为 403——即本 change 目的 |
| 缺 workspace 下发 | 不变，维持 403 `NO_WORKSPACE` |
| 跨 workspace/软删除/不存在 | 维持既有统一 404；可见性判定提前到角色门之前 |
| 降权后旧 command_id 重放 | 从"幂等重放成功"变为 403 |
| system scope | 不变(维持 403 NO_WORKSPACE) |
| 可信内部调用(非 REST) | 不变(服务签名兼容) |

## 非目标

- `POST /{task_id}/workflow-callback`(协议回调 token 信任模型)、Task 提交入口、命令读取端点的角色门槛、outbox/命令存储结构、前端干预 UI、评测模块、Runtime、共享 skill 文件。
