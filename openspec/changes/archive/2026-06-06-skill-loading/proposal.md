## Why — 动机

Feature 5.9 是最后一个 P1 缺口（18/19 已完成）。SkillModel 和只读 API 已经存在，但技能从未被加载或注入到 agent 执行中。`AgentModel.skills` 字段存储一个空列表，没有代码读取它。如果没有这一变更，agent 无法通过可重用的指令集进行扩展——这是技能系统的核心价值主张。

## What Changes — 变更内容

- 为 `SkillModel` 添加 `workspace_id` 以实现多租户隔离（当前缺失；所有其他资源模型都有该字段）。将唯一索引从 `(name)` 更改为 `(workspace_id, name)`。系统技能使用零 UUID。
- 创建 `SkillLoader` 服务，从数据库加载 agent 技能名称对应的指令，格式化为 XML 标签上下文块，并处理 `auto_load` 和 `max_tokens` 预算。
- 将技能加载接入 `WorkflowExecutionService.execute()` 和 `AgentExecutionPort.agent_execute()`，以 XML 格式 `<skills><skill name="...">body</skill></skills>` 将激活的技能指令注入系统提示词。
- 添加 CRUD 端点：`POST /api/skills`、`PUT /api/skills/{id}`、`DELETE /api/skills/{id}`。
- 添加 `POST /api/skills/import` 端点，解析 SKILL.md 文件（YAML 前置元数据 + Markdown 正文）并创建 SkillModel 记录。
- 添加 `POST /api/agents/{id}/skills` 和 `DELETE /api/agents/{id}/skills/{skill_name}` 端点来管理 agent-技能关联。

## Capabilities — 能力变更

### 新增能力
- `skill-loader`: 技能加载服务 — 将 agent 技能名称解析为指令、格式化上下文、处理 auto_load 和 token 预算
- `skill-api`: 完整的技能 CRUD + 导入 API — 创建、读取、更新、删除技能；导入 SKILL.md 文件

### 修改的能力
- `engine-ports`: 修改了 `context_assemble()` 和执行流程以将技能指令注入系统提示词
- `data-models`: SkillModel 新增 `workspace_id` 字段并更新了唯一索引

## Impact — 影响范围

- **Models**: `src/hecate/models/skill.py` — 添加 `workspace_id`，更改索引，更新 schemas
- **新服务**: `src/hecate/services/skill/loader.py` — SkillLoader 类
- **Services**: `src/hecate/services/workflow/execution_service.py` — 在 `execute()` 中加载技能
- **Services**: `src/hecate/services/orchestration/agent_execution_port.py` — 为子 agent 加载技能
- **API**: `src/hecate/api/management/skills.py` — 添加 POST、PUT、DELETE、import 端点
- **API**: `src/hecate/api/management/agents.py` — 添加技能关联端点
- **测试**: `tests/test_services/test_skill/`、`tests/test_api/test_skills.py`
