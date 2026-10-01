# Design

## Context

- Change 1(#202)已交付:`AgentPrincipalModel`/`AgentDeploymentModel`(`models/agent_principal.py`、`models/agent_deployment.py`)、`execution/principal_registry.py`/`deployment_registry.py`(单写入方应用服务先例)、`contracts/execution/identity.py`(`IdentityChain`/`WorkloadIdentity`,带 `to_dict`/`from_dict`)、step3 执行契约(`BackendRef`/`ExecutionRequest`/`OwnershipAxes`)、builtin 回填迁移(含部分唯一索引先例)。
- 本 change 是 step4 第二支,交付 Task/Run 数据契约与登记语义;按方案 §四,step5d 入口映射、step6 持久任务/控制命令、`managed-runner-enrollment` 均以其为前置。
- 现有 A2A `task_store` 独立自成一体(原基线 §2);本 change 只登记映射语义,不接 A2A(属 step8)。

## Goals / Non-Goals

**Goals**

- `TaskModel`/`RunModel` 及配套 enrollment、conversation 映射表与迁移。
- `execution/task_run_registry.py` 单写入方服务:创建/重试/后端会话绑定/观察导入/独立部署登记/惰性会话映射。
- contracts 层 `TaskRef`/`RunRef` 逻辑引用类型;分层测试把新表纳入 execution 域单写入方保护。

**Non-Goals**

- Task 工作流状态机与 `POST /api/tasks` 等 API(step6)。
- 受管注册/投影更新/控制命令的真实执行路径与断连语义(`managed-runner-enrollment`,step6/7)。
- 活跃 Run 迁移、A2A task_store 接入(step8)、独立宿主本体(step5c)。
- 事件存储改造:Run 只携带事件序号游标,事件本体仍在执行方/EventStore。

## Decisions

### D1: 表布局与命名——四张新表,全部 execution 域拥有

| 表 | 模型 | 要点 |
|---|---|---|
| `tasks` | `TaskModel`(`models/task.py`) | goal、initiator_ref(JSON,身份链引用)、acceptance(JSON)、responsibility、workspace_id、issuer_domain;**无状态列**(step6 再以 expand 加) |
| `runs` | `RunModel`(`models/run.py`) | task_id/deployment_id FK、attempt_no、identity_chain(JSON 冻结快照)、backend_ref(JSON)、backend_session(JSON 可空)、event_cursor、projection(JSON)、origin(`platform`/`imported_observation`)、local_source(可空:本地签发域+本地 run ID)、workspace_id 冗余用于隔离 |
| `standalone_enrollments` | `StandaloneEnrollmentModel`(`models/standalone_enrollment.py`) | 宿主身份/信任根、已装版本/能力 JSON、managed_new_runs(默认 false)、operator/admitted_at/admission_result |
| `conversation_task_links` | `ConversationTaskLinkModel`(`models/conversation_link.py`) | conversation_id、task_id、workspace_id、governance_status(正常/待治理) |

类名遵守 `XxxModel` 约定;表名与 A2A 的 a2a_tasks 不冲突。唯一约束:`(task_id, attempt_no)`;`(backend 签发域, backend_session)` 在非空时唯一——沿用 Change 1 迁移里部分唯一索引的写法。

*备选*:合并 enrollment 进 Deployment 表。否决——平台登记的 Deployment 与本地宿主的注册是两个写入方与信任域(方案 §一:宿主身份与信任根校验是独立语义),合并会破坏单写入方规则。

### D2: 不引入 Task 状态机,Run 只存投影与游标

Task 不设 status 列;Run 的 `projection` 是"平台最近观察到的后端状态"自由 JSON + `event_cursor` 整数游标,不定义生命周期枚举。理由:queued/waiting_approval 等状态语义、收敛规则和命令回执属 step6 契约,现在发明会造成后续返工;方案 step6 才是"任务不依赖 HTTP 请求存活"的交付点。spec R1 已把"MUST NOT 预先引入"写成规范。

### D3: 身份链与后端引用以契约类型进出,列上存冻结序列化

Run 的 `identity_chain`/`backend_ref`/`backend_session` 存 `IdentityChain`/`BackendRef` 的 `to_dict` 结果;登记服务在边界处 `from_dict` 校验后写入,读取时往返还原。往返保真测试钉住(与 Change 1 `test_identity_chain.py` 同法);`contracts/execution/references.py` 增加 `TaskRef`/`RunRef`(issuer_domain + id,`to_dict`/`from_dict`),供未来进程外消费,不泄漏 ORM。契约纯度测试(`test_contract_purity.py`)随扩展。

### D4: 重试与后端会话的唯一性由服务层 + 部分唯一索引双保险

`create_run` 以 `(task_id, max(attempt_no)+1)` 计算尝试号,`(task_id, attempt_no)` 唯一索引兜底并发;`bind_backend_session` 拒绝已绑定签发域+会话的二次绑定,部分唯一索引兜底。重试不复制旧 Run 的 backend_session(新尝试新会话),旧 run_id 永不复用——映射服务无任何"复活"路径。

### D5: 观察导入的"无投递权"用结构性证明

平台侧本 change 根本不存在投递/调度代码路径;`import_observation_run` 只能创建 `origin=imported_observation` 的 Run,且登记服务没有把 Run 加入任何队列的 API。测试断言:导入 Run 的 origin 标记、导入前后无任何其他行变化(tasks/conversation_links 不因导入创建)。真实投递权验证在 step6 有投递路径后补齐。

### D6: 会话映射惰性创建,迁移只加新表

存量 chat session 不回填:首次经 `link_conversation` 触及时创建映射行,缺失发起者治理数据时 `governance_status=pending`(与 Change 1 待治理语义一致)。alembic 单迁移仅建四张新表——无存量数据需要回填窗口,唯一约束即建即收紧;这满足 step4 第 17 项"先新表 → 回填 → 收紧"的增量原则(回填步骤为空集,惰性映射即兼容路径)。

### D7: 分层与单写入方

新表全部登记为 execution 域拥有:`tests/test_layering_domain.py` 按 Change 1 增补的模式,把 `models/task.py`/`run.py`/`standalone_enrollment.py`/`conversation_link.py` 加入 execution 拥有表清单,跨域直接写即测试失败。`task_run_registry.py` 是唯一写入方,API 形态对齐 `deployment_registry.py`(显式方法、审计字段、workspace 校验)。

## Risks / Trade-offs

- [无状态列可能被误读为遗漏] → proposal/spec/design 三处显式声明属 step6;step6 以 expand 迁移加列,不返工本表。
- [`tasks`/`runs` 表名与 A2A task_store 概念混淆] → A2A 保持独立表;design Context 登记映射语义;接入留 step8。
- [部分唯一索引跨 SQLite/PG 语法差异] → 沿用 Change 1 `backfill_builtin_deployments.py` 已验证的写法。
- [projection JSON 无 schema 可能被滥用为事实真源] → spec R2 已规范"投影 MUST NOT 覆盖执行事实";测试断言投影更新不触碰执行方权威字段;字段级 schema 属 step6 事件 envelope。

## Migration Plan

单个 alembic revision:`down_revision` 指向当前 head;upgrade 建四表(含约束/索引),downgrade 依次删表。既有链路零接触——chat/workflow 执行不读写新表(分层测试与全量回归验证)。回滚 = downgrade 新表,无既有数据损失。

## Open Questions

(无——命名、状态机延后、惰性映射与唯一索引策略均已按 Change 1 先例与方案 §四固定;owner 指派沿用"change 启动时指派"的既定安排。)
