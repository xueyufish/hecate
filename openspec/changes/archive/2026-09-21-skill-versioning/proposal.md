# Proposal

## Why

Skill 活行编辑即时生效且无历史：一次错误编辑会立即改变所有绑定 agent 的注入内容（L1 catalog、auto_load 全量注入、L2 按需加载），既无法回滚，也无法回答"那次运行用的是哪个版本的内容"。1.3.20 Agent Versioning 已把 skill 记为未版本化资源（`version=null, content_hash`），漂移只能检测、不能冻结，"safe skill updates without breaking dependent agents"（feature-catalog 5.9d）缺最后一块。同时自演进管线（skill-evolution loop）会程序化改写 skill 内容，无版本意味着不可审计、不可回滚的 AI 变更。5.9-enh（provider registry）已预埋与本机制可比对的 `content_hash` 和可复用的 `resolve_by_precedence`，本 change 是既定栈 5.9-enh → 5.9d → 5.9e 的第二步，也是 5.9e（依赖声明）的地基。

## What Changes

- 新增 `skill_versions` 表与 SkillVersionService：commit / list / get / diff / rollback / delete，镜像 1.3.20 的机制形态（单调 int 版本号、不可变快照、schema_version）。
- 快照冻结边界：`name / instructions / allowed_tools / scripts / references / description / max_tokens` 进快照（content_hash 保持 5 字段集不变，与 agent ref_manifest 可比）；`auto_load / model_invocable / user_invocable / provider / trust_tier / metadata_` 保持活解析。存量 skill 不回填，首次 commit 产生 v1。
- 回滚 = 以目标版本内容新建版本**并写回活行**（浮动模型：无 publish 指针，活行即被服务对象；写回后活行哈希 = 最新快照哈希，脏标记自动归零）。
- 删除语义：软删 skill 行不级联删版本（快照自包含，pinned 解析不受源删除影响）；被任一非删除 agent 版本 manifest 引用的 skill 版本禁止删除。
- plugin 来源（`provider=NULL`）的 skill 排除在版本化之外（生命周期归插件包）。
- self-evolution 发布 learned skill 时自动 commit 版本（`created_by=system`、关联 `learned_run_id`，发生于 skill-evolution-gate 审查之后）。
- Agent 版本提交的 ref_manifest 中 skill 条目升级为 pin 五元组 `(name, skill_id, provider, version, content_hash)`：`version` 取提交时该 skill 的最新快照（无快照则维持 `version=null`）；`resolve()` 对快照解析提供 pinned 内容（loader 增加 manifest-aware 路径）；pinned 条目退出漂移检测，未 pin 条目维持现状。
- 修复 `AgentVersionService._build_ref_manifest` 的同名解析缺陷：改经 `resolve_precedence_map` 仲裁（修复 5.9-enh 同名共存后存储序决定胜者的未确定行为）。
- skill API 暴露版本端点与 `latest_version` / `has_uncommitted_changes` 状态；studio 最小 UI（版本 tab + 提交 + 回滚 + 列表，diff 复用 agent 版本化展示）。
- 同步修改 agent-versioning 主规格（skill 从"未版本化资源"清单移入可 pin 资源）；更新 feature-catalog 5.9d/5.9e 行与 roadmap 依赖链措辞（5.9e 重定义为 bind-time 闭包固化，另行立项）。

非目标（明确不做）：publish 指针与运行时 draft/published 切换；发布评估门禁；plugin skill 版本化；版本约束解析（归 5.9e）；外部 registry 连接器。

## Capabilities

### New Capabilities

- `skill-versioning`: skill 资产级不可变版本快照与生命周期（提交/回滚/diff/删除约束/漂移状态），快照冻结边界、回滚写回语义、evolution 自动成版、plugin 排除、agent manifest pin 与 pinned 内容解析。

### Modified Capabilities

- `agent-versioning`: ref_manifest 的 skill 条目从"未版本化资源（version=null, content_hash）"改为可 pin 资源（提交时 pin 到当时最新 skill 版本 + content_hash 五元组）；同名解析改经 precedence 仲裁；快照解析时 pinned skill 从版本行取内容；漂移检测对 pinned 条目不再适用。

## Impact

- **数据**：新增 `skill_versions` 表（Alembic 迁移，additive，无存量回填）。
- **代码**：`src/hecate/models/skill_version.py`（新）；`src/hecate/tools/skill/`（versioning service 新文件、loader 增加 manifest-aware 解析路径）；`src/hecate/studio/agents/versioning.py`（manifest 构建 + precedence 修复）；skill API 路由（版本端点）；studio skill 编辑 UI。
- **复用**：`hecate.core.canonical_hash`（哈希可比性）、`deepdiff`（版本 diff）、`hecate.tools.skill.provider_registry.resolve_precedence_map`（同名仲裁）。
- **规格**：agent-versioning 主规格修改（见 Modified Capabilities）；新 spec `skill-versioning`。
- **文档**：`docs/features/feature-catalog.md`（5.9d 行补强、5.9e 行重写）、`docs/features/roadmap.md`（5.9e 依赖链措辞）。
- **兼容性**：全程 additive、非破坏；无 API 行为变更，存量 agent 快照与未 pin 条目行为不变。
