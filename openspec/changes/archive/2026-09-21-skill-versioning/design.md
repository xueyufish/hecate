# Design

## Context

机制模板与代码现状（动机见 proposal.md Why）：

- 1.3.20 的 `AgentVersionService`（`src/hecate/studio/agents/versioning.py`）是本机制的直接模板：`AgentVersionModel`（单调 int + `config_snapshot/pinned_refs/ref_manifest/schema_version/content_hash`）、commit/publish/rollback/diff/delete、`resolve()` 接缝、漂移查询。skill 版本化复用其存储形态与 diff 机制，但**不引入 publish 指针**（见 D1）。
- `SkillModel` 已带 5.9-enh 预埋：`content_hash`（`hecate.core.canonical_hash`，字段集 = ref_manifest 的 skill 字段集：name/instructions/allowed_tools/scripts/references）、`provider` 分类、同名跨 provider 共存（unique 索引已按 provider 拆分）。
- 已知缺陷：`AgentVersionService._build_ref_manifest` 用裸 dict 推导 `{s.name: s}` 取同名胜者，5.9-enh 同名共存后胜者取决于存储序，违反 gotchas 中"同名消费必须走 `provider_registry`"的契约——本 change 必须顺带修复（D6）。
- `SkillLoader` 按 name + workspace（含零 UUID 域）查询后经 `resolve_precedence_map` 仲裁；L1 catalog（name+description）与 L2 内容块（description+instructions）都消费 `description`，`max_tokens` 决定截断——二者必须进快照才能忠实还原注入内容。
- 自演进发布点在 `src/hecate/studio/self_evolution/review.py`（写 `learned_run_id`），发布前已有 skill-evolution-gate 审查。
- 冻结边界、回滚写回、删除约束、plugin 排除、evolution 自动成版、最小 UI 六项决策已由用户确认（见 proposal What Changes）。

## Goals / Non-Goals

**Goals:**

- skill 级不可变版本快照全生命周期，机制与 1.3.20 同构（降低认知与代码复用成本）。
- agent commit 的 ref_manifest skill 条目升级为五元组 pin，pinned 内容在快照解析路径上真实生效（"不破坏依赖 agent"从检测能力升级为冻结能力）。
- 同名仲裁契约在全链路一致（loader、ref_manifest、pin 解析同走 precedence）。
- 全程 additive、非破坏；存量 agent 快照与未 pin 条目行为零变化。

**Non-Goals:**

- publish 指针 / 运行时 draft-published 切换 / 发布门禁（skill 无外部契约消费者）。
- plugin skill 版本化（生命周期归插件包）。
- 版本约束解析、`requires` 依赖声明（5.9e 另行立项，仅保证本机制的版本语义可被其消费）。
- 外部 registry 连接器、semver 对外格式（内部单调 int；frontmatter `metadata.version` 维持作者自述字符串，不参与解析）。

## Decisions

### D1. 无 publish 指针，浮动模型 + 消费端 pin

活行即被服务对象，冻结发生在 agent commit 时的 manifest pin。备选：完整镜像 1.3.20 的 draft/published 二态——被否：skill 无外部契约消费者（agent 才有渠道契约），全局 publish 开关会改变所有 skill 作者的工作流而无对应收益。推论：回滚必须写回活行（D4），否则是无操作。

### D2. 快照表 `skill_versions`，快照列冻结七字段，治理字段活解析

新表镜像 `agent_versions`：`skill_id` FK、`version`（skill 内单调递增）、`name`、`change_summary`、`config_snapshot`(JSON：name/instructions/allowed_tools/scripts/references/description/max_tokens)、`schema_version`、`content_hash`（canonical_hash 5 字段集，与 ref_manifest 可比）、`learned_run_id`（nullable，evolution 自动成版关联）、`created_by`、`workspace_id`、软删列。索引 `(skill_id, version)` unique + `(workspace_id, deleted)`。备选：内容寻址表（无 FK，按 workspace+name+provider 键）——被否：skill 行删除后 FK 可保留历史归属信息，且与 agent_versions 形态一致；`skill_id` + 快照自包含（不 FK 回活行取内容）已满足"源删除后 pinned 仍可解析"。快照字段是哈希字段的超集（description/max_tokens 冻结但不进哈希），完整性靠行不可变。

### D3. 版本服务与 API 镜像 1.3.20

`src/hecate/tools/skill/versioning.py`：`SkillVersionService`（commit/list/get/update/diff/rollback/delete/status），diff 复用 deepdiff（与 agent 相同的 ImportError 降级路径）。API 挂在既有 skill 路由下：`POST /api/skills/{id}/versions`（commit）、`GET .../versions`、`GET .../versions/{version}`、`PATCH .../versions/{version}`（元数据）、`POST .../versions/{version}/rollback`、`DELETE .../versions/{version}`、`GET .../versions/{v1}/diff/{v2}`；skill list/detail 响应附 `latest_version`、`has_uncommitted_changes`（活行 content_hash vs 最新快照，无快照恒 true）。commit 请求对 `provider=NULL` 行 409 拒绝。删除约束：目标版本被任一非删除 `agent_versions.ref_manifest` 条目以 `skill_id+version` pin 时 409（引用检查按 skill_id 匹配，避免同名歧义）。

### D4. 回滚 = 新版本 + 写回活行（同事务）

`rollback_to_version` 在同一事务内：以目标快照复制新版本行（`change_summary="Rollback to version N"`，同 agent 模板）+ 将七个冻结字段写回活行 + 重算活行 `content_hash`。写回后活行哈希 = 新版本快照哈希 → 脏标记自然归零，无需特殊分支。已 pin 的 agent 不受影响（pin 指向旧版本行，快照自包含）。

### D5. agent manifest skill 条目升级五元组；pinned 解析走 loader 新路径

- `_build_ref_manifest` 的 skill 条目改为 `{resource_type: "skill", name, skill_id, provider, version, content_hash}`：活行经 `resolve_precedence_map` 仲裁（顺带修复存储序缺陷），`version` = 该行 `skill_versions` 的 max(version)（无则 null）。plugin 行（provider NULL）：保持 `version=null`，`skill_id/provider` 照实记录。tools/KB 条目结构不变。
- `AgentVersionService.resolve()` 返回的 `ResolvedAgentConfig.ref_manifest` 已携带条目；skill 内容解析新增 manifest-aware 入口：`SkillLoader.load_skill_content` 增加可选 `ref_manifest` 参数（或平行方法 `load_pinned_skill_content`），当条目 `version` 非 null 时按 `(skill_id, version)` 取版本快照并按快照内容渲染（`max_tokens` 取快照值）；`version=null` 走现路径。运行时调用点在快照解析路径传入 manifest，活行（studio/草稿）路径不传——studio 试跑继续看活内容，与 1.3.20 "studio 编辑器用草稿解析"的既有语义一致。
- 漂移查询：`version` 非 null 条目跳过；旧格式条目（无 version 字段）按 `version=null` 处理——存量快照零迁移。
- `_ref_manifest_diff` 对五元组条目按 `skill_id` 键控比对（version/content_hash 变化即 `content_changed`）。

### D6. 同名仲裁统一入口

loader、`_build_ref_manifest`、pinned 回退（若有）全部经 `resolve_precedence_map`；ref_manifest 构建查询与 loader 同款（workspace + 零 UUID 域 + plugin enabled 条件），保证"提交时记录的行 = 运行时会服务的行"。

### D7. evolution 自动成版挂发布点

`self_evolution/review.py` 发布 learned skill 的同事务内调用 `SkillVersionService.commit(skill_id, created_by=None(系统), learned_run_id=run_id)`；提交失败不阻断发布（best-effort，记 warning）——版本是审计增强，不是发布的前置条件。审查仍由 skill-evolution-gate 先行（现有流程不变）。

### D8. UI 最小面

skill 编辑页新增版本 tab：版本列表（版本号/说明/时间/创建者）+ 「提交版本」按钮 + 单版本「回滚」；diff 页复用 agent 版本化的 diff 展示组件；skill 列表行加 `latest_version` 徽标与脏标记点。不做独立管理页。

## Risks / Trade-offs

- [loader 热路径回归] → pinned 路径仅在快照解析（非 studio）时激活，查询为 `(skill_id, version)` 主键级；活行路径代码不变。补确定性单测（同名乱序、plugin disabled、pin 与活行并存）。
- [五元组条目破坏旧 diff/漂移消费方] → `_ref_manifest_diff` 与 `version_drift` 同 change 内适配并保留旧格式分支；存量快照行为由兼容 scenario 钉死。
- [回滚写回与并发编辑竞争] → 写回走与 update 相同的 API 层串行化（select-then-write），并发窗口与现状一致，不放大；写回前 re-check 活行 `content_hash` 与提交时一致，不一致则 409 提示先刷新（乐观锁语义，仅回滚路径引入）。
- [skill 行删除后 orphan 版本堆积] → 版本随 skill 生命周期保留是刻意取舍（pinned 解析依赖）；删除 skill 的 API 响应附保留版本数说明。真正的版本 GC（无 pin 无源）留待后续 change。
- [`skill_id` FK 与软删语义] → FK 不设数据库级 CASCADE；skill 软删不动版本行，`skill_versions` 查询一律带 `~deleted` 于 skill 侧判断（join 场景），版本自身删除用自身软删列。
- [evolution 自动成版失败静默] → best-effort + warning 日志；审计缺口可在 evolution 侧的发布事件中看到（仍有迹可循）。

## Migration Plan

1. Alembic 迁移：建 `skill_versions` 表 + 索引（纯 additive，无数据回填、无列变更、无索引重建）。
2. 部署顺序：先迁移后发代码（旧代码不识别新表，无影响；新代码要求表存在）。
3. 回滚：代码回滚无前置；表保留无害，降级脚本可直接 drop。

## Open Questions

（无——冻结边界、回滚写回、删除约束、plugin 排除、evolution 纳入、UI 范围六项决策已确认；其余实现细节在 tasks 阶段按本 design 落地。）
