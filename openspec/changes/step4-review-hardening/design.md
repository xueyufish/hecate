# Design

## Context

见 proposal。GLM 已合并的登记服务尚未被实际执行入口调用，因此可先收紧登记 API，而不切换旧 Agent 行为。生产为 PostgreSQL，测试使用共享 SQLite；约束必须跨方言一致。

## Goals / Non-Goals

修正登记数据和审计可追溯性。保持模块单写入方和纯数据契约，不增加运行时扩展点。不实现 IdP/授权引擎、宿主网络注册、任务调度或断连控制。

## Decisions

- 使用既有 workspace/组织/用户/成员表验证归属；audit 经公共 execution 内部函数解析真实 org。拒绝日志与调用者事务一致，不在登记服务内部擅自 commit；异常后需要保留审计的调用方必须提交拒绝事务。
- 部署快照默认补完整 unsupported 能力；backend 类型/所有权必须一致。enforced 留给可信验证流程，登记 API 拒绝该等级；托管事实缺少完整来源/时间/条件时保持 unverified。
- Run 新增 execution_snapshot 和 control_owner；复制版本配置、部署契约/能力/引用，不复制凭据值。IdentityChain 增加可空 audience 以兼容旧序列化，新建 Run 必须非空；委派引用必须 authorization 类型。Run 要求 principal/部署对应且 principal 活跃。观察导入保留旧 principal，但不授予权限。
- 在 PostgreSQL 对 Task 加行锁分配 attempt；数据库唯一键兜底。会话增加 issuer/id 标量列建唯一约束，与 JSON 文本格式无关；session 和 turn 在同一签发域共享标识空间，绑定不可覆盖，软删除也不释放。
- 导入来源按 workspace + deployment + issuer + local_run_id 唯一；重复导入返回原记录，冲突目标拒绝；不遍历整表。
- conversation 外键改为 conversations；单独 link_session 解析旧 Session.conversation_id 并校验双方租户。默认治理状态 pending，不用调用者的 true 声明认定身份已解析。
- 准入使用注入的异步可信解析 callable（具名消费者 set_managed_opt_in），默认缺省拒绝 admitted；调用者传来的 named refs 只是 pending 声明。管理员需真实活跃成员，installed_versions 含版本和能力。模型 CHECK 防止 rejected/pending + managed=true。
- 新迁移负责已升级库的修复；历史会话映射只能从 session 的 conversation_id 解析，同租户才迁移，不能解析则终止供人工修复。不凭空重建历史身份、受众、执行快照，已有 Run 保持 NULL 并在报告中注明治理缺口。

## Risks / Trade-offs

- 旧登记测试没有真实 workspace/conversation/principal → 改用真实实体并添加负例。
- 已有外部表直接写入可绕过服务 → 保留分层门禁并增加数据库唯一/准入约束；完整数据库权限分离属于后续部署治理。
- 托管供应商声明不能等价平台认证 → 不在 Step4 开放 enforced 提升，后续认证保留真实证据。

## Migration Plan

先部署增量迁移，修复规范化标识、默认部署约束与会话外键；发现重复/错误数据时清晰停止，不丢弃数据。修正原始建表/回填脚本的首次安装问题，且新迁移覆盖已升级数据库。Step4 和修复迁移 downgrade 显式拒绝；应用回退保留 schema 与审计/运行记录，不能用空 downgrade 伪造 alembic 版本回退。
