# Proposal: 5.9-enh Skill Provider Registry

## Why

Skill 资产(`SkillModel`)目前只有单一的 `(workspace_id, name)` 唯一约束与两级解析域(workspace + 零 UUID 系统域),没有来源分级、没有优先级仲裁(系统域与 workspace 域同名时胜者未定义)、没有显式的调用权控制(模型自动调用 vs 用户显式调用混在 `auto_load`/catalog 的隐式行为里)。这与 5.9-enh(Skill Provider Registry)的目标——provider 来源(project/user/bundled)+ rank 优先级 + kebab-case 语法 + 调用策略分离——差距集中在解析语义与治理字段上:来源分层与高优先级遮蔽、显式的 model/user 调用策略双开关、trust-tier 防提权,三项均为空白。本 change 是路线图既定项(feature-catalog 的 5.9-enh 行,Subsumes 5.12 Agent Skills Standard),也是 5.9 三件套(5.9-enh → 5.9d → 5.9e)的第一步,为后续 skill 版本化提供稳定的身份层。

## What Changes

- **来源(provider)体系**:为 skill 引入显式的 provider 分类与优先级——`bundled`(内置,零 UUID 域)、`user`、`project`(workspace 共享),外部 registry 来源(`custom`)仅预留枚举与扩展点,连接器不在本期范围。新字段命名为 `provider`,避免与现有 `origin` 列(插件包来源 provenance)冲突;现有 `source` 字段语义保持兼容(`system` 映射为 `bundled`,`plugin` 来源 `provider` 置空、不参与本期 rank 竞争)。
- **同名共存与 rank 解析**:**迁移唯一索引**,允许同 workspace 内同名 skill 以不同 provider 共存;`SkillLoader` 解析改为有序仲裁——project > user > bundled(企业多租户:管理员可预期,个人不遮蔽团队资产),同名遮蔽语义取代今日的未定义胜者。
- **调用策略双开关**:新增 `model_invocable` / `user_invocable` 两个布尔字段(默认双 true,现状兼容):`model_invocable=false` 的 skill 从 L1 catalog 与 `load_skill` 工具面隐藏,仅保留用户显式入口;`user_invocable=false` 的 skill 不注册用户显式调用入口。`auto_load=true` 与 `model_invocable=false` 组合在创建/更新时校验拒绝。
- **信任与完整性字段**:新增 `trust_tier` 枚举(official / trusted / community)与 `content_hash`(导入/发布时计算的内容摘要)。防提权规则写入 spec:未知来源的 tier 上限为 community,workspace 管理员不可将任意来源自授 official。真扫描(`scan_result` 实装)仍归 5.13a,本期只落字段与占位。
- **统一 kebab-case 语法**:名称校验规则(`^[a-z][a-z0-9-]*$`)从 Create schema 提升为 registry 级规范,覆盖导入与未来来源。
- **范围边界**:plugin 来源 skill 的撞名拒绝语义不变(ingestion spec 无 delta);plugin skill 命名空间化(derived qualified name)仅做显示层,不改存储名;不引入外部 registry 连接器;不做版本化(5.9d)。

## Capabilities

### New Capabilities

- `skill-provider-registry`:skill 来源分类、rank 优先级与同名遮蔽解析规则、调用策略双开关(`model_invocable`/`user_invocable`)、`trust_tier` 与防提权规则、`content_hash` 完整性锚定、统一 kebab-case 名称语法。

### Modified Capabilities

- `skill-loader`:按名解析需求改为"经 provider registry 的有序解析"——同名时按 rank(project > user > bundled)遮蔽,系统域与 workspace 域同名时 bundled 永远输(修复今日未定义胜者);L1 catalog 与 L2 加载遵循调用策略开关(`model_invocable=false` 不进模型可见面)。
- `skill-api`:创建/更新接口新增调用策略与 `trust_tier` 字段及校验(`auto_load=true` + `model_invocable=false` 拒绝);同名冲突语义从"同 workspace 同名即 409"改为"同 workspace 同名**且同 provider** 才 409,跨 provider 同名允许创建";读接口响应暴露 `provider`/`trust_tier`/`content_hash`/调用策略字段。

## Impact

- **数据层**:`SkillModel` 新增 `provider`/`model_invocable`/`user_invocable`/`trust_tier`/`content_hash` 列;`(workspace_id, name, deleted, deleted_at)` 唯一索引迁移为含 provider 维度的共存约束;Alembic 迁移需处理存量数据回填(存量行按现有 `source` 映射 provider,零 UUID 行归 bundled)。
- **服务层**:`SkillLoader`(`src/hecate/tools/skill/loader.py`)解析查询与仲裁逻辑改造(热路径,注意预算/进度披露行为不变);新增 registry 解析服务模块。
- **API 层**:`src/hecate/tools/api/skills.py` 创建/更新/列表/详情的 schema 与校验;409 语义变化对现有客户端是**行为放宽**(原先拒绝的跨 origin 同名创建现允许),无破坏性移除。
- **studio**:skill 列表/编辑页展示 origin、trust_tier、调用策略开关(最小 UI,照 1.3.20 先例)。
- **不受影响**:`agent-plugins-ingestion`(撞名拒绝保留)、`plugin-content-scanning`、`unified-skill-registry`(SkillRef 异构解析)、`skill-attribution`/`skill-evolution-*`。
