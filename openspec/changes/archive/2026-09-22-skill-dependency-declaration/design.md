# Design: Skill Dependency Declaration

## Context

Hecate 已有 5.9 / 5.9-enh / 5.9d 三层 skill 基础设施:CRUD + provider 优先级 + 版本快照与 content_hash。1.3.20 agent versioning 的「提交版本」流程已实现「直接列出的 skill」的 pin 至 `(name, skill_id, provider, version, content_hash)` 五元组。本 change 在此之上扩展两点:

1. SKILL.md frontmatter / plugin.json namespace 接受 `requires` 字段,平台持久化与校验。
2. agent 版本提交流程新增闭包 walk,把传递闭包中的全部 skill pin 进 ref-manifest。

详见 `proposal.md` 的 Why 与 What Changes;`openspec/specs/skill-dependency-declaration/spec.md` 与各 modified capability 的 spec 提供完整 requirement。

## Goals / Non-Goals

**Goals:**

- 创作期校验(缺失依赖 + 环检测 + 跨 source 拒绝),422 含结构化路径。
- 绑定期闭包 walk 原子化(全成功才写入 ref-manifest,任一失败整体回滚)。
- 闭包中所有节点(直接 + 隐式)沿用既有五元组 pin 形状,不引入新条目类型。
- plugin.json namespace 扩展复用既有 Hecate namespace 解析路径,与 `type` / `entry` / `permissions` 等并列。
- CLI 诊断命令覆盖直接 / 闭包 / 反向查询,可调试。

**Non-Goals(写明边界,防止 scope 蔓延):**

- 不引入运行时 range 求解器(声明松散、绑定期冻结)。
- 不引入 trust 升级、跨 source require 反向通道。
- 不引入 bundle skill(空壳 skill) — 推到 5.5d。
- 不引入 semver 范围(v1 只允许 name + 可选 provider 提示)。
- 不变更 SkillLoader 运行时路径 — 5.9d pinned 加载语义覆盖隐式依赖。
- 不变更 schema 形态:`SkillModel` 仅新增 `requires` 列;不新增关联表。

## Decisions

### D1. 闭包 walk 用 DFS + 记忆化,深度上限 32

闭包解析器对每个直接 skill 启动一次 DFS,沿 `requires` 边展开,过程中:

- 维护 `visited` 集合检测环
- 维护 `memo[(name, provider)] = resolved_tuple` 缓存,同一节点不重复解析
- 维护 `depth` 计数器,达到 32 即报错(防止误用导致栈溢出 / 工作量爆表)

**替代方案**:BFS。DFS 更适合环检测,且 Hecate skill 闭包一般浅(< 5 层);BFS 需额外的环检测 pass。DFS 已选择。

### D2. `SkillModel.requires` 列类型为 `JSON`,默认 `'[]'`

alembic 迁移新增 `requires JSON NULL DEFAULT '[]'` 列。`NULL` 与 `[]` 在语义上等价于「无依赖」,但保留 `NULL` 区分「尚未迁移的存量行」(理论上一迁移就 `[]`,实际不区分,但 SQL 层可识别)。

**替代方案**:JSONB。PostgreSQL 支持 JSONB 索引,但当前不需要按 `requires` 内容查询(只是写入 + 全量读)。JSON 列更可移植(SQLite / PostgreSQL 一致语义)。

### D3. 校验与闭包解析为独立模块,非耦合到 service 层

新增模块位置:

- `src/hecate/runtime/skill/dependency_validator.py` — 创作期校验(缺失依赖 / 环 / 跨 source),输入 skill 实例 + workspace_id,返回结构化错误列表
- `src/hecate/runtime/skill/dependency_resolver.py` — 绑定期闭包 walk,输入直接 skill 列表 + workspace_id,返回闭包五元组列表或 `ClosureError`

API 层(`src/hecate/api/skill.py`)、agent versioning 提交层(`src/hecate/runtime/agent/versioning.py`)、plugin ingestion 层(`src/hecate/plugin/agent_plugins.py`)分别调用这两个模块。

**替代方案**:把校验塞进 `SkillService`。`SkillService` 已经是 CRUD 中心,再叠加图算法职责违反 SRP;独立模块便于单元测试与未来复用(例如未来知识库 / 工具的依赖声明)。

### D4. API 422 错误体使用既有 `error_code` 枚举

沿用既有 API 错误响应规范(详见 `cli` 与 `api` spec 的既有 422 错误形状),新增枚举值:

- `dependency_missing`
- `dependency_cycle`
- `cross_source_denied`
- `invalid_requires_syntax`
- `self_reference`

422 响应体新增 `dependency_path` 数组字段,记录从出错 skill 出发的完整依赖链(环检测场景按环遍历顺序)。

**替代方案**:自由文本错误。结构化 error_code + path 让 API 客户端可以编程处理;CLI / Studio / 第三方插件均可消费。

### D5. plugin.json requires 复用既有 Hecate namespace 解析路径

`extensions.io.github.xueyufish.requires` 数组沿用既有 Hecate namespace 解析器(`agent-plugins-ingestion` 已对 `extensions` 做 namespace 解析)。新增字段验证逻辑:

- 接受数组,元素 shape 与 SKILL.md frontmatter 一致(`{name, provider?}`)
- 在插件导入时,plugin-level requires 与包内每个 SKILL.md frontmatter requires 取并集,namespace 优先(去重后等价)
- 任一 requires 不入图标;缺漏 / 跨 source / 环存在,整个插件导入被拒

**替代方案**:plugin-level requires 用独立 schema。没必要 — 元素形状一致,共享校验模块(D3)。

### D6. CLI 诊断命令复用既有 `typer` 子命令结构

`hecate skill deps` 与 `hecate agent version --skill-closure` 沿用既有 `typer` 子命令注册模式(`cli` spec 已规范)。新增命令:

- `hecate skill deps <name> [--graph|--closure|--reverse]` — 通过 `GET /api/skills/{id}/deps?mode=graph|closure|reverse` 调用
- `hecate agent version <v> --skill-closure` — 通过 `GET /api/agents/{id}/versions/{v}/skill-closure` 调用

**替代方案**:CLI 端直接调数据库。CLI 是 thin client,所有数据走 API(与既有命令一致)。

### D7. 反向查询(`--reverse`)包含传递反向

`--reverse` 默认返回所有直接 + indirect 引用该 skill 的 skill。提供 `--reverse-direct` 标志返回直接反向。

**替代方案**:仅直接反向。传递反向对调试更有价值(「我改了 pdf-utils,谁会被影响?」);直接反向用 `--reverse-direct` 退路。

### D8. plugin 卸载的 dangling 标记在 plugin uninstall handler 内完成

`agent-plugins-ingestion` 的卸载 handler(已有)在事务内:

1. 删除 plugin-sourced skill(`DELETE WHERE plugin_id = P`)
2. 查询所有 `SkillModel.requires` JSON 中含目标 skill id 的 user / project 行
3. 在 `SkillModel.metadata` (JSON) 中写入 `{"dangling": {"reason": "plugin_uninstalled", "missing_deps": [...]}}`
4. 卸载完成

**替代方案**:新增 `dangling` 列。metadata JSON 已足够;新增列成本不抵收益。

### D9. 闭包 walk 的事务边界 = 整个 agent version 提交

agent 版本「提交版本」流程原有范围(自有字段写入 + 直接 skill pin + ref-manifest 持久化)已在一个 DB 事务内。闭包 walk 在该事务内:

1. 闭包解析(resolver)
2. 全部节点五元组写入 ref-manifest
3. 任一节点解析失败 → 整个事务回滚

**替代方案**:闭包 walk 单独事务,ref-manifest 写入另一事务。跨事务无法保证原子性,且 5.9d 既有「提交版本」已是单事务,直接纳入即可。

## Risks / Trade-offs

- **[Risk] 闭包 walk 在超深 `requires` 图下栈溢出** → Mitigation:深度上限 32,workspace 内 skill 数 < 1000 性能可接受,越界返回 422 「dependency_depth_exceeded」。
- **[Risk] 既有 skill 升级时 `requires` 字段为 NULL** → Migration:`'[]'::jsonb` DEFAULT,迁移完成即非 NULL,代码层 NULL ≡ `[]`。
- **[Risk] 校验 / 闭包解析在并发写入下出现竞态(skill 在校验通过后被软删)** → Mitigation:绑定期 walk 在事务内 + `SELECT ... FOR UPDATE`(沿用 1.3.20 既有并发控制);创作期校验失败由用户重试。
- **[Risk] `plugin.json` namespace 字段随 Agent Plugins 1.0 上游 schema 变更而被破坏** → 既有 namespace 解析器对未识别的子字段 warn + 继续,`requires` 是命名空间内字段,被 namespace 解析器吞掉,不影响 plugin.json 顶层 schema;沿用 5.5c 既有 fail-closed 行为。
- **[Trade-off] JSON 列 vs JSONB**:JSON 列在 SQLite 测试环境与 PostgreSQL 生产一致,迁移简单;若未来需按 `requires` 内容做高频查询(如「所有依赖 X 的 skill」),可切换到 JSONB + GIN 索引(v2 改造)。
- **[Trade-off] 不引入 bundle skill**:把"空壳 skill"压到 5.5d,意味着本 change 不表达 "装一个名字拉一串" 的产品形态;若产品上要快速实现该形态,需要 5.5d 单独 change 提供。

### Migration Plan

Phase 1 — Schema(可独立上线,无功能影响):

- alembic 迁移:`ALTER TABLE skill ADD COLUMN requires JSON NULL DEFAULT '[]'::jsonb`

Phase 2 — 校验与 API(可独立上线,功能反向兼容):

- `SkillModel.requires` 列读取 / 写入
- `dependency_validator` 模块
- `POST /api/skills` / `PUT /api/skills/{id}` / `POST /api/skills/import` 接受 `requires`,校验失败 422

Phase 3 — 绑定期闭包:

- `dependency_resolver` 模块
- `agent_versioning` 提交流程扩展闭包 walk

Phase 4 — plugin.json namespace requires:

- `extensions[io.github.xueyufish].requires` 解析
- 卸载 handler 写 dangling metadata

Phase 5 — CLI 诊断命令:

- `hecate skill deps` 子命令
- `hecate agent version --skill-closure` 标志

**Rollback**:

- Phase 1 schema 是 additive(可空 JSON 列 + 默认值),`DROP COLUMN` 即可回滚。
- Phase 2-5 是纯 Python 代码,revert commit 即可;存量 `requires=[]` 行不受影响。

**No feature flag needed**:旧 skill 无 `requires` 字段,行为完全等价于「无依赖」;新字段 strict 增量,无 BREAKING。

## Open Questions

- 闭包 walk 是否需要并行化(workspace skill 数 > 5000)?v1 不需要,串行 DFS 在 ~1000 节点规模下 < 10ms;后续若出现性能瓶颈可加并行版本。
- `--reverse` 是否要支持按 plugin 维度过滤(`--reverse --plugin <id>`)?不在 v1;v2 视产品反馈追加。
- 跨 workspace 依赖未来要不要支持?`skill-provider-registry` 当前都把 workspace 作为隔离边界;若未来要支持跨 workspace 共享,需要新增 source 类型 `shared`,不在本 change 范围。