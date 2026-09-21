# Design

## Context

现状(见 proposal.md Why):`SkillModel` 唯一索引 `(workspace_id, name, deleted, deleted_at)` 不允许同 workspace 同名共存;`SkillLoader._load_skills_by_names` 用 `workspace_id IN [ws, zero_uuid]` 一次查询,同名跨域时行序未定义、胜者未定义;调用权隐式分布(catalog/`load_skill` 面向模型,studio CRUD 面向用户,无字段);`SkillModel.origin`(String 1024)已被 5.5c 占用为**插件包来源 provenance**,与本次的 provider 分类是不同概念。1.3.20 的 `ref_manifest` 为 skill 计算内容哈希(字段集:name/instructions/allowed_tools/scripts/references,`canonical_hash` 位于 `src/hecate/studio/agents/versioning.py`)。

## Goals / Non-Goals

**Goals:**

- 同 workspace 跨 provider 同名共存 + 确定性 rank 阴蔽解析(project > user > bundled),修复今日未定义胜者。
- `model_invocable`/`user_invocable` 双开关落地到 loader 的模型可见面与用户显式面。
- `trust_tier`(含防提权)、`content_hash`(与 ref_manifest 字段集对齐)、kebab-case 语法 registry 化。
- 存量数据零行为回归:plugin ingestion 撞名拒绝、plugin 启用位门控、auto_load/预算/进度披露语义全部不变。

**Non-Goals:**

- 版本化(5.9d)、依赖/requires(5.9e 重定义后)、外部 registry 连接器(custom 仅预留枚举)、plugin skill 存储名命名空间化(仅派生显示名,后续 change)、真扫描实装(5.13a)。
- `unified-skill-registry`(SkillRef 异构解析)与 `skill-attribution`/`skill-evolution-*` 不动。

## Decisions

### D1. 新增 `provider` 列,不复用 `source` 或 `origin`

`source` 是所有权模式(CRUD 面),`origin` 已是插件包 provenance。provider 是解析维度,三者正交。备选:直接给 `source` 加 rank 语义——被否,`plugin`/`learned` 等值混入 rank 会把 ingestion 与自演进卷进解析竞赛,爆炸半径大。映射:`system→bundled`、`user→user`、`project→project`;`plugin` 行 `provider` 置 NULL(不参与 rank);`custom` 仅入枚举校验拒绝赋值。

### D2. 唯一性:双索引策略

- 主共存约束:`UNIQUE (workspace_id, name, provider, deleted, deleted_at)`——非 NULL provider 行一条一坑,跨 provider 同名合法。
- 补部分唯一索引:`UNIQUE (workspace_id, name, deleted, deleted_at) WHERE provider IS NULL`——精确保持 plugin 行的今日语义(同 workspace 同名至多一行,含两个不同插件包互撞仍拒绝)。

备选:单索引 `(workspace_id, name, provider)` 靠 Postgres NULL 互异放行 plugin 多行——被否,那会放松今日 plugin 撞名拒绝,违反"ingestion 行为不变"的范围边界。软删列继续参与(沿用今日 `deleted_at` 区分可重名机制)。

### D3. 解析:独立 registry 解析模块,loader 单查询 + 内存仲裁

新增 `src/hecate/tools/skill/provider_registry.py`:纯函数 `resolve_by_precedence(rows) -> row`(rank 表 `project=0 > user=1 > bundled=2`,稳定排序取首)。`SkillLoader` 改为一次查询同名全部 provider 候选行,内存仲裁后取用;`agent.skills` 名单与 `_query_auto_load_skills` 候选共用同一仲裁(auto_load 同名跨 provider 时同样只服务最高 rank,符合 spec "any consumer" 确定性要求)。备选:SQL 窗口函数裁决——被否,逻辑进 Python 可单测、可复用(5.9e 影响图将复用),且候选行数极小(每名字 ≤3 行)。

### D4. `canonical_hash` 共享化

`canonical_hash` 从 `studio/agents/versioning.py` 提升到共享基础设施层(如 `src/hecate/core/`),versioning 原地 re-export 保持兼容;skills 侧 import 共享版,避免 `tools → studio` 反向依赖。content_hash 字段集 = ref_manifest 字段集(name/instructions/allowed_tools/scripts/references,不含 description),保证 5.9d 的 drift/pin 可直接比对。

### D5. 调用策略执行点

- 模型面:L1 catalog 组装时过滤 `model_invocable=false`;`load_skill_content` 对其拒绝(复用 `SkillNotAdvertisedError` 语义,错误信息注明原因)。
- 用户面:v1 在 API 响应与 studio UI 标注;`user_invocable=false` 的显式入口裁剪(命令面)随最小 UI 一起做。
- 校验面:create/update schema 层拒绝 `auto_load=true ∧ model_invocable=false`(422)。
- 默认双 true,存量回填 true,现有行为零变化。

### D6. trust_tier 管理与防提权

列默认 `community`;bundled 行回填并常置 `official`;plugin 行继承所属包 tier(5.5c/5.5d 的包 tier,缺失时 community——精确映射细节若包 tier 枚举不一致,defer 到 tasks 摸底后定,不影响 spec 语义)。防提权:workspace 级 update 路径对 `trust_tier`/`provider` 422 拒绝;平台级 allowlist 用 settings 占位(`SKILL_TRUSTED_SOURCE_PATTERNS`,默认空 = 无平台来源可获 trusted,仅 bundled 为 official),为 5.13a/registry 连接器预留。

### D7. 存量迁移与回填

顺序:加列(全 nullable)→ 数据回填(`provider` 按 source 映射;`trust_tier` 按 D6;双开关 true;`content_hash` 用共享 `canonical_hash` 现算)→ 索引重建(先建新,后删旧)。skill 表行数小,单迁移可完成;回填在同一事务内。回滚:降级脚本先合并跨 provider 同名(保留最高 rank 行),再恢复旧索引——迁移文档中写明该前置。

### D8. UI 最小范围

skill 列表加 provider/trust_tier 徽标,编辑页加双开关 toggle(含 auto_load 冲突的前端提示);不做独立 registry 管理页。

## Risks / Trade-offs

- [loader 热路径回归] → 候选行 ≤3/名字,内存仲裁 O(n);保留既有查询形态,仅加 provider 列选择;补确定性仲裁单测(乱序输入)。
- [API 409 放宽被客户端依赖] → 行为是放宽(原先 409 的跨 provider 创建现 201),无破坏;changelog 注明。
- [provider 与 source 漂移(手改库)] → guard 测试断言映射一致性;漂移行解析按 provider 走(不崩,警告)。
- [双索引在并发创建下的竞争] → 唯一性仲裁与今日一致落在 API 层(select-then-insert + 409),索引提供同构约束与软删复用语义;并发窗口与现状相同,不放大。
- [`canonical_hash` 搬家破坏 1.3.20 引用] → 原地 re-export + 既有 agent versioning 测试兜底。

## Migration Plan

1. Alembic 迁移:加列 → 回填 → 建 `idx_skills_ws_name_provider`(unique)+ partial unique(provider IS NULL)→ 删旧 `idx_skills_workspace_name`。
2. 部署顺序:先迁移后发代码(旧代码不识别新列,兼容;新代码要求列存在)。
3. 回滚:代码回滚无前置;迁移降级按 D7 前置合并重复后重建旧索引。

## Open Questions

- plugin 包 tier 枚举 → skill `trust_tier` 的精确映射表(tasks 阶段核对 5.5c/5.5d 实际存储值后定,不影响 spec)。
- `user_invocable=false` 在 studio 显式入口的具体裁剪位置(随最小 UI 定)。
