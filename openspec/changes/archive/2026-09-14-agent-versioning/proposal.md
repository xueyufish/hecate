## Why

Agent 目前没有版本化：外部渠道直接调用活的 `AgentModel` 行（`channel/api/v1/agents.py` 加载活行），studio 里的一次编辑立即对外生效，外部调用者没有任何稳定契约；IM 会话路由更是回退到全零 UUID 的 `default_agent_id`（`channel/gateway/im_session_router.py`）。工作流虽有版本化（1.1.9），但其运行时取最高版本号而非 `published_version`，"发布"语义名存实亡。1.3.20 是路线图 Opening Queue 的最后一项，也是 Resource Versioning（14.x 机制）的载体——5.9d（Skill Versioning）、3.5.12（Ontology Versioning）、7.5（A/B Testing）都引用它。业界对标（Dify draft/published 硬绑、Bedrock AgentCore endpoint 别名、AgentArts 提交版本→Runtime 实例）表明"草稿/发布二态 + 别名间接层"是产品型平台的及格线。

## What Changes

- **Agent 版本快照**：新增不可变 `agent_versions` 表——自有字段冻结（persona、model_config、mode、tools、skills、knowledge_base_ids、risk_level、guardrail_config 等）+ `pinned_refs`（pin 引用的工作流版本）+ `ref_manifest`（skills/tools/KB 记 `(resource_id, version|null, content_hash)`，未版本化资源运行时实时解析但漂移可检测）。
- **版本生命周期**：显式「提交版本」→ 发布（可挂 7.3a 评估门禁，v1 默认 warn/关）→ 回滚（照抄 1.1.9：以目标版本内容新建版本，再显式发布）→ diff。被渠道 pin 的版本与已发布版本禁止删除。
- **Channel 实体（别名间接层）**：新增 `channels` 表——`type(api|im|embed|webhook)`（v1 仅 api/im 接线，embed/webhook 为占位枚举）、`agent_id`、`bind_mode(published|pinned)`、按 type 的 `config` JSON。渠道创建要求该 agent 已有已发布版本。
- **版本解析接缝**：统一 `resolve(agent_id, version?)` —— studio 编辑/试跑永远走活草稿；API 渠道按 `bind_mode` 解析（`published` 追踪最新发布 / `pinned` 钉死 vN）；`X-Agent-Version` 请求头（或 `?version=`）允许显式指定版本调试。语义承诺为**配置不可变**（不承诺行为不可变——模型权重可能在同一模型 ID 后更新）。
- **IM 渠道路由修复**：`IMSessionRouter.resolve_or_create` 弃用全零 UUID 兜底，改为按渠道实体路由（provider/应用实例 → channel → agent + 版本）；`IMMessageBus.enqueue` 的 `agent_id` 正确填充。**BREAKING**（对依赖隐式兜底行为的 IM 部署：未配置渠道的 IM 实例将从"静默路由到零 UUID"变为显式错误/跳过）。
- **工作流运行时发布解析（顺带修复）**：`WorkflowExecutionService._load_workflow_graph` 改为按执行上下文解析——studio 试跑走最新草稿，其余执行优先 `published_version`（未发布过则回落最高版本）。**BREAKING**（存量"已发布但仍在改草稿"的工作流：studio 之外触发的执行将改跑已发布版）。修复后 1.1.9 的回滚语义自动收敛为"回滚产生新草稿版本，发布才上线"。

## Capabilities

### New Capabilities

- `agent-versioning`：Agent 版本快照模型与生命周期——提交/发布/回滚/diff、引用清单与漂移检测、工作流版本 pin、版本删除约束、发布评估门禁挂钩（warn 模式）。
- `agent-channel-publishing`：Channel 实体与渠道发布——渠道 CRUD、`bind_mode` 两种绑定语义、API 渠道版本解析与 `X-Agent-Version`、IM 会话按渠道路由到 agent 版本。

### Modified Capabilities

- `workflow-version-publish`：新增需求——运行时执行按上下文解析版本（studio 试跑=最新草稿；其余=`published_version`，未发布过回落最高版本），使"发布"成为真实运行时语义。
- `im-channel-feishu-slack`：会话创建的 agent 解析从硬编码 `default_agent_id` 改为渠道实体路由；消息入队携带正确 `agent_id`。

## Impact

- **数据**：新表 `agent_versions`、`channels`（Alembic migration）；`agents` 表本身不加版本列（草稿即活行）。
- **代码**：`src/hecate/models/`（新 ORM + schema）；新增 studio/agents 版本化 service（模板：`studio/workflows/service.py` 的 `list_versions/publish_version/rollback_to_version/diff_versions`）；`channel/api/v1/agents.py`（解析接缝）、`channel/gateway/im_session_router.py`、`channel/im/message_bus.py`；`studio/workflows/execution_service.py`（published 解析）。
- **不改**：`packages/channels/hecate-channel-{feishu,slack}` 插件（路由修复在 core gateway 层）；`web-widget-access`（embed 保持 `?agent=` 现状，v1 不绑版本）；agent-as-tool 调用保持 live 解析（跨 agent pin 属 14.x）。
- **不做**（防止过度设计）：A/B 与流量切分（7.5）、环境晋升、深拷贝资源、key 绑渠道、embed/webhook 接线、重审批流。
