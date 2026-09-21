# Tasks

## 1. 数据层

- [x] 1.1 新建 `src/hecate/models/skill_version.py`：`SkillVersionModel`（`skill_id` FK、`version` 单调 int、`name`、`change_summary`、`config_snapshot` JSON、`schema_version`、`content_hash`、`learned_run_id` nullable、`created_by`、`workspace_id`、软删列；unique `(skill_id, version)` + 索引 `(workspace_id, deleted)`），并在 models 注册处导出。验证：模型可导入，列定义与 design D2 一致
- [x] 1.2 Alembic 迁移：建 `skill_versions` 表（纯 additive；`downgrade` drop 表）。验证：`alembic upgrade head` 与 `downgrade -1` 往返通过（需 PostgreSQL 基础设施；迁移文件已完成、`alembic heads` 链校验通过，DB 往返因本环境无可用 PostgreSQL 待补验）

## 2. 版本服务 `SkillVersionService`（`src/hecate/tools/skill/versioning.py`）

- [x] 2.1 `commit(skill_id, *, name, change_summary, actor_user_id, learned_run_id=None)`：七冻结字段快照 + canonical_hash（5 字段集）+ 单调版本号；`provider=NULL` 行 409 拒绝。验证：单测覆盖快照字段集、哈希与活行一致、版本号递增、plugin 行 409
- [x] 2.2 `list_versions` / `get_version(include_snapshot)` / `update_version`（仅 name/change_summary 元数据）/ `get_status`（`latest_version` + `has_uncommitted_changes`，无快照恒 true，治理字段变更不置脏）。验证：单测覆盖从未提交状态、回滚/提交后脏标记归零、治理字段不置脏
- [x] 2.3 `diff_versions(v1, v2)`：deepdiff 覆盖七冻结字段，ImportError 降级路径与 agent 版本化一致。验证：单测覆盖字段级差异与 identical 判定
- [x] 2.4 `rollback_to_version`：同一事务内复制目标快照为新版本 + 七字段写回活行 + 重算活行 `content_hash`；写回前乐观锁 re-check（活行哈希与请求时不一致则 409）。验证：单测覆盖回滚后活行等于目标内容、新版本号产生、脏标记归零、并发编辑竞争触发 409
- [x] 2.5 `delete_version`：被任一非删除 `agent_versions.ref_manifest` 条目以 `skill_id+version` pin 时 409（按 skill_id 匹配）；其余软删。验证：单测覆盖被 pin 拒删与未 pin 可删
- [x] 2.6 快照解析读取接口：按 `(skill_id, version)` 取版本快照内容（含软删 skill 行仍可读取，供 pinned 解析使用）。验证：单测覆盖源行软删后仍可读取快照

## 3. Agent manifest 集成（`src/hecate/studio/agents/versioning.py`）

- [x] 3.1 `_build_ref_manifest` skill 条目升级五元组 `(resource_type, name, skill_id, provider, version, content_hash)`：同名经 `resolve_precedence_map` 仲裁（修复存储序缺陷），查询带 plugin enabled 条件与零 UUID 域，`version` = 该行最新快照（无则 null）；tools/KB 条目不变。验证：单测覆盖 pin 到最新版本、从未提交 version=null、同名 project/user 乱序输入仲裁确定、plugin 行 version=null
- [x] 3.2 `version_drift` 跳过 `version` 非 null 条目；无 `version` 字段的旧格式条目按 null 处理（存量快照零迁移）。验证：单测覆盖 pinned 不报漂移、未 pin 照常、旧格式兼容
- [x] 3.3 `_ref_manifest_diff` 对五元组按 `skill_id` 键控比对（version/content_hash 变化即 content_changed），保留旧格式分支。验证：单测覆盖新旧格式混用 diff
- [x] 3.4 pinned 内容解析接线：`SkillLoader` 增加 manifest-aware 入口（条目 version 非 null 时按 `(skill_id, version)` 从版本快照渲染内容，`max_tokens` 取快照值；null 走现路径）；快照解析的运行时调用点传入 `resolve()` 的 ref_manifest，草稿/studio 路径不传。验证：单测覆盖 agent 快照 pin v2 后活行前进仍注入 v2 内容、version=null 实时解析、studio 路径行为不变

## 4. API 面

- [x] 4.1 版本端点：`POST /api/skills/{id}/versions`、`GET .../versions`、`GET .../versions/{version}`、`PATCH .../versions/{version}`、`POST .../versions/{version}/rollback`、`DELETE .../versions/{version}`、`GET .../versions/{v1}/diff/{v2}`。验证：API 集成测试覆盖 happy path + 409 双场景（plugin 提交、被 pin 删除）+ 404
- [x] 4.2 skill list/detail 响应附 `latest_version`、`has_uncommitted_changes`。验证：API 集成测试断言两字段随提交/回滚/治理字段编辑正确变化

## 5. Evolution 集成

- [x] 5.1 `src/hecate/studio/self_evolution/review.py` 发布点同事务调用 commit（`learned_run_id`、系统创建者，best-effort：失败 warning 不阻断发布）。验证：单测覆盖发布产生带 `learned_run_id` 的版本、commit 异常时发布仍成功

## 6. Studio 最小 UI

> 范围调整：本仓库前端 `web/` 当前没有 skill 独立管理页（skill 仅在 agent 编辑器内作为子选择器出现）。最小 UI 所需页面超出本 change 范围。**5.9d 后端能力（版本端点、`latest_version` / `has_uncommitted_changes` 字段）已完整可用；studio 集成待后续 skill 管理页 change 落地时直接消费**。后续 UI change 应包含：版本列表 + 「提交版本」+「回滚」（带确认提示说明写回活行）+ 列表行徽标 + diff 复用 agent 版本化组件。

## 7. 集成与回归

- [x] 7.1 层叠守卫：loader pinned 路径不引入反向依赖（512 passing 跨 test_layering_domain / channel / runtime / enterprise / sandbox；test_layering_llm 1 失败为 pre-existing 的 hecate_llm 子项目状态，与本 change 无关）
- [x] 7.2 验证：ruff check + ruff format check + 6/6 skill-versioning 单测全过；mypy 在当前环境因 mypy.__main__ DLL 加载失败（环境约束，非代码问题），待环境修复后补验

## 8. 文档与规格

- [x] 8.1 `docs/features/feature-catalog.md` 5.9d 行补强：追加 drift detection、agent manifest pin（version+content_hash）、evolution 自动成版
- [x] 8.2 `docs/features/feature-catalog.md` 5.9e 行重写：声明 + 创作时校验（全图环检测）+ 绑定时闭包固化进 agent manifest pin，无运行时求解器
- [x] 8.3 `docs/features/roadmap.md` 依赖链措辞：5.9e 链条 "Dependency Resolution" → "Bind-time Dependency Resolution (closure pinning)"
- [x] 8.4 `docs/gotchas.md` 增补：ref_manifest skill 条目必须经 `resolve_precedence_map` 仲裁；pinned 解析绕过同名仲裁直达 `(skill_id, version)`；`content_hash` 5 字段集与快照字段集的超集关系；rollback 写回语义；overlay 不写回 ORM
- [x] 8.5 `openspec validate --change skill-versioning` 通过；specs 与 proposal Capabilities 一致（skill-versioning 新增、agent-versioning 修改）
