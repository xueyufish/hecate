## Context

现状（详见 proposal）：Agent 无版本化，外部渠道直连活行；IM 会话路由兜底零 UUID；工作流有版本存储但运行时取最高版本。可复用模板：`WorkflowModel/WorkflowVersionModel`（指针 + 不可变快照）与 `WorkflowService.list_versions/publish_version/rollback_to_version/diff_versions`；发布门禁机制（7.3a `evaluation_gate`）。约束：跨域访问只做函数级引用（无模块级结构耦合，如 `channel/api/v1/agents.py` 引 studio session_lock 的既有先例）；studio 前端在本仓 `web/`（Next.js，agent 界面位于 `src/app/(dashboard)/agents` 与 `src/components/agent`，`src/components/workflow` 已有可镜像的版本化 UI 先例）。

## Goals / Non-Goals

**Goals:**

- Agent 版本快照存储 + 提交/发布/回滚/diff 生命周期（复刻 1.1.9 形态，适配组合型实体）
- 统一版本解析接缝：studio=草稿、渠道=绑定版本、显式头指定版本
- Channel 实体作为别名间接层（published/pinned 两模式），api/im 两类型接线
- IM 会话经渠道路由（替换零 UUID 兜底）；工作流运行时改读 `published_version`
- 为 14.x 留接缝：引用清单支持后续把 `(id, null)` 升级为 `(id, version)` 而不改快照形状

**Non-Goals:**

- A/B 与流量切分（7.5）；环境晋升（dev/staging/prod）；审批工作流（多级/多角色）
- 深拷贝资源内容；key 绑渠道；embed/webhook 接线（仅保留枚举）；agent-as-tool 的跨 agent pin（14.x）
- 行为不可变承诺（模型权重不可控，只承诺配置不可变）

## Decisions

### D1. 快照形态：自有字段冻结 + pinned_refs + ref_manifest（含内容哈希）

`agent_versions` 用三个 JSON 列：`config_snapshot`（自有字段整体冻结）、`pinned_refs`（`[(workflow_id, workflow_version)]`）、`ref_manifest`（未版本化资源的 `[(resource_type, resource_id, version=null, content_hash)]`）。不逐字段建列——AgentModel 每加字段无需迁移快照 schema；diff 在 JSON 上实现。快照含 `schema_version` 便于前向兼容。

备选：浅快照（无清单）被否——"不可变契约"形同虚设；深拷贝资源被否——存储/同步爆炸且与 14.x 冲突。内容哈希取业界收敛做法（OpenClaw revision hash / deer-flow 内容审计 / CubePlex skills-lock）。哈希语义：对资源的**定义/元数据**做规范化 JSON 后 sha256；v1 覆盖 skills 与 tools；KB 只记 id（语料是可变数据，等同 Dify"记连接不拷数据"；KB 元数据哈希可后续补，不阻塞）。工作流 pin 规则：提交时取其 `published_version`，未发布过取最高版本。

### D2. 草稿即活行，不引入 draft 指针

`agents` 表不加快照类列；唯一新增设置列 `evaluation_gate`（镜像 workflow，门禁配置属设置而非版本内容）。提交 = 读活行写快照行。备选（Dify 式独立草稿副本）被否——多一份状态要双向同步，studio 现有编辑路径零改动。

### D3. 显式提交，非每次保存自动版本化

对齐目录「提交版本」按钮与产品型平台惯例（Dify/Palantir 显式、AgentCore 自动但噪音大）。徽章"存在未提交变更"由活行与最新快照的 content-hash 比对推导，不落列。

### D4. 版本号为 agent 内单调递增 int

对齐 1.1.9 与目录"已发布 v3"措辞。备选 semver（Palantir git tag）被否——低码用户无语义化版本诉求。

### D5. Channel 实体 = 别名间接层，bind_mode 两模式

`channels` 表：`type/name/agent_id/bind_mode/config`。`published` 追踪最新发布（AgentCore DEFAULT 语义），`pinned` 钉死 vN（命名别名语义）。发布只改 `agents.published_version` 指针，渠道按调用时读取解析——无发布扇出写。备选：仅支持 pinned 直接绑定被否（"发布 v4 后旧渠道动不动"无解）；AgentArts 式"版本物化为 Runtime 实例"被否——过重，Hecate 进程内解析即可。

### D6. 渠道寻址新入口，既有直连入口保持 live

新增 `POST /v1/channels/{channel_id}/chat/completions`（OpenAI 兼容、支持流式，复用 `_process_chat` 通路，preloaded 配置来自解析结果）。既有 `/v1/agents/{agent_id}/chat/completions` 行为不变（解析活行），定位为未版本化直连面——避免对存量 API 用户的 BREAKING。`X-Agent-Version` 头与 `?version=` 参数在渠道入口上做显式版本调试。

### D7. 解析接缝落在 `studio/agents/` 的 `AgentVersionService`

`resolve(agent_id, version=None) -> ResolvedAgentConfig`（活行 or 快照 + pin 的工作流版本号）。渠道、IM gateway、工作流执行经函数级引用消费（既有跨域约定）。命名遵守约定：领域服务用普通名词，不用 `Port`。

### D8. IM 路由：按渠道行查找，逐消息解析，无渠道行显式拒绝

查找键 = `(channel_type, provider_name)`（v1 每类型单实例，provider 名来自 `im_channel_names()`；config JSON 预留 app_id 供多实例）。解析发生在每条消息处理时（改绑免重建会话）。无渠道行 → 拒绝 + 显式错误日志（含实例标识），移除零 UUID 兜底与 `enqueue(agent_id=None)`。`packages/channels/*` 插件不改（修复在 core gateway 层）。

### D9. 工作流运行时：执行上下文携带版本选择器

`_load_workflow_graph` 增加 `{latest_for_studio, published_preferred}` 选择器：studio 试跑传 `latest_for_studio`；其余路径 `published_preferred`（`published_version` 为空回落最新，保存量兼容）。单次执行内只解析一次（执行中发布不影响进行中执行）。修复后 1.1.9 的"回滚=新建版本"自然收敛为"回滚产生草稿，发布才上线"。

### D10. 门禁复用 7.3a 形态，v1 默认关

`agents.evaluation_gate`（JSON，nullable）复用 workflow 门禁配置 schema 与求值逻辑；warn/require 语义一致。发布响应附带评估报告（同 workflow `evaluation_report` 先例）。

## Risks / Trade-offs

- [未 pin 资源实时解析削弱"不可变"感知] → 文档与 UI 统一措辞"配置不可变"；漂移查询 + 版本详情警示（内容哈希比对）让偏差可见
- [IM BREAKING：静默兜底变显式拒绝] → 运维 runbook + release notes 明确"升级前先为 IM 实例建渠道行"；错误日志含实例标识便于定位
- [工作流运行时 BREAKING：非 studio 路径改跑已发布版] → 未发布过的工作流行为不变（回落最新）；已发布且继续改草稿的部署需在 release notes 标注
- [config_snapshot 前向兼容] → 快照带 `schema_version`；解析器对未知字段忽略不报错
- [逐消息渠道查找的 DB 开销] → 单主键查询，量级可忽略；如需可后加进程内缓存（不改行为）
- [provider 单实例假设] → config 预留 app_id，多实例 IM 到来时查找键扩展为三元组，渠道行无需迁移

## Migration Plan

1. Alembic 迁移：建 `agent_versions`、`channels` 两表 + `agents.evaluation_gate` 列（纯增量，无数据回填）。
2. 部署后存量 Agent 零行为变化（无快照、无渠道行）；IM 部署需按 runbook 为每个实例建渠道行后才能继续服务。
3. 工作流运行时变化随代码生效：未发布工作流不受影响；发布过的工作流即刻切换为已发布语义。
4. 回滚策略：代码回退 + `alembic downgrade`；两表数据可弃（版本与渠道可重建）。

## Open Questions

（无——原"前端仓库位置"疑问已核实：studio 前端在本仓 `web/`，studio UI 作为 PR-4 纳入任务拆分。）
