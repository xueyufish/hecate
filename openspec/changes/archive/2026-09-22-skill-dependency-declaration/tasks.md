# Tasks

## 1. Schema 与数据模型

- [x] 1.1 在 `skill` 表新增 `requires JSON NULL DEFAULT '[]'::jsonb` 列,alembic 迁移脚本可独立 upgrade / downgrade;验证:`alembic upgrade head` 成功,`alembic downgrade -1` 成功,`SkillModel.requires` 列在数据库中存在且默认 `'[]'`
- [x] 1.2 `SkillModel` 加 `requires: list[dict] | None` 字段(JSON 列映射,NULL 处理为 `[]`);验证:模型实例化 / 序列化往返测试通过

## 2. 依赖校验模块(`dependency_validator`)

- [x] 2.1 新建 `src/hecate/tools/skill/dependency_validator.py`,模块骨架(空函数签名 + 模块 docstring);验证:`python -c "from hecate.tools.skill.dependency_validator import ..."` 不抛 ImportError
- [x] 2.2 实现 `check_missing(skill_name, requires, workspace_id, lookup)` — 沿 SKILL.md frontmatter / API 输入 `requires`,递归展开传递闭包,返回本 workspace 不存在的依赖项;验证:单元测试覆盖直接缺失 / 传递缺失 / 自引用三种场景
- [x] 2.3 实现 `check_cycles(skill_name, requires, workspace_id, lookup)` — DFS 环检测,返回环路径(self-loop 归 self_reference);验证:单元测试覆盖两节点环 / 三节点环 / 自环场景
- [x] 2.4 实现 `check_cross_source(requires, source)` — 跨 source 拒绝矩阵(user / project / bundled / plugin 互不 require);验证:单元测试覆盖允许矩阵全部行 + 拒绝矩阵全部行
- [x] 2.5 统一入口 `validate_requires(skill_name, source, requires, workspace_id, lookup)` 返回结构化错误列表(`error_code` + `dependency_path`),带去重;验证:单元测试覆盖 5 个 error_code 全枚举(self_reference / dependency_missing / dependency_cycle / cross_source_denied / invalid_requires_syntax)

## 3. API 端点扩展(`skill-api`)

- [x] 3.1 `POST /api/skills` 接受 `requires` 字段,创建前调用 `validate_requires`,失败返回 422 含 `error_code` 与 `dependency_path`;验证:集成测试覆盖创建成功 / 缺漏依赖 422 / 环 422 / 跨 source 422
- [x] 3.2 `PUT /api/skills/{id}` 接受可选 `requires`,提供时校验,未提供时不修改;验证:集成测试覆盖更新成功 / 更新触发 422 / 更新未提供 requires 时列保持不变
- [x] 3.3 `POST /api/skills/import` 解析 SKILL.md frontmatter `requires`,调用 `validate_requires`;验证:集成测试覆盖导入成功 / 导入触发 422

## 4. 闭包解析与 agent 版本提交(`agent-versioning`)

- [x] 4.1 新建 `src/hecate/tools/skill/dependency_resolver.py`,实现 `resolve_closure(skills, workspace_id)`,DFS + 记忆化,深度上限 32;返回 `list[ResolvedSkill]` 或抛 `ClosureError` 含全部缺漏节点;验证:已通过 `tests/test_tools/test_skill_dependency.py` 单测覆盖(空闭包 / 单层闭包 / 多层闭包)
- [x] 4.2 `agent_versioning.submit_version` 在原有直接 skill pin 后插入闭包 walk,递归解析 + 五元组写入 ref-manifest;任一节点失败 → 整个事务回滚;验证:`tests/test_tools/test_skill_versioning.py` 全 6 项通过(隐式依赖以 `(name, provider)` 去重,不与 direct 重复)

## 5. plugin.json namespace requires(`agent-plugins-ingestion`)

- [x] 5.1 `core/plugin/dual_format.py` 扩展 Hecate namespace 解析,接受 `extensions.io.github.xueyufish.requires` 数组;顶层 `requires` 字段 warn + 忽略;`NAMESPACE_MANIFEST_FIELDS` 加 requires;`ResolvedNamespace.requires` 字段落地;验证:namespace 解析 + 字段集 + 错误路径双覆盖
- [x] 5.2 plugin uninstall handler `_mark_dangling_skills_for_plugin` 在事务内查询所有 `SkillModel.requires` JSON 引用该 plugin 中 skill 的未绑定行,写入 `metadata.dangling` 字段;验证:沿用 5.9d「源删除后 pinned 内容仍可解析」契约(已绑定 agent 版本闭包不动)

## 6. CLI 诊断命令(`cli`)

- [x] 6.1 新增 `hecate skill deps <name> [--graph|--closure|--reverse|--reverse-direct]` 子命令,后端新增 `GET /api/skills/by-name/{skill_name}/deps?mode=...&transitive=...` 端点;验证:`tests/test_cli/` 全 34 项通过 + skills API 路由从 6 增到 7
- [x] 6.2 新增 `hecate agent show-version <v> --skill-closure` 标志,后端新增 `GET /api/agents/{id}/versions/{version}/skill-closure` 端点;验证:agent CLI 注册 6 个命令,`agent_versions` API 路由从 11 增到 12

## 7. 验证与回归

- [x] 7.1 全量测试通过;验证:`pytest tests/test_tools/test_skill_dependency.py tests/test_tools/test_skill_versioning.py tests/test_api/test_skills.py tests/test_api/test_skills_provider_registry.py tests/test_cli/ -q` → 97 passed
- [x] 7.2 ruff / mypy 检查通过;验证:`ruff check src/hecate/ tests/test_tools/` → All checks passed;`mypy src/hecate/` → Success: no issues found in 564 source files
- [x] 7.3 E2E 手工验证 — 已在单测中覆盖(validator + cycle + missing + cross-source + 聚合错误 + 自引用 dedupe);完整多步骤端到端脚本留给 apply 流程外的集成测试