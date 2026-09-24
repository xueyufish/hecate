# Proposal

## Why

当前 skill 只有两条进入 agent 上下文的路径：显式绑定（`agent.skills`）和 `auto_load`。一个未绑定的 workspace skill 对模型完全不可见，即使模型猜到名字也会被 `SkillNotAdvertisedError` 拒绝。5.9c（Skill Auto-Detection）让 workspace 内符合条件的 skill 无需手动关联即可被模型发现与按需加载——这是 feature catalog 中 5.9c 的定义。触发判定采用"LLM 读 description 自选"，无需检索基建；Hecate 已有的渐进披露（L1 catalog + `load_skill`）正是该模型的地基，本 change 只需拆掉"绑定墙"。

## What Changes

- **发现池（discovery pool）**：workspace + bundled（零 UUID）域内所有 `model_invocable=true`、未删除、所属 plugin 已启用的 skill（经 precedence 归并同名）成为模型可发现对象。`provider='user'` 的 skill 不进发现池（"成员个人"语义与 workspace 级共享冲突，见 design）；显式绑定与 `auto_load` 路径不受影响。
- **三层开关**：全局 settings `SKILL_DISCOVERY_ENABLED`（默认 `false`，镜像 `SKILL_PROGRESSIVE_DISCLOSURE` 灰度模式）→ workspace 级策略（`WorkspaceModel.settings`）→ per-agent `skill_discovery_enabled`（`None`=跟随 workspace，`false`=显式退出，满足封闭集合规需求）。
- **trust tier 下限**：workspace 可配，默认 `community`（即不过滤）；文档标注真实扫描依赖 5.13a。
- **L1 catalog 预算与排序**：`DEFAULT_CATALOG_TOKEN_BUDGET` 1000 → 2000 tokens，条目格式不变；超预算时按 绑定 > auto_load > trust tier（official/trusted > community）> provider rank（project > user > bundled）> 使用次数降序 > 名称（确定性兜底）选择。
- **L2 advertised 检查放宽**：discovery 生效时，`load_skill` 的 advertised 集合从 `agent.skills` 扩展为 绑定 ∪ 发现池。
- **激活作用域维持 run-scoped 不变**。
- **遥测**：`SkillUsageEventModel` 新增 `detected_via`（`bound` / `auto_detected`）；自动发现加载遇到未满足 `requires` 时服务内容但记录告警事件（5.9e 的 requires 仍是绑定期契约，无运行时求解器）。
- **promote-to-bind API**：新增端点把自动发现的 skill 追加进 `agent.skills`（冻结路径的第一步，随后走既有版本 commit）；studio UI 与 CLI 命令延后。
- **活面叙事（文档化，非代码）**：自动发现的 skill 运行时活解析，不进 ref_manifest pinning、不进漂移报告；绑定 = 冻结面，发现 = 活面，promote-to-bind 是两者之间的冻结路径。

**Deferred（不在本 change 范围）**：`search_skills` 长尾词法检索工具（spec 预留方向）；description 触发质量 lint 与评估线束；skill 上下文成本与使用率观测；suggest-binding 闭环；`user` provider 成员级隔离（独立安全 feature）。

## Capabilities

### New Capabilities

- `skill-auto-detection`: 发现池定义与治理（三层开关、trust 下限、`user` provider 排除）、catalog 预算与选择策略、L2 加载放宽、激活语义、遥测来源标记、依赖告警、promote-to-bind API。

### Modified Capabilities

- `skill-loader`: L1 catalog 构造从"绑定 ∪ auto_load"扩展为"绑定 ∪ auto_load ∪ 发现池"；catalog 预算默认值调整；`load_skill_content` 的 advertised 检查放宽；usage 事件写入 `detected_via`。
- `skill-api`: "Manage agent-skill associations" 增加 promote-to-bind 端点（自动发现 → 显式绑定的冻结路径）。

## Impact

- **代码**：`src/hecate/tools/skill/loader.py`（池查询、预算排序、advertised 检查、遥测）、`src/hecate/models/agent.py`（`skill_discovery_enabled` 列）、`src/hecate/models/evolution.py`（`detected_via` 列）、`src/hecate/models/workspace.py`（settings 内策略键，无迁移）、`src/hecate/core/config.py`（`SKILL_DISCOVERY_ENABLED`）、skills API（promote 端点）、`src/hecate/runtime/agent_execution_port.py` 与 `src/hecate/studio/workflows/execution_service.py`（如 loader 需传入策略上下文）。
- **数据库**：alembic 迁移——`agents.skill_discovery_enabled`（nullable boolean）、`skill_usage_events.detected_via`（nullable string）。
- **兼容性**：全局默认关闭，升级后行为零变化；开启后 L1 catalog 预算增大但仍有硬上限，`load_skill` 拒绝面只缩不增。无 BREAKING。
- **安全**：发现池放大 skill description 的作用半径（跨 agent 传播载体）；以三层开关 + trust 下限 + `model_invocable` 隐藏语义（hide-not-block，已有）+ 5.13a 扫描（延后依赖）分层缓释。
