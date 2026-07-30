## 1. 数据模型迁移

- [x] 1.1 在 `src/hecate/models/skill.py` 的 `SkillModel` 中添加 `workspace_id` 列 — UUID，默认 `00000000-0000-0000-0000-000000000000`
- [x] 1.2 将 `idx_skills_name` 唯一索引更改为复合索引 `(workspace_id, name)`，带 `postgresql_where=deleted_at.is_(None)`
- [x] 1.3 更新 `SkillCreateSchema`，从请求体中排除 `workspace_id`（从认证上下文设置）
- [x] 1.4 更新 `SkillReadSchema` 以包含 `workspace_id`
- [x] 1.5 创建 Alembic 迁移：添加默认零 UUID 的列，删除旧索引，创建新的复合索引

## 2. SkillLoader 服务

- [x] 2.1 创建 `src/hecate/services/skill/__init__.py`（空包标记）
- [x] 2.2 在 `src/hecate/services/skill/loader.py` 中创建 `SkillLoader` 类，接受 `db: AsyncSession`
- [x] 2.3 实现 `format_skills(agent_id: UUID, workspace_id: UUID) -> str` — 按 ID 查询 agent，读取 `skills` 列表，按名称 + 工作空间加载 `SkillModel` 记录，格式化为 XML `<skills>` 块
- [x] 2.4 优雅处理缺失技能 — 记录警告，跳过，继续处理剩余技能
- [x] 2.5 实现 `auto_load` 包含 — 查询工作空间中所有 `auto_load=True` 的技能，与 agent 的显式技能合并，按名称去重
- [x] 2.6 实现 `max_tokens` 截断 — 估算 token 数（len/4），将单个技能截断到其 `max_tokens`，如果总内容超过 4000 token 预算，则丢弃最低优先级的技能
- [x] 2.7 格式化输出：每个技能为 `<skill name="...">description\n\ninstructions</skill>`，包裹在 `<skills>...</skills>` 标签中

## 3. 将技能加载接入执行流程

- [x] 3.1 修改 `WorkflowExecutionService.execute()` — 当提供 `agent_id` 时，按 ID 加载 agent，调用 `SkillLoader.format_skills()`，构造 `system_prompt = persona + skills_block`
- [x] 3.2 修改 `AgentExecutionPort.agent_execute()` — 通过 `SkillLoader` 加载技能，附加到系统消息的 persona 之后
- [x] 3.3 更新 `src/hecate/api/v1/chat.py` 中的 `_process_chat()` — 当使用带有 agent_id 的增强路径时，将 agent_id 传递给 `execute()` 以便加载技能
- [x] 3.4 处理 `persona=None` 回退 — 当未设置 persona 时，使用 "You are a helpful assistant." 作为基础

## 4. 技能 CRUD API

- [x] 4.1 添加 `POST /api/skills` 端点 — 使用来自认证上下文的 `workspace_id` 创建技能
- [x] 4.2 添加 `PUT /api/skills/{id}` 端点 — 更新技能字段，验证工作空间所有权
- [x] 4.3 添加 `DELETE /api/skills/{id}` 端点 — 软删除，验证工作空间所有权
- [x] 4.4 更新 `GET /api/skills` — 按认证上下文的 `workspace_id` 过滤，同时包含系统技能（`workspace_id=00000000`）
- [x] 4.5 添加工作空间所有权检查辅助方法 — 拒绝对其他工作空间技能的操作

## 5. SKILL.md 导入 API

- [x] 5.1 创建 `src/hecate/services/skill/parser.py`，包含 `parse_skill_md(content: str) -> dict` — 提取 `---` 分隔符之间的 YAML 前置元数据，使用 `yaml.safe_load()` 解析，剩余文本作为指令
- [x] 5.2 验证解析的前置元数据 — 要求 `name` 和 `description`，对名称格式应用 SkillCreateSchema 验证规则（`^[a-z][a-z0-9-]*$`）
- [x] 5.3 添加 `POST /api/skills/import` 端点 — 接受 `UploadFile`，使用 `parse_skill_md()` 解析，创建 `source="user"` 的 SkillModel，返回 201
- [x] 5.4 处理边界情况：文件过大（限制 100KB）、无效 YAML、缺少分隔符、非 UTF-8 编码

## 6. Agent-技能关联 API

- [x] 6.1 添加 `POST /api/agents/{id}/skills` 端点 — 接受 `{"skill_name": "..."}`，追加到 agent 的 `skills` 列表（幂等，无重复）
- [x] 6.2 添加 `DELETE /api/agents/{id}/skills/{skill_name}` 端点 — 从 agent 的 `skills` 列表中移除技能名称（幂等）
- [x] 6.3 在添加关联前验证技能存在于工作空间中 — 如果技能名称未找到，返回 404

## 7. 测试

- [x] 7.1 创建 `tests/test_services/test_skill/__init__.py`
- [x] 7.2 创建 `tests/test_services/test_skill/test_loader.py` — 测试 format_skills：有技能、无技能、缺失技能、auto_load、token 截断、XML 格式
- [x] 7.3 创建 `tests/test_services/test_skill/test_parser.py` — 测试 parse_skill_md：有效 SKILL.md、缺失前置元数据、无效 YAML、缺少必填字段
- [x] 7.4 创建 `tests/test_api/test_skills.py` — 测试 POST/PUT/DELETE/import 端点、工作空间隔离、重复名称、agent-技能关联
- [x] 7.5 测试工作空间隔离 — 验证工作空间 A 的技能对工作空间 B 不可见
- [x] 7.6 运行 `python -m pytest tests/ -q` — 全部通过，无回归

## 8. 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/`
- [x] 8.2 运行 `ruff format --check src/ tests/`
- [x] 8.3 运行 `mypy src/`
- [x] 8.4 运行 `python -m pytest tests/ -q` — 无回归
