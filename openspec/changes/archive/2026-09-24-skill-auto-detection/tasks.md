# Tasks

## 1. 数据与配置基座

- [x] 1.1 `src/hecate/core/config.py` 新增 `SKILL_DISCOVERY_ENABLED: bool = False`；确认与 `SKILL_PROGRESSIVE_DISCLOSURE` 注释风格一致。验证：`python -c "from hecate.core.config import settings; print(settings.SKILL_DISCOVERY_ENABLED)"` 输出 `False`
- [x] 1.2 `src/hecate/models/agent.py` 的 `AgentModel` 新增 `skill_discovery_enabled: Mapped[bool | None]`（nullable Boolean，默认 None），同步更新 Pydantic agent schema（`AgentCreateSchema`/`AgentUpdateSchema`/read schema）暴露三态字段；`src/hecate/models/evolution.py` 的 `SkillUsageEventModel` 新增 `detected_via: Mapped[str | None]`（String(20)）。验证：模型可导入、mypy 无错
- [x] 1.3 新增 alembic 迁移（单个 revision）：`agents.skill_discovery_enabled` + `skill_usage_events.detected_via` 两列。验证：`alembic upgrade head` 在本地 PostgreSQL 通过，`alembic downgrade -1` 可回退
- [x] 1.4 workspace 策略读取助手：从 `WorkspaceModel.settings` JSON 解析 `skill_discovery.enabled` 与 `skill_discovery.min_trust_tier`（默认 `community`）的小工具函数（放 `src/hecate/tools/skill/` 下，与 loader 同域），非法 tier 值回退默认并告警。验证：单测覆盖 缺键/合法值/非法值 三种输入

## 2. SkillLoader 发现池与预算排序

- [x] 2.1 `SkillLoader` 内部惰性解析 discovery 上下文（agent 三态值 + workspace 策略 + 全局 settings），缓存于实例；全局关闭时不发出 workspace 查询。验证：单测断言全局关闭时 settings 访问短路（无 workspace 查询）
- [x] 2.2 新增发现池查询方法：workspace + 零 UUID、未删、`model_invocable=true`、`provider != 'user'`、plugin 启用条件（复用 `_plugin_enabled_condition`），经 `resolve_precedence_map` 归并后应用 trust 下限过滤，并排除已绑定/auto_load 名称。验证：单测覆盖 user 排除、model_invocable 排除、plugin 禁用排除、同名归并、bundled 跨 workspace 可见
- [x] 2.3 `format_skills` 合并三来源（绑定 + auto_load + 发现池），catalog 预算默认值 1000 → 2000（`DEFAULT_CATALOG_TOKEN_BUDGET`），实现选择策略：绑定/auto_load 恒优先 → trust tier → provider rank → 使用次数降序（`skill_loaded` 事件 COUNT，排除 `catalog_served`）→ 名称字典序。验证：单测覆盖溢出裁剪顺序、trust 优先于 usage、同名次序确定性、budget 未溢出时顺序保持声明序
- [x] 2.4 `load_skill_content` advertised 集合放宽：discovery 生效时 advertised = 绑定 ∪ auto_load ∪ 发现池；`detected_via` 按来源写 `bound` / `auto_detected`；发现条目加载时对 `requires` 做存在性检查，缺失则记录 `dependency_warning` 事件（含缺失依赖名）后照常服务。验证：单测覆盖 opt-out 后拒绝、发现条目成功加载并标记 `auto_detected`、缺依赖告警且内容照常返回、`requires` 全满足时无告警
- [x] 2.5 usage 事件写入路径补充 `detected_via`（`catalog_served` 与 `skill_loaded` 均带），旧行为 NULL 兼容。验证：单测断言两条事件类型的 `detected_via` 值

## 3. Promote API

- [x] 3.1 `src/hecate/studio/api/agents.py` 新增 `POST /agents/{agent_id}/skills/promote`：解析校验（workspace/bundled 可解析、未删、`model_invocable=true`、plugin 启用），失败 404 不动 agent；成功幂等 append 并返回 `{skills, frozen: false, next_step}`。验证：API 单测覆盖 成功 append / 幂等 / 404 不存在 / 404 model_invocable=false
- [x] 3.2 既有 add/remove association 端点回归确认未变（盲目 append 语义保留）。验证：既有测试全绿

## 4. 集成与回归

- [x] 4.1 runtime 与 studio 两条执行路径的集成断言：discovery 开启时系统提示含发现条目、关闭时与既有快照一致（`tests/test_runtime` 既有 skill 注入测试不修改即通过）。验证：`python -m pytest tests/test_runtime -q "skill" -q` 全绿
- [x] 4.2 全量验证：`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/ -q` 四项全绿
  - 注：ruff/format/mypy 三项 0 错误；pytest 5499 passed / 11 failed——11 个失败经 git stash 基线对照证实为既有 Windows 平台问题（workspace boundary 路径分隔符、symlink 权限、shell hook 退出码、litellm import 扫描），干净基线同样失败，与本 change 无关
- [x] 4.3 文档更新：skill 概念页补"绑定 = 冻结面 / 发现 = 活面"框架与三层开关说明；`docs/features/feature-catalog.md` 5.9c 行更新状态与叙事。验证：文档 diff 评审通过
- [x] 4.4 Deferred 项落册：将五项 deferred（`search_skills` 长尾词法检索工具、description 触发质量 lint 与评估线束、skill 上下文成本与使用率观测、suggest-binding 闭环、`user` provider 成员级隔离）写入 `docs/features/feature-catalog.md` 与 `docs/features/roadmap.md`——search_skills 建议铸新 ID `5.9f`（延续 5.9 字母后缀序列），其余四项按粒度记为 5.9c 行内 deferred 注记或 P5 独立行（成员级隔离归安全线）；archive 阶段按 AGENTS.md 惯例随 `docs/design/positioning.md` 复查一并定稿。验证：两个文档中五项 deferred 均可检索到且 ID/去向无歧义
