## 1. PR-1 版本化内核：存储 + 生命周期 + 解析接缝

- [x] 1.1 Alembic 迁移：建 `agent_versions` 表（config_snapshot/pinned_refs/ref_manifest 三个 JSON 列 + 元数据列 + (agent_id, version) 唯一索引）、`agents.published_version` + `agents.evaluation_gate` 列
- [x] 1.2 ORM 与 Pydantic schema：`AgentVersionModel`、Commit/Update/Read/Detail/Diff/Status/Drift schema（新文件 `models/agent_version.py`，命名对齐现有约定）
- [x] 1.3 `AgentVersionService`：提交（读活行写快照，构建 pinned_refs 与 ref_manifest，skills/tools 内容哈希）、版本列表/改名/发布说明、发布（含门禁 warn/require 挂钩与评估报告）、回滚（新建版本）、diff（自有字段 + 引用变化）、删除约束（已发布禁删；被 pin 禁删在 PR-2 接通）
- [x] 1.4 `resolve(agent_id, version=None)` 解析接缝：返回统一 ResolvedAgentConfig（草稿 or 快照 + pin 的工作流版本号 + 未 pin 资源实时解析），含漂移查询接口
- [x] 1.5 studio REST 路由：`/api/agents/{id}/versions` 系列（列表/提交/详情/改名/删除/漂移）+ publish/rollback/diff/published/version-status；`evaluation_gate` 经 agent update 端点配置（镜像 workflow 先例）
- [x] 1.6 测试：生命周期全链路（in-memory SQLite + Stub 惯例，见 tests/AGENTS.md）——提交不可变、pin 规则、发布指针、门禁 warn/require、回滚语义、删除约束、解析接缝、漂移检测
- [x] 1.7 跑四项检查（ruff check ✓ / ruff format ✓ / mypy 0 错误 ✓ / 相关层 pytest ✓，存量失败经干净树对照确认与本次改动无关）

## 2. PR-2 Channel 实体 + API 渠道

> 实现说明：2.x 与 3.x 在调用路径上原子耦合（webhook 贯穿 `agent_version` 依赖 execute 的对应形参），两者合并为一个原子提交。

- [x] 2.1 Alembic 迁移：建 `channels` 表（type/name/agent_id/bind_mode/pinned_version/config/status）
- [x] 2.2 ORM 与 schema：`ChannelModel`（`models/channels.py`）+ Create/Update/Read schema；type 枚举含 api/im 接线与 embed/webhook 占位（创建标记 unwired）
- [x] 2.3 渠道 service（`channel/publishing.py`）+ CRUD 路由：创建约束（目标 agent 必须已有已发布版本；im 必须带 config.provider）、bind_mode 切换/改绑（pinned 目标版本存在性校验，published 切换清空 pinned_version）
- [x] 2.4 渠道寻址调用入口：`POST /v1/channels/{channel_id}/chat/completions`（OpenAI 兼容 + 流式，快照构造瞬态 AgentModel 复用 `_process_chat`），按 bind_mode 经 `AgentVersionService.resolve` 解析；支持 `X-Agent-Version` 头与 `?version=` 显式调试（未知版本 404、非整数 400）
- [x] 2.5 版本删除约束接通：删除版本前检查渠道 pinned 引用（409 VERSION_PINNED，错误信息含绑定渠道名）
- [x] 2.6 测试：published 跟随新发布、pinned 不受新发布影响、渠道调用执行冻结配置、直连入口行为不变、显式版本调试、创建/删除/改绑约束、disabled/unwired 不可调用
- [x] 2.7 跑四项检查

## 3. PR-3 IM 渠道路由 + 工作流运行时发布解析

- [x] 3.1 IM webhook 路由：按 `(type=im, config.provider)` 查找渠道行解析 agent 与 bind_mode（逐消息解析，改绑免重建会话）；无渠道行显式拒绝 + 结构化错误日志（200 + `ok:false`，避免 IM 平台重试风暴），移除零 UUID 兜底
- [x] 3.2 `IMMessageBus.enqueue` 携带解析出的 agent_id + agent_version（信封贯穿）
- [x] 3.3 工作流执行版本选择：`execute()` 新增 `agent_version/workflow_version/version_selector` 形参；`_load_workflow_graph` 支持 exact pin > `latest_for_studio` > `published_preferred`（未发布回落最新），单次执行内只解析一次；`agent_version` 时 persona/model/workflow 绑定来自快照（skills 仍实时解析）
- [x] 3.4 调用方接入：workflow 编辑器 test-run 走自身 latest 查询（不变）；时间旅行续跑保持"当前定义"（1.3.21 D8 既有设计）；其余生产路径默认 `published_preferred`；studio 会话内 agent 自有字段仍为活草稿
- [x] 3.5 测试：无渠道拒绝、published/pinned 逐消息解析、bus 信封贯穿 execute、工作流已发布/未发布/studio/pinned 四态解析、缺失 pin 版本报错
- [x] 3.6 跑四项检查（ruff ✓ / format ✓ / mypy 0 ✓ / 分层测试 + 相关层 pytest ✓）

## 4. PR-4 studio 前端（web/，Next.js）

- [x] 4.1 API client 扩展：`agentVersionsApi` + `channelsApi`（`web/src/lib/api-client.ts`）与 AgentVersion/ChannelEntry 等类型（`api-types.ts`）
- [x] 4.2 Agent 编辑器状态徽章：未提交 / 开发中 / 已发布 vN，"有未提交变更"由 version-status 接口推导（`components/agent/version-panel.tsx`）
- [x] 4.3 版本列表面板 + 「提交版本」对话框 + 发布/强制发布（门禁 409 提示）/回滚/diff 摘要/漂移警示
- [x] 4.4 渠道管理：渠道列表/创建（embed/webhook 占位禁用态、im 必填 provider）/pinned 版本内联改绑/启停/删除/API 调用地址复制（`components/agent/channel-manager.tsx`）
- [x] 4.5 前端测试（vitest，9 文件 64 用例全过）+ `npm run lint` 0 错 + `npm run build` 成功（顺带修复 3 处存量 lint/type 错误：controller-config 未用变量 ×2、evaluation 面板裸 `Array` 泛型、dataset versions 元组转型）

## 5. 收尾

- [x] 5.1 运维 runbook（`docs/operations/agent-versioning-runbook.md`）：IM 实例建渠道行步骤（升级前置）；两处 BREAKING 语义说明（IM 拒绝未配置实例、工作流非 studio 路径改跑已发布版）+ 回滚步骤
- [x] 5.2 全量 `python -m pytest tests/ -q` 通过（PR-2+3 提交钩子在最终后端代码上全绿；PR-4 仅前端/文档）+ 后端四项检查通过 + 前端 vitest 64/64、lint 0 错、build 成功
