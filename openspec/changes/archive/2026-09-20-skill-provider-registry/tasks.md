# Tasks

## 1. 共享基础设施

- [x] 1.1 将 `canonical_hash` 从 `src/hecate/studio/agents/versioning.py` 提升到共享层(`src/hecate/core/`),versioning 原地 re-export 保持兼容;验证:既有 agent versioning 测试全绿(`python -m pytest tests/ -k versioning -q`)
- [x] 1.2 新建 `src/hecate/tools/skill/provider_registry.py`:provider 枚举(bundled/user/project/custom-reserved)、source→provider 映射表、rank 常量(project=0 > user=1 > bundled=2)、纯函数 `resolve_by_precedence(rows)`;验证:乱序输入确定性仲裁、跨 workspace 隔离不受影响的单测通过

## 2. 数据模型与迁移

- [x] 2.1 `SkillModel` 新增列 `provider`(nullable,String)、`trust_tier`(default community)、`model_invocable`/`user_invocable`(default true)、`content_hash`(nullable String 64),并同步 `SkillCreateSchema`/`SkillUpdateSchema`/`SkillReadSchema`;验证:模型层单测断言默认值与字段映射
- [x] 2.2 Alembic 迁移:加列 → 回填(provider 按 source 映射、bundled→official、双开关 true、content_hash 用共享 `canonical_hash` 现算)→ 建唯一索引 `(workspace_id, name, provider, deleted, deleted_at)` 与 partial unique `(workspace_id, name, deleted, deleted_at) WHERE provider IS NULL` → 删旧 `idx_skills_workspace_name`;验证:`alembic upgrade head` 在空库与含同名跨域存量的 fixture 库上通过,downgrade 脚本含重复合并前置
- [x] 2.3 guard 测试:全表 provider↔source 映射一致性、plugin 行 provider 为 NULL、bundled 行 trust_tier=official;验证:`python -m pytest tests/ -k skill_provider -q` 全绿

## 3. API 层(skill-api delta)

- [x] 3.1 create 路径:接受 `model_invocable`/`user_invocable`(默认 true)、拒绝 `custom` provider 赋值、拒绝 `auto_load=true ∧ model_invocable=false`(422)、409 语义改为同 provider 同名才拒、响应携带 `provider`/`trust_tier`/`content_hash`/双开关;验证:对照 specs/skill-api delta 逐场景的 API 测试
- [x] 3.2 update 路径:双开关可改(含 auto_load 一致性校验)、`provider`/`trust_tier` 只读(422);验证:对应 API 测试
- [x] 3.3 import 路径:计算 `content_hash`(字段集与 ref_manifest 对齐)、跨 provider 同名放行、同 provider 409;验证:对应 API 测试
- [x] 3.4 list/detail 响应暴露 `provider`/`trust_tier`/`model_invocable`/`user_invocable`/`content_hash`;验证:对应 API 测试

## 4. Loader 解析改造(skill-loader delta)

- [x] 4.1 `_load_skills_by_names` 改为一次查询同名全部 provider 候选并经 `resolve_by_precedence` 仲裁;`_query_auto_load_skills` 候选集合同样按名字仲裁;验证:project>user>bundled 阴蔽、bundled 兜底、auto_load 同名仲裁的 loader 单测
- [x] 4.2 `model_invocable=false` 过滤出 L1 catalog,`load_skill_content` 对其拒绝(错误注明 model-invocable 原因);验证:对应 loader 单测
- [x] 4.3 既有行为回归:token 预算、progressive disclosure、plugin 启用位门控、auto_load 全注入、usage 事件记录;验证:既有 loader 测试全绿(`python -m pytest tests/ -k skill_loader -q`)

## 5. Studio 前端(最小)

- [x] 5.1 摸底 skill 管理 UI 落点(`web/src/components/agent/skill-selector.tsx` 及可能的管理页;当前无独立 skills 路由);验证:落点结论——项目无独立 skills 管理页,唯一 skill 前端为 agent 编辑页的 SkillSelector
- [x] 5.2 在落点处展示 provider/trust_tier 徽标(决策:仅 SkillSelector 选项展示 `[provider · tier]` 前缀;`model_invocable`/`user_invocable` 开关延后到独立 UI change,本期经 API 管理并双重校验);验证:`cd web && npm run build` 通过

## 6. 配置与收尾

- [x] 6.1 settings 占位 `SKILL_TRUSTED_SOURCE_PATTERNS`(默认空 = 仅 bundled 为 official);验证:settings 默认值测试
- [x] 6.2 按 design 复核 plugin ingestion 行为不变(撞名拒绝、门控);验证:既有 agent-plugins-ingestion 测试全绿
- [x] 6.3 全量验证四件套:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q`;验证:全部 0 errors
- [x] 6.4 视需要在 `docs/gotchas.md` 记录 provider NULL 语义与双索引约束;验证:文档评审通过(catalog/positioning 更新留给 /opsx-archive)
